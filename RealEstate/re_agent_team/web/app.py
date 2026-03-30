"""FastAPI Webアプリケーション - 地図UI + API"""
import sys
import re
import json
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agents.orchestrator_agent import OrchestratorAgent
from agents.land_price_agent import LandPriceAgent
from agents.rental_agent import RentalAgent
from agents.scraper_agent import ScraperAgent
from agents.url_scraper_agent import UrlScraperAgent
from agents.land_scraper_agent import LandScraperAgent
from agents.plan_agent import PlanAgent
from agents.asset_score_agent import AssetScoreAgent
from agents.maisoku_agent import MaisokuAgent
from engine.map_data_builder import MapDataBuilder
from engine.batch_processor import BatchProcessor
from engine.area_analyzer import AreaAnalyzer
from models.property import Property
from models.land_listing import LandListing
from storage.report_store import ReportStore
from storage.database import Database
from data.city_master import CITY_MASTER, CITY_NAME_MAP
from data.station_master import STATIONS, get_stations_by_prefecture
from config.settings import (
    MAP_DEFAULT_CENTER,
    MAP_DEFAULT_ZOOM,
    WEB_HOST,
    WEB_PORT,
    DATA_DIR,
    REINFOLIB_API_KEY,
    HAZARD_TILE_URLS,
    ORS_API_KEY,
    ORS_API_BASE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(title="不動産投資判定システム", version="2.0.0")

# Static files & templates
STATIC_DIR = Path(__file__).parent / "static"
TEMPLATE_DIR = Path(__file__).parent / "templates"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Agents
orchestrator = OrchestratorAgent()
land_agent = LandPriceAgent()
rental_agent = RentalAgent()
scraper_agent = ScraperAgent()
url_scraper = UrlScraperAgent()
land_scraper = LandScraperAgent()
plan_agent = PlanAgent()
map_builder = MapDataBuilder()
report_store = ReportStore()
db = Database()
batch_processor = BatchProcessor()
area_analyzer = AreaAnalyzer()
asset_score_agent = AssetScoreAgent()
maisoku_agent = MaisokuAgent()

# バックグラウンドタスク状態管理
_bg_task_status = {"running": False, "step": "", "result": None, "error": None}

# 起動時にサンプル賃料データを読込 → DB + メモリ
_sample_csv = DATA_DIR / "rental_comps_tokyo.csv"
if _sample_csv.exists():
    count = rental_agent.load_comps_from_csv(str(_sample_csv))
    logging.info(f"サンプル賃料データ読込: {count}件")

# 初回DB初期化（駅マスタ + 参考データ投入 + メトリクス計算）
if db.get_db_stats().get("station_metrics", 0) == 0:
    logging.info("初回バッチ: 駅マスタ + 参考データ投入 + メトリクス計算")
    batch_processor.run_full_update()


def _reload_rental_agent():
    """DBからrental_agentのインメモリデータをリロード"""
    rental_agent._comps_db.clear()
    rental_agent._load_db_comps()
    # オーケストレーター内部のrental_agentも更新
    orchestrator.rental_agent._comps_db = rental_agent._comps_db
    orchestrator.valuation_agent.rental_agent._comps_db = rental_agent._comps_db
    logging.info(f"RentalAgent リロード: {len(rental_agent._comps_db)}件")


# ===== ページ =====

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    import hashlib, time
    cache_bust = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
    return templates.TemplateResponse("index.html", {
        "request": request,
        "center": MAP_DEFAULT_CENTER,
        "zoom": MAP_DEFAULT_ZOOM,
        "api_configured": bool(REINFOLIB_API_KEY),
        "cache_bust": cache_bust,
    })


# ===== マスタデータAPI =====

@app.get("/api/cities/{prefecture_code}")
async def get_cities(prefecture_code: str):
    """市区町村一覧"""
    cities = CITY_MASTER.get(prefecture_code, [{"code": "", "name": "全域"}])
    return JSONResponse(content={"cities": cities})


@app.get("/api/stations/{prefecture_code}")
async def get_stations(prefecture_code: str):
    """駅一覧（メトリクス付き）"""
    stations = get_stations_by_prefecture(prefecture_code)
    metrics = db.get_station_metrics(prefecture_code=prefecture_code)
    metrics_map = {m["station_id"]: m for m in metrics}

    result = []
    for s in stations:
        sid = s["station_id"]
        m = metrics_map.get(sid, {})
        result.append({
            "station_id": sid,
            "name": s["name"],
            "line": s.get("line", ""),
            "lat": s["lat"],
            "lon": s["lon"],
            "avg_land_price_sqm": m.get("avg_land_price_sqm"),
            "avg_rent_per_sqm": m.get("avg_rent_per_sqm"),
            "implied_yield": m.get("implied_yield"),
            "passengers_daily": m.get("passengers_daily"),
            "vacancy_rate": m.get("vacancy_rate"),
            "sample_count_land": m.get("sample_count_land", 0),
            "sample_count_rent": m.get("sample_count_rent", 0),
        })

    return JSONResponse(content={"stations": result, "count": len(result)})


# ===== サンプルデータAPI =====

@app.get("/api/sample-properties")
async def get_sample_properties(
    include_land: bool = True,
    sort_by: str = "updated_at",
    station_filter: str = "",
    min_price: int = None,
    max_price: int = None,
    min_yield: float = None,
):
    """サンプル物件 + DB物件 + 土地物件（建築プラン付き）の統合一覧"""
    props = []
    # Static samples
    sample_file = DATA_DIR / "sample_properties.json"
    if sample_file.exists():
        with open(sample_file, "r", encoding="utf-8") as f:
            props = json.load(f)
    # DB properties (scraped etc)
    db_props = db.get_properties(limit=1000)
    for p in db_props:
        p.pop("data_json", None)
        p["_type"] = "property"
        props.append(p)

    # 土地物件+ベストプラン統合
    if include_land:
        land_rows = db.get_land_listings(limit=1000)
        for ll in land_rows:
            plans = db.get_building_plans(ll["id"]) if ll.get("id") else []
            best_plan = max(plans, key=lambda x: x.get("estimated_yield") or 0) if plans else None

            # 土地物件をProperty形式に変換
            total_price = ll.get("land_price") or 0
            annual_rent = None
            gross_yield = None
            structure = None
            building_area = None
            units = None
            plan_label = ""

            if best_plan:
                total_price += best_plan.get("estimated_construction_cost") or 0
                annual_rent = best_plan.get("estimated_annual_income")
                gross_yield = best_plan.get("estimated_yield")
                structure = best_plan.get("structure_type")
                building_area = best_plan.get("actual_total_floor_area_sqm")
                units = best_plan.get("max_units")
                plan_label = f" ({structure}{best_plan.get('floors','')}F/{units}戸)"

            # 判定結果があれば取得
            lj = db.get_land_judgment(ll["id"]) if ll.get("id") else None

            prop = {
                "name": f"[土地] {ll.get('address', '住所不明')}{plan_label}",
                "address": ll.get("address", ""),
                "prefecture_code": "",
                "city_code": "",
                "latitude": ll.get("latitude"),
                "longitude": ll.get("longitude"),
                "asking_price": total_price,
                "land_area": ll.get("land_area_sqm"),
                "building_area": building_area,
                "structure": structure,
                "building_age": 0,
                "current_rent_annual": annual_rent,
                "gross_yield": gross_yield,
                "units": units,
                "nearest_station": ll.get("station"),
                "station_distance_min": ll.get("walk_minutes"),
                "land_use_zone": ll.get("zoning"),
                "building_coverage": ll.get("building_coverage_ratio"),
                "floor_area_ratio": ll.get("floor_area_ratio"),
                "source": ll.get("source", "土地"),
                "source_url": ll.get("source_url"),
                "grade": lj.get("grade") if lj else None,
                "_type": "land",
                "_land_listing_id": ll.get("id"),
                "_land_price": ll.get("land_price"),
            }
            props.append(prop)

    # フィルタ適用
    if station_filter:
        sf = station_filter.lower()
        props = [p for p in props if sf in (p.get("nearest_station") or "").lower()
                 or sf in (p.get("address") or "").lower()]

    if min_price is not None:
        props = [p for p in props if (p.get("asking_price") or 0) >= min_price]
    if max_price is not None:
        props = [p for p in props if (p.get("asking_price") or 0) <= max_price]
    if min_yield is not None:
        props = [p for p in props if (p.get("gross_yield") or 0) >= min_yield]

    # ソート
    sort_keys = {
        "updated_at": lambda p: p.get("fetched_at") or p.get("updated_at") or "",
        "price_asc": lambda p: p.get("asking_price") or float("inf"),
        "price_desc": lambda p: -(p.get("asking_price") or 0),
        "yield_desc": lambda p: -(p.get("gross_yield") or 0),
        "grade": lambda p: {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5}.get(p.get("grade"), 9),
        "station_near": lambda p: p.get("station_distance_min") or 999,
    }
    if sort_by in sort_keys:
        props.sort(key=sort_keys[sort_by])

    # 座標なし物件に駅マスタから推定座標を付与
    _estimate_missing_coords(props)
    return JSONResponse(content={"properties": props, "total": len(props)})


def _estimate_missing_coords(props: list):
    """座標なし物件に最寄駅+徒歩から推定座標を付与"""
    import random
    import math

    # 駅マスタをキャッシュ
    if not hasattr(_estimate_missing_coords, "_station_cache"):
        all_stations = db.get_stations()
        _estimate_missing_coords._station_cache = {
            s["station_name"]: (s["latitude"], s["longitude"])
            for s in all_stations if s.get("latitude") and s.get("longitude")
        }
    station_coords = _estimate_missing_coords._station_cache

    # 区→代表座標のフォールバック
    WARD_CENTER = {
        "13101": (35.694, 139.754), "13102": (35.672, 139.773),
        "13103": (35.658, 139.751), "13104": (35.694, 139.703),
        "13105": (35.717, 139.752), "13106": (35.713, 139.782),
        "13107": (35.711, 139.802), "13108": (35.673, 139.817),
        "13109": (35.609, 139.730), "13110": (35.634, 139.698),
        "13111": (35.561, 139.716), "13112": (35.646, 139.653),
        "13113": (35.664, 139.698), "13114": (35.708, 139.664),
        "13115": (35.700, 139.637), "13116": (35.726, 139.716),
        "13117": (35.753, 139.737), "13118": (35.736, 139.783),
        "13119": (35.751, 139.709), "13120": (35.735, 139.652),
        "13121": (35.775, 139.805), "13122": (35.743, 139.847),
        "13123": (35.707, 139.868),
    }

    for p in props:
        if p.get("latitude") and p.get("longitude"):
            continue  # 既に座標あり

        lat, lng = None, None

        # 1. 最寄駅名から座標取得
        station_name = p.get("nearest_station") or ""
        # "東武亀戸線/小村井駅" → "小村井"
        for sep in ["/", "／", "線"]:
            if sep in station_name:
                station_name = station_name.split(sep)[-1]
        station_name = station_name.replace("駅", "").strip()

        if station_name and station_name in station_coords:
            lat, lng = station_coords[station_name]
        else:
            # 部分一致
            for sname, coords in station_coords.items():
                if station_name and station_name in sname:
                    lat, lng = coords
                    break

        # 2. 駅が見つからない場合、city_codeから区の中心座標
        if lat is None:
            city = p.get("city_code", "")
            if city in WARD_CENTER:
                lat, lng = WARD_CENTER[city]

        if lat is None:
            continue

        # 徒歩分数で駅からオフセット（1分≒80m、ランダム方向）
        walk_min = p.get("station_distance_min") or 5
        offset_km = walk_min * 0.08 / 111.0  # 緯度1度≒111km
        # 物件IDのハッシュで決定論的な方向を付ける（再読込で位置が変わらない）
        pid = hash(str(p.get("id", "") or p.get("name", "")))
        angle = (pid % 360) * math.pi / 180
        p["latitude"] = round(lat + offset_km * math.cos(angle), 6)
        p["longitude"] = round(lng + offset_km * math.sin(angle) / math.cos(math.radians(lat)), 6)
        p["_coords_estimated"] = True  # 推定フラグ


# ===== 地価データAPI =====

@app.get("/api/land-prices/{prefecture_code}")
async def get_land_prices(
    prefecture_code: str,
    city_code: str = "",
    year: int = None,
):
    """地価データをGeoJSON（市区町村ベース集計）で返す"""
    features = []
    with db._conn() as conn:
        sql = """
            SELECT t.city_code, AVG(t.price_per_sqm) as avg_price,
                   COUNT(*) as cnt
            FROM transactions t
            WHERE t.prefecture_code = ? AND t.property_type = '宅地(土地)'
            AND t.price_per_sqm > 0 AND t.price_per_sqm < 10000000
        """
        params = [prefecture_code]
        if city_code:
            sql += " AND t.city_code = ?"
            params.append(city_code)
        sql += " GROUP BY t.city_code HAVING cnt >= 2"
        rows = conn.execute(sql, params).fetchall()

    # 市区町村の代表座標（reinfolib_clientから取得）
    from data.reinfolib_client import ReinfolibClient
    city_centers = ReinfolibClient()._get_city_centers(prefecture_code)
    total_count = 0
    total_price = 0

    for row in [dict(r) for r in rows]:
        cc = row.get("city_code", "")
        coords = city_centers.get(cc)
        if not coords:
            continue
        avg_price = int(row["avg_price"])
        cnt = row["cnt"]
        total_count += cnt
        total_price += avg_price * cnt
        city_name = CITY_NAME_MAP.get(cc, cc)

        color = map_builder._price_to_color(avg_price)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [coords[1], coords[0]]},
            "properties": {
                "address": city_name,
                "price_per_sqm": avg_price,
                "price_label": f"¥{avg_price:,}/㎡",
                "type": "土地取引平均",
                "use_zone": "",
                "station": city_name,
                "change_rate": None,
                "color": color,
                "count": cnt,
                "layer": "land_price",
            },
        })

    avg_all = int(total_price / total_count) if total_count > 0 else 0
    geojson = {"type": "FeatureCollection", "features": features}
    return JSONResponse(content={
        "geojson": geojson,
        "summary": {
            "avg_price": avg_all,
            "median_price": avg_all,
            "count": total_count,
            "area_count": len(features),
            "city_name": CITY_NAME_MAP.get(city_code, ""),
        },
    })


@app.get("/api/transactions/{prefecture_code}")
async def get_transactions(
    prefecture_code: str,
    city_code: str = "",
):
    """取引データをGeoJSON（市区町村ベース集計）で返す"""
    features = []
    with db._conn() as conn:
        sql = """
            SELECT t.city_code, t.property_type,
                   AVG(t.price_per_sqm) as avg_price,
                   AVG(t.transaction_price) as avg_total,
                   COUNT(*) as cnt
            FROM transactions t
            WHERE t.prefecture_code = ? AND t.price_per_sqm > 0
        """
        params = [prefecture_code]
        if city_code:
            sql += " AND t.city_code = ?"
            params.append(city_code)
        sql += " GROUP BY t.city_code, t.property_type HAVING cnt >= 2"
        rows = conn.execute(sql, params).fetchall()

    from data.reinfolib_client import ReinfolibClient
    city_centers = ReinfolibClient()._get_city_centers(prefecture_code)

    for row in [dict(r) for r in rows]:
        cc = row.get("city_code", "")
        coords = city_centers.get(cc)
        if not coords:
            continue
        city_name = CITY_NAME_MAP.get(cc, cc)
        ptype = row.get("property_type", "")
        # 取引種別で少しオフセット
        offset = 0.002 if "土地" in ptype else -0.002 if "マンション" in ptype else 0
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [coords[1] + offset, coords[0]]},
            "properties": {
                "address": f"{city_name} ({ptype})",
                "price": int(row["avg_total"]),
                "price_label": f"¥{int(row['avg_price']):,}/㎡",
                "price_per_sqm": int(row["avg_price"]),
                "date": "",
                "type": ptype,
                "use": "",
                "area": None,
                "count": row["cnt"],
                "layer": "transaction",
            },
        })

    geojson = {"type": "FeatureCollection", "features": features}
    return JSONResponse(content={
        "geojson": geojson,
        "count": len(features),
    })


# ===== 賃料API =====

@app.get("/api/rental-estimate")
async def estimate_rent(
    city_code: str,
    structure: str = "RC",
    building_age: int = 10,
    area_sqm: float = 60,
    station_distance_min: int = 5,
):
    """賃料推定"""
    result = rental_agent.estimate_rent(
        city_code=city_code,
        structure=structure,
        building_age=building_age,
        area_sqm=area_sqm,
        station_distance_min=station_distance_min,
    )
    return JSONResponse(content=result)


@app.post("/api/rental-comps/upload")
async def upload_rental_csv(file: UploadFile = File(...)):
    """賃料CSVアップロード"""
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()
        count = rental_agent.load_comps_from_csv(tmp.name)
        return JSONResponse(content={
            "status": "ok",
            "imported": count,
            "total": len(rental_agent._comps_db),
        })
    finally:
        os.unlink(tmp.name)


@app.get("/api/rental-stats")
async def get_rental_stats():
    """現在の賃料データ統計（DBから直接取得）"""
    import statistics as stats_mod

    # DBから全賃料事例を取得
    rows = db.get_rental_comps(limit=10000)
    if not rows:
        return JSONResponse(content={"count": 0})

    rents = [r["rent_per_sqm"] for r in rows if r.get("rent_per_sqm") and r["rent_per_sqm"] > 0]
    if not rents:
        return JSONResponse(content={"count": len(rows), "avg_rent_per_sqm": 0})

    # 構造別集計
    by_struct = {}
    for r in rows:
        key = r.get("structure") or "不明"
        by_struct.setdefault(key, []).append(r.get("rent_per_sqm", 0))
    by_struct_result = {
        k: {"avg": round(stats_mod.mean(v), 0), "count": len(v)}
        for k, v in by_struct.items() if v and any(x > 0 for x in v)
    }

    # 区別集計
    by_city = {}
    for r in rows:
        key = r.get("city_code") or "不明"
        by_city.setdefault(key, []).append(r.get("rent_per_sqm", 0))
    by_city_result = {
        k: {"avg": round(stats_mod.mean(v), 0), "count": len(v)}
        for k, v in sorted(by_city.items(), key=lambda x: -len(x[1]))[:15]
        if v and any(x > 0 for x in v)
    }

    return JSONResponse(content={
        "count": len(rows),
        "avg_rent_per_sqm": round(stats_mod.mean(rents), 0),
        "median_rent_per_sqm": round(stats_mod.median(rents), 0),
        "by_structure": by_struct_result,
        "by_city": by_city_result,
    })


def _group_by(comps, attr):
    groups = {}
    for c in comps:
        key = getattr(c, attr, None) or "不明"
        groups.setdefault(key, []).append(c.rent_per_sqm)
    import statistics
    return {
        k: {"avg": round(statistics.mean(v), 0), "count": len(v)}
        for k, v in groups.items() if v
    }


def _group_by_station(comps):
    groups = {}
    for c in comps:
        key = c.nearest_station or "不明"
        groups.setdefault(key, []).append(c.rent_per_sqm)
    import statistics
    result = {
        k: {"avg": round(statistics.mean(v), 0), "count": len(v)}
        for k, v in groups.items() if v
    }
    return dict(sorted(result.items(), key=lambda x: x[1]["avg"], reverse=True)[:15])


# ===== 物件分析API =====

@app.post("/api/analyze")
async def analyze_property(request: Request):
    """物件の投資判定を実行"""
    data = await request.json()
    prop = Property.from_dict(data)
    result = orchestrator.run(prop)
    judgment = result["judgment"]
    critic = result.get("critic_review", {})
    return JSONResponse(content={
        "judgment": judgment.to_dict(),
        "valuation": result["valuation"].to_dict(),
        "simulation": result["simulation"].to_dict(),
        "critic_review": critic,
        "summary": judgment.summary_text,
    })


@app.post("/api/analyze-batch")
async def analyze_batch(request: Request):
    """複数物件の一括判定"""
    data = await request.json()
    properties = [Property.from_dict(p) for p in data.get("properties", [])]
    results = orchestrator.run_batch(properties)
    return JSONResponse(content={
        "results": [r["judgment"].to_dict() for r in results],
        "ranking": [
            {"name": r["judgment"].property_name, "grade": r["judgment"].grade,
             "score": r["judgment"].overall_score,
             "recommendation": r["judgment"].recommendation,
             "critic": r.get("critic_review", {}).get("reliability_grade", "?")}
            for r in sorted(results, key=lambda x: x["judgment"].overall_score, reverse=True)
        ],
    })


# ===== スクレイピングAPI =====

@app.get("/api/scrape")
async def scrape_properties(
    prefecture_code: str = "13",
    sources: str = "suumo,rakumachi,athome",
    max_pages: int = 10,
    split_by_price: bool = False,
):
    """複数ソースから収益物件をスクレイピング"""
    try:
        source_list = [s.strip() for s in sources.split(",") if s.strip()]
        props = scraper_agent.run(
            prefecture_code=prefecture_code,
            sources=source_list,
            max_pages=max_pages,
            split_by_price=split_by_price,
        )
        # DB保存
        for p in props:
            try:
                db.upsert_property(p.to_dict())
            except Exception:
                pass
        return JSONResponse(content={
            "count": len(props),
            "sources": source_list,
            "properties": [p.to_dict() for p in props],
        })
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "count": 0, "properties": []},
            status_code=500,
        )


@app.get("/api/scrape-rentals")
async def scrape_rentals(
    prefecture_code: str = "13",
    city_code: str = "",
    max_pages: int = 10,
):
    """SUUMO賃貸から賃料データをスクレイピング"""
    try:
        rentals = scraper_agent.scrape_rentals(
            prefecture_code=prefecture_code,
            city_code=city_code,
            max_pages=max_pages,
        )
        # DB保存
        saved = db.upsert_rental_comps(rentals)
        # インメモリのrental_agentも更新
        _reload_rental_agent()
        return JSONResponse(content={
            "count": len(rentals),
            "saved": saved,
            "rentals": rentals[:50],
        })
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "count": 0},
            status_code=500,
        )


# ===== URL物件取込API =====

@app.post("/api/scrape-url")
async def scrape_url(request: Request):
    """
    URLから1件の物件情報を取込（クロール＋OCR→構造化→DB保存）

    Body: {"url": "https://...", "use_ocr": true, "use_browser": false, "auto_analyze": false}
    """
    data = await request.json()
    url = data.get("url", "").strip()
    if not url:
        return JSONResponse(content={"error": "URLが指定されていません"}, status_code=400)

    use_ocr = data.get("use_ocr", True)
    use_browser = data.get("use_browser", False)
    auto_analyze = data.get("auto_analyze", False)

    try:
        prop = url_scraper.run(url=url, use_ocr=use_ocr, use_browser=use_browser)
        if not prop:
            return JSONResponse(
                content={"error": "物件情報を取得できませんでした", "url": url},
                status_code=422,
            )

        # DB保存
        db.upsert_property(prop.to_dict())

        result = {
            "status": "ok",
            "property": prop.to_dict(),
            "saved_to_db": True,
        }

        # 自動分析
        if auto_analyze:
            try:
                analysis = orchestrator.run(prop)
                judgment = analysis["judgment"]
                result["judgment"] = judgment.to_dict()
                result["summary"] = judgment.summary_text
                result["critic_review"] = analysis.get("critic_review", {})
            except Exception as e:
                result["analyze_error"] = str(e)

        return JSONResponse(content=result)

    except Exception as e:
        import traceback
        return JSONResponse(
            content={"error": str(e), "traceback": traceback.format_exc()},
            status_code=500,
        )


@app.get("/api/scraped-properties")
async def list_scraped_properties(limit: int = 200):
    """DB内のスクレイピング済み物件一覧"""
    props = db.get_properties(limit=limit)
    cleaned = []
    for p in props:
        p.pop("data_json", None)
        cleaned.append(p)
    return JSONResponse(content={"count": len(cleaned), "properties": cleaned})


@app.get("/api/properties/export/csv")
async def export_properties_csv():
    """収益物件一覧をCSV出力（物件情報シートと同一構造）"""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    props = db.get_properties(limit=5000)

    output = io.StringIO()
    writer = csv.writer(output)

    # ヘッダ（物件情報シートと同一）
    writer.writerow([
        "result", "id", "物件名", "住所",
        "都道府県", "都道府県CD", "市区町村", "市区町村CD",
        "路線/駅", "最寄駅", "徒歩(分)",
        "売出価格(円)", "土地面積(㎡)", "建物面積(㎡)",
        "構造", "築年数", "戸数",
        "年間賃料(円)", "表面利回り(%)",
        "ソース", "URL",
        "緯度", "経度",
    ])

    PREF_MAP = {
        "13": "東京都", "14": "神奈川県", "11": "埼玉県", "12": "千葉県",
    }
    for p in props:
        pref = PREF_MAP.get(p.get("prefecture_code", ""), "")
        city = p.get("city_code", "")
        gy = p.get("gross_yield")
        if not gy and p.get("current_rent_annual") and p.get("asking_price"):
            gy = p["current_rent_annual"] / p["asking_price"]

        writer.writerow([
            "OK",
            p.get("id", ""),
            p.get("name", ""),
            p.get("address", ""),
            pref,
            p.get("prefecture_code", ""),
            "",  # city name (extracted from address)
            city,
            "",  # line/station combined
            p.get("nearest_station", ""),
            p.get("station_distance_min", ""),
            p.get("asking_price", ""),
            p.get("land_area", ""),
            p.get("building_area", ""),
            p.get("structure", ""),
            p.get("building_age", ""),
            p.get("units", ""),
            p.get("current_rent_annual", ""),
            round(gy * 100, 2) if gy else "",
            p.get("source", ""),
            p.get("source_url", ""),
            p.get("latitude", ""),
            p.get("longitude", ""),
        ])

    output.seek(0)
    return StreamingResponse(
        iter(['\ufeff' + output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=properties.csv"},
    )


# ===== 土地物件API =====

@app.get("/api/land-listings")
async def list_land_listings(
    station: str = "", min_price: int = None, max_price: int = None,
    min_area: float = None, status: str = "", limit: int = 500,
):
    """土地物件一覧"""
    rows = db.get_land_listings(
        station=station, min_price=min_price, max_price=max_price,
        min_area=min_area, status=status, limit=limit,
    )
    return JSONResponse(content={"count": len(rows), "listings": rows})


@app.get("/api/land-listings/{listing_id}")
async def get_land_listing(listing_id: int):
    """土地物件詳細+全分析データ統合レスポンス"""
    listing = db.get_land_listing_by_id(listing_id)
    if not listing:
        return JSONResponse(content={"error": "not found"}, status_code=404)

    plans = db.get_building_plans(listing_id)
    judgment = db.get_land_judgment(listing_id)
    asset_score = db.get_asset_score(listing_id)

    # 判定結果のJSONパース
    if judgment:
        if judgment.get("key_metrics_json"):
            judgment["key_metrics"] = json.loads(judgment["key_metrics_json"])
        if judgment.get("full_result_json"):
            judgment["full_result"] = json.loads(judgment["full_result_json"])

    # 地価データ（市区町村の取引相場）
    land_value = None
    city_code = listing.get("city_code") or ""
    pref_code = listing.get("prefecture_code") or ""
    if not city_code:
        # 住所から推定
        ll_obj = LandListing.from_dict(listing)
        city_code = ll_obj._guess_city_code()
        pref_code = ll_obj._guess_pref_code()
    if city_code or pref_code:
        try:
            lv = land_agent.estimate_land_value(
                address=listing.get("address", ""),
                land_area=listing.get("land_area_sqm", 0) or 0,
                prefecture_code=pref_code or "13",
                city_code=city_code,
            )
            land_value = lv
        except Exception:
            pass

    return JSONResponse(content={
        "listing": listing,
        "plans": plans,
        "judgment": judgment,
        "asset_score": asset_score,
        "land_value": land_value,
    })


@app.get("/api/land-listings/export/csv")
async def export_land_listings_csv():
    """土地物件一覧をCSV出力（AIマイソク自動解析シートと同一構造）"""
    import csv
    import io
    from fastapi.responses import StreamingResponse

    listings = db.get_land_listings(limit=50000)
    all_plans_cache = {}
    judgments_cache = {}

    for l in listings:
        lid = l["id"]
        plans = db.get_building_plans(lid)
        if plans:
            all_plans_cache[lid] = plans
        j = db.get_land_judgment(lid)
        if j:
            judgments_cache[lid] = j

    output = io.StringIO()
    writer = csv.writer(output)

    # ===== ヘッダ行1: 構造ラベル =====
    header1 = ["", "土地情報", "", "", "", "", "", "", "", "", "", "", "", "",
               "", "木造", "", "", "", "", "", "", "", "重量鉄骨", "", "", "", "", "", "", "",
               "判定", "", "", "メモ"]
    writer.writerow(header1)

    # ===== ヘッダ行2: 階数ラベル =====
    header2 = ["", "", "", "", "", "", "", "", "", "", "", "", "", "",
               "", "2階建て", "", "", "", "3階建て", "", "", "",
               "3階建て", "", "", "", "4階建て", "", "", "",
               "", "", "", ""]
    writer.writerow(header2)

    # ===== ヘッダ行3: 列名（入力シートと同一構造） =====
    writer.writerow([
        "No", "住所", "路線 / 駅", "所要時間\n徒歩(分)", "土地価格",
        "面積\n(m2)", "建蔽率\n(%)", "容積率\n(%)", "用途地域",
        "準防火\n地域", "2方向\n道路", "北道路", "LINK", "解析\n結果",
        # 木造2F
        "最大", "20㎡", "25㎡", "30㎡", "35㎡",
        # 木造3F
        "20㎡", "25㎡", "30㎡", "35㎡",
        # 重量鉄骨3F
        "最大", "20㎡", "25㎡", "30㎡", "35㎡",
        # 重量鉄骨4F
        "20㎡", "25㎡", "30㎡", "35㎡",
        # 判定
        "グレード", "スコア", "判定",
        "メモ",
    ])

    # ===== データ行 =====
    for i, l in enumerate(listings, 1):
        lid = l["id"]
        plans = all_plans_cache.get(lid, [])
        judgment = judgments_cache.get(lid, {})

        # プランをマトリクスに整理
        plan_matrix = {}  # key = (structure, floors, unit_size) -> plan
        for p in plans:
            key = (p.get("structure_type", ""), p.get("floors", 0), p.get("unit_size_sqm", 0))
            plan_matrix[key] = p

        def plan_cell(struct, floors, unit_size):
            """プランセルの値（戸数/利回り）"""
            p = plan_matrix.get((struct, floors, unit_size))
            if p and p.get("max_units", 0) >= 2:
                units = p["max_units"]
                yld = p.get("estimated_yield", 0)
                return f"{units}戸 {yld*100:.1f}%"
            return ""

        def max_units_for(struct, floors_list):
            """構造×階数群の最大戸数"""
            best = None
            for fl in floors_list:
                for sz in [20, 25, 30, 35]:
                    p = plan_matrix.get((struct, fl, sz))
                    if p and p.get("max_units", 0) >= 2:
                        if best is None or p["max_units"] > best["max_units"]:
                            best = p
            if best:
                return f"{best['max_units']}戸"
            return ""

        addr = l.get("address", "")
        line_station = ""
        if l.get("railway_line") or l.get("station"):
            line_station = f"{l.get('railway_line', '')} / {l.get('station', '')}"

        row = [
            i,
            addr,
            line_station,
            l.get("walk_minutes", ""),
            l.get("land_price", ""),
            l.get("land_area_sqm", ""),
            round(l.get("building_coverage_ratio", 0) * 100) if l.get("building_coverage_ratio") else "",
            round(l.get("floor_area_ratio", 0) * 100) if l.get("floor_area_ratio") else "",
            l.get("zoning", ""),
            "対象" if l.get("quasi_fireproof") else "非対象",
            "対象" if l.get("two_way_road") else "非対象",
            "対象" if l.get("north_road") else "非対象",
            l.get("source_url", "") or l.get("maisoku_pdf_path", ""),
            "OK" if plans else "",
            # 木造2F
            max_units_for("木造", [2]),
            plan_cell("木造", 2, 20), plan_cell("木造", 2, 25),
            plan_cell("木造", 2, 30), plan_cell("木造", 2, 35),
            # 木造3F
            plan_cell("木造", 3, 20), plan_cell("木造", 3, 25),
            plan_cell("木造", 3, 30), plan_cell("木造", 3, 35),
            # 重量鉄骨3F
            max_units_for("重量鉄骨", [3, 4, 5]),
            plan_cell("重量鉄骨", 3, 20), plan_cell("重量鉄骨", 3, 25),
            plan_cell("重量鉄骨", 3, 30), plan_cell("重量鉄骨", 3, 35),
            # 重量鉄骨4F
            plan_cell("重量鉄骨", 4, 20), plan_cell("重量鉄骨", 4, 25),
            plan_cell("重量鉄骨", 4, 30), plan_cell("重量鉄骨", 4, 35),
            # 判定
            judgment.get("grade", ""),
            round(judgment.get("overall_score", 0), 1) if judgment.get("overall_score") else "",
            judgment.get("recommendation", ""),
            l.get("memo", ""),
        ]
        writer.writerow(row)

    output.seek(0)
    return StreamingResponse(
        iter(['\ufeff' + output.getvalue()]),  # BOM for Excel UTF-8
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=land_listings.csv"},
    )


@app.put("/api/land-listings/{listing_id}")
async def update_land_listing(listing_id: int, request: Request):
    """土地物件の情報を更新"""
    data = await request.json()

    existing = db.get_land_listing_by_id(listing_id)
    if not existing:
        return JSONResponse(content={"error": "not found"}, status_code=404)

    # Updateable fields
    allowed = [
        "address", "railway_line", "station", "walk_minutes",
        "land_price", "land_area_sqm", "building_coverage_ratio",
        "floor_area_ratio", "zoning", "quasi_fireproof", "two_way_road",
        "north_road", "memo", "latitude", "longitude",
    ]

    updates = []
    params = []
    for field in allowed:
        if field in data:
            updates.append(f"{field}=?")
            params.append(data[field])

    if not updates:
        return JSONResponse(content={"error": "no fields to update"}, status_code=400)

    updates.append("updated_at=datetime('now','localtime')")
    params.append(listing_id)

    with db._conn() as conn:
        conn.execute(
            f"UPDATE land_listings SET {', '.join(updates)} WHERE id=?",
            params,
        )

    # Re-generate plans if key fields changed
    plan_fields = {"land_area_sqm", "building_coverage_ratio", "floor_area_ratio", "zoning", "land_price"}
    if plan_fields & set(data.keys()):
        # Delete old plans and re-generate
        with db._conn() as conn:
            conn.execute("DELETE FROM building_plans WHERE land_listing_id=?", (listing_id,))
            conn.execute("DELETE FROM land_judgments WHERE land_listing_id=?", (listing_id,))
        db.update_land_listing_status(listing_id, "pending")

    return JSONResponse(content={"status": "ok"})


@app.post("/api/land-listings/scrape")
async def scrape_land_listings(request: Request):
    """土地物件スクレイピング実行（バックグラウンド処理）"""
    import threading
    global _bg_task_status

    if _bg_task_status["running"]:
        return JSONResponse(content={
            "status": "running",
            "message": f"実行中: {_bg_task_status['step']}",
        })

    try:
        data = await request.json()
    except Exception:
        data = {}

    def _run_scrape():
        global _bg_task_status
        _bg_task_status = {"running": True, "step": "スクレイピング中...", "result": None, "error": None}
        try:
            _bg_task_status["step"] = "スクレイピング中..."
            result = batch_processor.run_land_pipeline(
                source=data.get("source", "suumo"),
                pref=data.get("prefecture_code", "13"),
                price_min=data.get("price_min"),
                price_max=data.get("price_max"),
                area_min=data.get("area_min"),
                walk_max=data.get("walk_max"),
                max_pages=data.get("max_pages", 3),
            )
            _bg_task_status["result"] = result
            _bg_task_status["step"] = "完了"
        except Exception as e:
            logging.error(f"バックグラウンドスクレイピングエラー: {e}")
            import traceback
            traceback.print_exc()
            _bg_task_status["error"] = str(e)
            _bg_task_status["step"] = f"エラー: {e}"
        finally:
            _bg_task_status["running"] = False

    try:
        thread = threading.Thread(target=_run_scrape, daemon=True)
        thread.start()
        return JSONResponse(content={
            "status": "ok",
            "message": "スクレイピングをバックグラウンドで開始しました。進捗は自動更新されます。",
        })
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "error": str(e)},
            status_code=500,
        )


@app.get("/api/task-status")
async def get_task_status():
    """バックグラウンドタスクの進捗状態"""
    return JSONResponse(content=_bg_task_status)


@app.post("/api/land-listings/import-csv")
async def import_land_csv(file: UploadFile = File(...)):
    """土地物件CSVアップロード取込"""
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()
        inserted = batch_processor.batch_land_listings_from_csv(tmp.name)
        # 取込後にプラン自動生成
        plans = batch_processor.batch_building_plans()
        return JSONResponse(content={
            "status": "ok", "listings_imported": inserted,
            "plans_generated": plans,
        })
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "error": str(e)}, status_code=500
        )
    finally:
        os.unlink(tmp.name)


@app.post("/api/maisoku/analyze")
async def analyze_maisoku(
    file: UploadFile = File(...),
    lat: float = None,
    lng: float = None,
    save_listing: bool = True,
):
    """
    マイソクPDF/画像をアップロードして解析。

    - file: PDF or 画像ファイル
    - lat, lng: 座標（任意、API補完に使用）
    - save_listing: Trueなら解析結果をland_listingsに保存
    """
    import tempfile, os

    # ファイル保存
    suffix = Path(file.filename).suffix if file.filename else ".pdf"
    maisoku_dir = Path(__file__).parent.parent / "output" / "maisoku"
    maisoku_dir.mkdir(parents=True, exist_ok=True)

    # ファイル名: タイムスタンプ + 元ファイル名
    from datetime import datetime as dt
    safe_name = re.sub(r'[^\w.\-]', '_', file.filename or "maisoku")
    saved_path = maisoku_dir / f"{dt.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"

    try:
        content = await file.read()
        with open(saved_path, "wb") as f:
            f.write(content)

        # 解析実行
        result = maisoku_agent.run(
            file_path=str(saved_path),
            lat=lat,
            lng=lng,
            enrich_from_api=True,
        )

        if result.get("error"):
            return JSONResponse(
                content={"status": "error", "error": result["error"], "raw_text": result.get("_raw_text", "")},
                status_code=400,
            )

        # 借地権は除外
        if result.get("_rejected"):
            return JSONResponse(content={
                "status": "rejected",
                "reason": "借地権物件のため対象外",
                "parsed": {k: v for k, v in result.items() if not k.startswith("_")},
            })

        # DB保存
        listing_id = None
        if save_listing:
            listing_dict = maisoku_agent.to_land_listing_dict(result)
            if listing_dict.get("address"):
                try:
                    listing = LandListing.from_dict(listing_dict)
                    listing_data = listing.to_dict()
                    listing_data["analysis_status"] = "ok"
                    listing_id = db.upsert_land_listing(listing_data)
                except Exception as e:
                    logging.warning(f"マイソク物件DB保存エラー: {e}")

        # レスポンス: 内部キーを除外
        parsed = {k: v for k, v in result.items() if not k.startswith("_")}
        return JSONResponse(content={
            "status": "ok",
            "parsed": parsed,
            "listing_id": listing_id,
            "extraction_method": result.get("_extraction_method", ""),
            "confidence": result.get("_confidence", {}),
        })

    except Exception as e:
        logging.error(f"マイソク解析エラー: {e}")
        return JSONResponse(
            content={"status": "error", "error": str(e)},
            status_code=500,
        )


@app.post("/api/maisoku/batch-enrich")
async def batch_enrich_maisoku():
    """マイソクPDF付き物件の一括再解析"""
    try:
        count = batch_processor.batch_enrich_maisoku()
        return JSONResponse(content={"status": "ok", "enriched": count})
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "error": str(e)},
            status_code=500,
        )


@app.post("/api/land-listings/{listing_id}/analyze")
async def analyze_land_listing(listing_id: int):
    """土地物件のプラン生成"""
    listing_data = db.get_land_listing_by_id(listing_id)
    if not listing_data:
        return JSONResponse(content={"error": "not found"}, status_code=404)

    try:
        listing = LandListing.from_dict(listing_data)
        listing.id = listing_data["id"]
        summary = plan_agent.run(listing)

        if summary.plans:
            plan_dicts = [p.to_dict() for p in summary.plans]
            for pd in plan_dicts:
                pd["land_listing_id"] = listing_id
            db.upsert_building_plans(plan_dicts)

        db.update_land_listing_status(listing_id, "ok")
        return JSONResponse(content={
            "status": "ok",
            "summary": summary.to_dict(),
        })
    except Exception as e:
        db.update_land_listing_status(listing_id, "error")
        return JSONResponse(
            content={"status": "error", "error": str(e)}, status_code=500
        )


@app.post("/api/land-listings/batch-analyze")
async def batch_analyze_land():
    """未分析土地の一括プラン生成"""
    try:
        plans = batch_processor.batch_building_plans()
        return JSONResponse(content={"status": "ok", "plans_generated": plans})
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "error": str(e)}, status_code=500
        )


@app.get("/api/building-plans/{listing_id}")
async def get_building_plans(listing_id: int):
    """建築プラン取得"""
    plans = db.get_building_plans(listing_id)
    return JSONResponse(content={"plans": plans, "count": len(plans)})


@app.get("/api/best-plans")
async def get_best_plans(sort_by: str = "estimated_yield", limit: int = 100):
    """最高利回りプラン一覧"""
    rows = db.get_best_plans(sort_by=sort_by, limit=limit)
    return JSONResponse(content={"plans": rows, "count": len(rows)})


# ===== 土地投資判定API =====

@app.post("/api/land-listings/{listing_id}/judge")
async def judge_land_listing(listing_id: int):
    """土地物件のフル投資判定（資産性スコアリング含む）"""
    listing_data = db.get_land_listing_by_id(listing_id)
    if not listing_data:
        return JSONResponse(content={"error": "not found"}, status_code=404)

    try:
        listing = LandListing.from_dict(listing_data)
        listing.id = listing_data["id"]

        # 資産性スコアリング（投資判定の一部として実行）
        asset_score_data = None
        if listing_data.get("latitude") and listing_data.get("longitude"):
            try:
                as_result = asset_score_agent.run(
                    lat=listing_data["latitude"], lng=listing_data["longitude"],
                    land_area_sqm=listing_data.get("land_area_sqm"),
                    station_distance_min=listing_data.get("walk_minutes"),
                    station_name=listing_data.get("station"),
                    has_retaining_wall=bool(listing_data.get("has_retaining_wall")),
                )
                db.upsert_asset_score({
                    "land_listing_id": listing_id,
                    "overall_score": as_result.overall_score,
                    "grade": as_result.grade,
                    "summary": as_result.summary,
                    "road_score": as_result.road_info.road_score,
                    "road_info": as_result.road_info.to_dict(),
                    "hazard_score": as_result.hazard_info.hazard_score,
                    "hazard_info": as_result.hazard_info.to_dict(),
                    "elevation_score": as_result.elevation_info.terrain_score,
                    "elevation_info": as_result.elevation_info.to_dict(),
                    "lot_shape_score": as_result.lot_shape.shape_score,
                    "lot_shape_info": as_result.lot_shape.to_dict(),
                    "population_score": as_result.population.population_score,
                    "population_info": as_result.population.to_dict(),
                    "station_distance_score": as_result.station_distance_score,
                })
                asset_score_data = as_result.to_dict()
            except Exception as e:
                logging.warning(f"資産性スコアリングエラー (ID={listing_id}): {e}")

        plans = db.get_building_plans(listing_id)
        if not plans:
            return JSONResponse(content={"error": "プランなし。先にプラン生成してください。"}, status_code=400)

        from models.building_plan import BuildingPlan
        best_plan = BuildingPlan.from_dict(plans[0])
        prop = listing.to_property(best_plan)
        # 資産性グレードをオーケストレーターに渡して判定に反映
        as_grade = as_result.grade if asset_score_data else None
        analysis = orchestrator.run(prop, asset_score_grade=as_grade)
        judgment = analysis["judgment"]

        db.save_land_judgment({
            "land_listing_id": listing_id,
            "building_plan_id": plans[0].get("id"),
            "grade": judgment.grade,
            "recommendation": judgment.recommendation,
            "overall_score": judgment.overall_score,
            "confidence": judgment.confidence,
            "key_metrics": judgment.key_metrics,
        })

        return JSONResponse(content={
            "status": "ok",
            "judgment": judgment.to_dict(),
            "critic_review": analysis.get("critic_review", {}),
            "asset_score": asset_score_data,
        })
    except Exception as e:
        import traceback
        return JSONResponse(
            content={"status": "error", "error": str(e), "traceback": traceback.format_exc()},
            status_code=500,
        )


@app.post("/api/land-listings/batch-judge")
async def batch_judge_land():
    """土地物件の一括投資判定（資産性スコアリング含む、バックグラウンド）"""
    import threading
    global _bg_task_status

    if _bg_task_status["running"]:
        return JSONResponse(content={"status": "running", "message": f"実行中: {_bg_task_status['step']}"})

    def _run():
        global _bg_task_status
        _bg_task_status = {"running": True, "step": "資産性スコアリング中...", "result": None, "error": None}
        try:
            scored = batch_processor.batch_asset_scores()
            _bg_task_status["step"] = "投資判定中..."
            judged = batch_processor.batch_land_judgments()
            _bg_task_status["result"] = {"scored": scored, "judged": judged}
            _bg_task_status["step"] = "完了"
        except Exception as e:
            logging.error(f"バッチ判定エラー: {e}")
            _bg_task_status["error"] = str(e)
            _bg_task_status["step"] = f"エラー: {e}"
        finally:
            _bg_task_status["running"] = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return JSONResponse(content={
        "status": "ok",
        "message": "資産性分析+投資判定をバックグラウンドで開始しました。",
    })


@app.get("/api/land-listings/{listing_id}/judgment")
async def get_land_judgment(listing_id: int):
    """土地物件の判定結果取得"""
    result = db.get_land_judgment(listing_id)
    if not result:
        return JSONResponse(content={"judgment": None})
    # Parse JSON fields
    if result.get("key_metrics_json"):
        result["key_metrics"] = json.loads(result["key_metrics_json"])
    if result.get("full_result_json"):
        result["full_result"] = json.loads(result["full_result_json"])
    return JSONResponse(content={"judgment": result})


@app.get("/api/compare")
async def get_compare_data(
    sort_by: str = "estimated_yield",
    limit: int = 500,
    min_yield: float = None,
    min_area: float = None,
    station: str = "",
    grade: str = "",
):
    """全物件比較テーブル用データ（収益物件+土地物件統合）"""
    rows = []

    # 土地物件（プラン付き）
    with db._conn() as conn:
        sql = """
            SELECT ll.id, 'land' as type, ll.address, ll.station, ll.walk_minutes,
                   ll.land_price, ll.land_area_sqm,
                   ll.building_coverage_ratio, ll.floor_area_ratio, ll.zoning,
                   ll.latitude, ll.longitude, ll.source, ll.source_url,
                   bp.structure_type, bp.floors, bp.unit_size_sqm, bp.max_units,
                   bp.estimated_yield, bp.total_investment, bp.estimated_annual_income,
                   las.overall_score as asset_score, las.grade as asset_grade,
                   las.road_score, las.hazard_score, las.lot_shape_score,
                   las.station_distance_score, las.summary as asset_summary,
                   lj.grade as judge_grade, lj.overall_score as judge_score,
                   lj.recommendation
            FROM land_listings ll
            LEFT JOIN building_plans bp ON bp.land_listing_id = ll.id
                AND bp.id = (SELECT bp2.id FROM building_plans bp2
                             WHERE bp2.land_listing_id = ll.id
                             ORDER BY bp2.estimated_yield DESC LIMIT 1)
            LEFT JOIN land_asset_scores las ON las.land_listing_id = ll.id
            LEFT JOIN land_judgments lj ON lj.land_listing_id = ll.id
                AND lj.id = (SELECT lj2.id FROM land_judgments lj2
                             WHERE lj2.land_listing_id = ll.id
                             ORDER BY lj2.id DESC LIMIT 1)
            WHERE ll.duplicate_of_id IS NULL
        """
        params = []
        if station:
            sql += " AND ll.station LIKE ?"
            params.append(f"%{station}%")
        if min_yield is not None:
            sql += " AND bp.estimated_yield >= ?"
            params.append(min_yield)
        if min_area is not None:
            sql += " AND ll.land_area_sqm >= ?"
            params.append(min_area)
        if grade:
            sql += " AND (las.grade = ? OR lj.grade = ?)"
            params.extend([grade, grade])

        allowed_sorts = {
            "estimated_yield": "bp.estimated_yield",
            "land_price": "ll.land_price",
            "land_area_sqm": "ll.land_area_sqm",
            "asset_score": "las.overall_score",
            "judge_score": "lj.overall_score",
            "walk_minutes": "ll.walk_minutes",
        }
        order = allowed_sorts.get(sort_by, "bp.estimated_yield")
        sql += f" ORDER BY {order} DESC NULLS LAST LIMIT ?"
        params.append(limit)

        land_rows = conn.execute(sql, params).fetchall()
        rows.extend([dict(r) for r in land_rows])

    # 収益物件（properties + judgments）
    with db._conn() as conn:
        sql2 = """
            SELECT p.id, 'property' as type, p.address, p.nearest_station as station,
                   p.station_distance_min as walk_minutes,
                   p.asking_price as land_price, p.land_area as land_area_sqm,
                   p.building_coverage as building_coverage_ratio,
                   p.floor_area_ratio, p.land_use_zone as zoning,
                   p.latitude, p.longitude, p.source, p.source_url,
                   p.structure as structure_type, p.floors, NULL as unit_size_sqm,
                   p.units as max_units,
                   p.gross_yield as estimated_yield,
                   p.asking_price as total_investment,
                   p.current_rent_annual as estimated_annual_income,
                   NULL as asset_score, NULL as asset_grade,
                   NULL as road_score, NULL as hazard_score, NULL as lot_shape_score,
                   NULL as station_distance_score, NULL as asset_summary,
                   j.grade as judge_grade, j.overall_score as judge_score,
                   j.recommendation
            FROM properties p
            LEFT JOIN judgments j ON j.property_id = p.id
                AND j.id = (SELECT j2.id FROM judgments j2
                            WHERE j2.property_id = p.id ORDER BY j2.id DESC LIMIT 1)
            WHERE 1=1
        """
        params2 = []
        if station:
            sql2 += " AND p.nearest_station LIKE ?"
            params2.append(f"%{station}%")
        if min_yield is not None:
            sql2 += " AND p.gross_yield >= ?"
            params2.append(min_yield)
        if min_area is not None:
            sql2 += " AND p.land_area >= ?"
            params2.append(min_area)

        sql2 += f" ORDER BY p.gross_yield DESC NULLS LAST LIMIT ?"
        params2.append(limit)

        prop_rows = conn.execute(sql2, params2).fetchall()
        rows.extend([dict(r) for r in prop_rows])

    # 統合ソート
    def sort_key(r):
        if sort_by == "estimated_yield":
            return r.get("estimated_yield") or 0
        if sort_by == "asset_score":
            return r.get("asset_score") or 0
        if sort_by == "judge_score":
            return r.get("judge_score") or 0
        if sort_by == "land_price":
            return r.get("land_price") or 0
        return r.get("estimated_yield") or 0

    rows.sort(key=sort_key, reverse=True)
    rows = rows[:limit]

    return JSONResponse(content={"rows": rows, "count": len(rows)})


@app.post("/api/land-listings/geocode")
async def geocode_land_listings():
    """土地物件の一括ジオコーディング"""
    try:
        count = batch_processor.batch_geocode()
        return JSONResponse(content={"status": "ok", "geocoded": count})
    except Exception as e:
        return JSONResponse(content={"status": "error", "error": str(e)}, status_code=500)


# ===== 資産性スコアAPI =====

@app.get("/api/land-listings/{listing_id}/asset-score")
async def get_asset_score(listing_id: int):
    """土地物件の資産性スコア取得"""
    result = db.get_asset_score(listing_id)
    return JSONResponse(content={"asset_score": result})


@app.post("/api/land-listings/{listing_id}/asset-score")
async def run_asset_score(listing_id: int):
    """単一物件の資産性スコアリング実行"""
    listing_data = db.get_land_listing_by_id(listing_id)
    if not listing_data:
        return JSONResponse(content={"error": "not found"}, status_code=404)
    if not listing_data.get("latitude") or not listing_data.get("longitude"):
        return JSONResponse(content={"error": "座標が未設定です。ジオコーディングを先に実行してください。"}, status_code=400)

    try:
        result = asset_score_agent.run(
            lat=listing_data["latitude"],
            lng=listing_data["longitude"],
            land_area_sqm=listing_data.get("land_area_sqm"),
            station_distance_min=listing_data.get("walk_minutes"),
        )

        db.upsert_asset_score({
            "land_listing_id": listing_id,
            "overall_score": result.overall_score,
            "grade": result.grade,
            "summary": result.summary,
            "road_score": result.road_info.road_score,
            "road_info": result.road_info.to_dict(),
            "hazard_score": result.hazard_info.hazard_score,
            "hazard_info": result.hazard_info.to_dict(),
            "elevation_score": result.elevation_info.terrain_score,
            "elevation_info": result.elevation_info.to_dict(),
            "lot_shape_score": result.lot_shape.shape_score,
            "lot_shape_info": result.lot_shape.to_dict(),
            "population_score": result.population.population_score,
            "population_info": result.population.to_dict(),
            "station_distance_score": result.station_distance_score,
        })

        return JSONResponse(content={"status": "ok", "asset_score": result.to_dict()})
    except Exception as e:
        return JSONResponse(content={"status": "error", "error": str(e)}, status_code=500)


@app.post("/api/land-listings/batch-asset-score")
async def batch_asset_score():
    """土地物件の一括資産性スコアリング（バックグラウンド）"""
    import threading
    global _bg_task_status

    if _bg_task_status["running"]:
        return JSONResponse(content={"status": "running", "message": f"実行中: {_bg_task_status['step']}"})

    def _run():
        global _bg_task_status
        _bg_task_status = {"running": True, "step": "資産性スコアリング中...", "result": None, "error": None}
        try:
            count = batch_processor.batch_asset_scores()
            _bg_task_status["result"] = {"scored": count}
            _bg_task_status["step"] = "完了"
        except Exception as e:
            logging.error(f"バッチスコアリングエラー: {e}")
            _bg_task_status["error"] = str(e)
            _bg_task_status["step"] = f"エラー: {e}"
        finally:
            _bg_task_status["running"] = False

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return JSONResponse(content={"status": "ok", "message": "資産性スコアリングを開始しました。"})


# ===== スクレイピング設定API =====

@app.post("/api/scrape-config")
async def save_scrape_config(request: Request):
    """検索条件を保存"""
    data = await request.json()
    config_id = db.save_scrape_config(data)
    return JSONResponse(content={"status": "ok", "id": config_id})


@app.get("/api/scrape-configs")
async def list_scrape_configs():
    """保存済み検索条件一覧"""
    configs = db.get_scrape_configs()
    return JSONResponse(content={"configs": configs})



# ===== 重複検出API =====

@app.post("/api/land-listings/dedup")
async def dedup_land_listings():
    """土地物件の重複検出"""
    count = db.detect_duplicates()
    return JSONResponse(content={"status": "ok", "duplicates_marked": count})


# ===== スケジューラAPI =====

@app.post("/api/scheduler/start")
async def start_scheduler():
    """定期スクレイピング開始"""
    from engine.scheduler import scheduler
    scheduler.start()
    return JSONResponse(content={"status": "ok", "running": scheduler.is_running})


@app.post("/api/scheduler/stop")
async def stop_scheduler():
    """定期スクレイピング停止"""
    from engine.scheduler import scheduler
    scheduler.stop()
    return JSONResponse(content={"status": "ok", "running": scheduler.is_running})


@app.get("/api/scheduler/status")
async def scheduler_status():
    """スケジューラ状態"""
    from engine.scheduler import scheduler
    return JSONResponse(content={"running": scheduler.is_running})


# ===== データ大量収集API =====

@app.post("/api/collect/run")
async def run_data_collection():
    """大量データ収集をバックグラウンドで実行"""
    import threading
    def _run():
        try:
            from engine.data_collector import run_collection
            run_collection()
        except Exception as e:
            logging.error(f"データ収集エラー: {e}")
    threading.Thread(target=_run, daemon=True).start()
    stats = db.get_db_stats()
    return JSONResponse(content={
        "status": "ok",
        "message": "データ収集をバックグラウンドで開始しました（一都三県×全価格帯）。完了まで30-60分。",
        "current_listings": stats.get("land_listings", 0),
    })


# ===== 実データ大量取得API =====

@app.post("/api/ingest/real-data")
async def ingest_real_data():
    """reinfolib APIから実データ大量取得（バックグラウンド）"""
    import threading
    def _run():
        try:
            bp = BatchProcessor()
            if bp.api.is_configured():
                logging.info("=== 実データ大量取得開始 ===")
                tx = bp.ingest_real_transactions()
                lp = bp.ingest_real_land_prices()
                olp = bp.ingest_official_land_prices()
                for p in ["13", "14", "11", "12"]:
                    bp.compute_station_metrics(p)
                logging.info(f"=== 実データ取得完了: 取引{tx}件, 地価{lp}件, 公示地価(XPT002){olp}件 ===")
            else:
                logging.warning("APIキー未設定")
        except Exception as e:
            logging.error(f"実データ取得エラー: {e}")
    threading.Thread(target=_run, daemon=True).start()
    configured = batch_processor.api.is_configured()
    return JSONResponse(content={
        "status": "ok" if configured else "warning",
        "message": "実データ取得を開始しました" if configured else "APIキーが未設定です。.envにREINFOLIB_API_KEYを設定してください。",
        "api_configured": configured,
    })


# ===== 統合データベースビューAPI =====

@app.get("/api/unified-data")
async def get_unified_data(
    limit: int = 500, offset: int = 0,
    sort_by: str = "updated_at", sort_dir: str = "DESC",
    min_price: int = None, max_price: int = None,
    station: str = "", zoning: str = "",
):
    """統合データベースビュー - 全テーブルをJOINして一覧"""
    import json as _json

    allowed_sorts = {"updated_at", "land_price", "land_area_sqm", "estimated_yield", "overall_score"}
    col = sort_by if sort_by in allowed_sorts else "ll.updated_at"
    direction = "ASC" if sort_dir.upper() == "ASC" else "DESC"

    # Sort column mapping
    sort_map = {
        "updated_at": "ll.updated_at",
        "land_price": "ll.land_price",
        "land_area_sqm": "ll.land_area_sqm",
        "estimated_yield": "bp.estimated_yield",
        "overall_score": "lj.overall_score",
    }
    order_col = sort_map.get(sort_by, "ll.updated_at")

    with db._conn() as conn:
        sql = """
            SELECT
                ll.id, ll.address, ll.railway_line, ll.station, ll.walk_minutes,
                ll.land_price, ll.land_area_sqm,
                ll.building_coverage_ratio, ll.floor_area_ratio,
                ll.zoning, ll.quasi_fireproof, ll.two_way_road, ll.north_road,
                ll.source, ll.source_url, ll.memo,
                ll.latitude, ll.longitude,
                ll.analysis_status, ll.created_at, ll.updated_at,
                bp.structure_type AS best_structure,
                bp.floors AS best_floors,
                bp.unit_size_sqm AS best_unit_size,
                bp.max_units AS best_units,
                bp.estimated_yield AS best_yield,
                bp.estimated_annual_income AS best_annual_income,
                bp.estimated_construction_cost AS best_construction_cost,
                bp.total_investment AS best_total_investment,
                bp.estimated_rent_per_sqm AS best_rent_per_sqm,
                lj.grade, lj.overall_score, lj.recommendation,
                lj.key_metrics_json, lj.confidence
            FROM land_listings ll
            LEFT JOIN building_plans bp ON bp.land_listing_id = ll.id
                AND bp.id = (
                    SELECT bp2.id FROM building_plans bp2
                    WHERE bp2.land_listing_id = ll.id
                    ORDER BY bp2.estimated_yield DESC LIMIT 1
                )
            LEFT JOIN land_judgments lj ON lj.land_listing_id = ll.id
                AND lj.id = (
                    SELECT lj2.id FROM land_judgments lj2
                    WHERE lj2.land_listing_id = ll.id
                    ORDER BY lj2.id DESC LIMIT 1
                )
            WHERE ll.duplicate_of_id IS NULL
        """
        params = []

        if min_price:
            sql += " AND ll.land_price >= ?"
            params.append(min_price)
        if max_price:
            sql += " AND ll.land_price <= ?"
            params.append(max_price)
        if station:
            sql += " AND ll.station LIKE ?"
            params.append(f"%{station}%")
        if zoning:
            sql += " AND ll.zoning LIKE ?"
            params.append(f"%{zoning}%")

        sql += f" ORDER BY {order_col} {direction} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        data = []
        for r in rows:
            d = dict(r)
            # Parse key_metrics JSON
            if d.get("key_metrics_json"):
                try:
                    d["key_metrics"] = _json.loads(d["key_metrics_json"])
                except Exception:
                    d["key_metrics"] = {}
                del d["key_metrics_json"]
            data.append(d)

        # Total count
        count_sql = "SELECT COUNT(*) as cnt FROM land_listings WHERE duplicate_of_id IS NULL"
        total = dict(conn.execute(count_sql).fetchone())["cnt"]

    return JSONResponse(content={
        "data": data,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


# ===== レポートAPI =====

@app.get("/api/reports")
async def list_reports():
    reports = report_store.list_reports()
    items = []
    for r in reports[:50]:
        try:
            data = report_store.load(str(r))
            j = data.get("judgment", {})
            p = data.get("property", {})
            items.append({
                "filename": r.name,
                "property_name": p.get("name", ""),
                "grade": j.get("grade", ""),
                "score": j.get("overall_score", 0),
                "recommendation": j.get("recommendation", ""),
                "generated_at": data.get("generated_at", ""),
            })
        except Exception:
            items.append({"filename": r.name})
    return JSONResponse(content={"reports": items})


@app.get("/api/reports/{filename}")
async def get_report(filename: str):
    filepath = report_store.report_dir / filename
    if not filepath.exists():
        return JSONResponse(content={"error": "not found"}, status_code=404)
    data = report_store.load(str(filepath))
    return JSONResponse(content=data)


# ===== システムAPI =====

@app.get("/api/status")
async def system_status():
    """システム状態"""
    stats = db.get_db_stats()
    return JSONResponse(content={
        "api_configured": bool(REINFOLIB_API_KEY),
        "rental_comps_count": len(rental_agent._comps_db),
        "reports_count": len(report_store.list_reports()),
        "db": stats,
        "last_batch": db.last_batch_time("land_prices"),
    })


# ===== データベースAPI =====

@app.get("/api/db/stats")
async def db_stats():
    """DB統計"""
    return JSONResponse(content=db.get_db_stats())


@app.get("/api/db/land-prices")
async def db_land_prices(
    city_code: str = "", prefecture_code: str = "13",
    station_id: str = "", limit: int = 500,
):
    """DB内の地価データ"""
    rows = db.get_land_prices(
        city_code=city_code, prefecture_code=prefecture_code,
        station_id=station_id, limit=limit,
    )
    return JSONResponse(content={"count": len(rows), "data": rows})


@app.get("/api/db/transactions")
async def db_transactions(
    city_code: str = "", prefecture_code: str = "13",
    station_id: str = "", limit: int = 500,
):
    """DB内の取引データ"""
    rows = db.get_transactions(
        city_code=city_code, prefecture_code=prefecture_code,
        station_id=station_id, limit=limit,
    )
    return JSONResponse(content={"count": len(rows), "data": rows})


@app.get("/api/db/rental-comps")
async def db_rental_comps(
    city_code: str = "", station: str = "",
    station_id: str = "", limit: int = 500,
):
    """DB内の賃料データ"""
    rows = db.get_rental_comps(
        city_code=city_code, station=station,
        station_id=station_id, limit=limit,
    )
    return JSONResponse(content={"count": len(rows), "data": rows})


# ===== バッチ処理API =====

@app.post("/api/batch/run")
async def run_batch(request: Request):
    """バッチ処理を実行"""
    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    prefectures = data.get("prefectures", ["13"])

    try:
        batch_processor.run_full_update(prefectures)
        stats = db.get_db_stats()
        return JSONResponse(content={"status": "completed", "db": stats})
    except Exception as e:
        return JSONResponse(content={"status": "error", "error": str(e)}, status_code=500)


@app.get("/api/batch/logs")
async def batch_logs(limit: int = 30):
    """バッチ処理ログ"""
    logs = db.get_batch_logs(limit)
    return JSONResponse(content={"logs": logs})


# ===== 駅単位歪み分析API =====

@app.get("/api/analysis/distortion")
async def station_distortion(prefecture_code: str = "13"):
    """駅単位歪み分析"""
    results = area_analyzer.analyze_all_areas(prefecture_code)
    geojson = area_analyzer.build_distortion_geojson(results)
    from dataclasses import asdict
    ranking = [asdict(r) for r in results]
    return JSONResponse(content={
        "geojson": geojson,
        "ranking": ranking,
        "count": len(results),
    })


@app.get("/api/analysis/station-detail/{station_id}")
async def station_detail(station_id: str):
    """駅詳細データ"""
    detail = area_analyzer.get_area_detail(station_id)
    return JSONResponse(content=detail)


# レガシー互換
@app.get("/api/analysis/area-detail/{city_code}")
async def area_detail(city_code: str):
    """エリア詳細データ（レガシー互換）"""
    detail = area_analyzer.get_area_detail(city_code)
    return JSONResponse(content=detail)


@app.get("/api/analysis/competition")
async def competition_analysis(
    station: str = "",
    prefecture_code: str = "13",
):
    """競合分析 - 駅周辺の賃料分布・空室率・間取り分布"""
    import statistics

    # 賃料データ取得
    comps = db.get_rental_comps(station=station, limit=2000)
    if not comps:
        comps = db.get_rental_comps(limit=2000)

    # 間取りサイズ別分布
    size_buckets = {"~20㎡": [], "20-30㎡": [], "30-40㎡": [], "40-50㎡": [], "50㎡~": []}
    structure_dist = {}

    for c in comps:
        area = c.get("area_sqm", 0)
        rent = c.get("rent_monthly", 0)
        rpsqm = c.get("rent_per_sqm", 0)
        struct = c.get("structure", "不明")

        if area <= 0 or rent <= 0:
            continue

        # サイズ別
        if area < 20:
            size_buckets["~20㎡"].append({"rent": rent, "rpsqm": rpsqm})
        elif area < 30:
            size_buckets["20-30㎡"].append({"rent": rent, "rpsqm": rpsqm})
        elif area < 40:
            size_buckets["30-40㎡"].append({"rent": rent, "rpsqm": rpsqm})
        elif area < 50:
            size_buckets["40-50㎡"].append({"rent": rent, "rpsqm": rpsqm})
        else:
            size_buckets["50㎡~"].append({"rent": rent, "rpsqm": rpsqm})

        # 構造別
        structure_dist.setdefault(struct, []).append(rpsqm)

    # 集計
    size_summary = {}
    for bucket, items in size_buckets.items():
        if items:
            rents = [i["rent"] for i in items]
            rpsqms = [i["rpsqm"] for i in items]
            size_summary[bucket] = {
                "count": len(items),
                "avg_rent": round(statistics.mean(rents)),
                "median_rent": round(statistics.median(rents)),
                "avg_rpsqm": round(statistics.mean(rpsqms)),
                "min_rent": min(rents),
                "max_rent": max(rents),
            }

    struct_summary = {}
    for struct, rpsqms in structure_dist.items():
        if rpsqms:
            struct_summary[struct] = {
                "count": len(rpsqms),
                "avg_rpsqm": round(statistics.mean(rpsqms)),
                "median_rpsqm": round(statistics.median(rpsqms)),
            }

    # 地価推移（年別）
    lps = db.get_land_prices(prefecture_code=prefecture_code, limit=10000)
    year_prices = {}
    for lp in lps:
        y = lp.get("year", 0)
        p = lp.get("price_per_sqm", 0)
        if y > 2000 and p > 0:
            year_prices.setdefault(y, []).append(p)

    price_trend = {str(y): {"avg": round(statistics.mean(ps)), "count": len(ps)}
                   for y, ps in sorted(year_prices.items()) if ps}

    # 駅統計データ（乗降客数・空室率）
    station_stats = {}
    if station:
        metrics = db.get_station_metrics(prefecture_code=prefecture_code)
        for m in metrics:
            if m.get("station_name") and station in m["station_name"]:
                station_stats = {
                    "station_name": m.get("station_name"),
                    "passengers_daily": m.get("passengers_daily"),
                    "vacancy_rate": m.get("vacancy_rate"),
                    "avg_land_price_sqm": m.get("avg_land_price_sqm"),
                    "avg_rent_per_sqm": m.get("avg_rent_per_sqm"),
                    "implied_yield": m.get("implied_yield"),
                }
                break

    return JSONResponse(content={
        "size_distribution": size_summary,
        "structure_distribution": struct_summary,
        "price_trend": price_trend,
        "station_stats": station_stats,
        "total_comps": len(comps),
        "station": station,
    })


@app.get("/api/analysis/metrics")
async def analysis_metrics(prefecture_code: str = "13"):
    """駅メトリクス一覧"""
    metrics = db.get_station_metrics(prefecture_code=prefecture_code)
    return JSONResponse(content={"metrics": metrics, "count": len(metrics)})


# ===== 分析ページ =====

@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page(request: Request):
    """駅単位歪み分析ページ"""
    return templates.TemplateResponse("analysis.html", {
        "request": request,
        "center": MAP_DEFAULT_CENTER,
        "zoom": MAP_DEFAULT_ZOOM,
    })


# ===== 資産性分析API =====

@app.get("/api/asset-score")
async def get_asset_score(
    lat: float,
    lng: float,
    land_area: float = None,
    station_distance_min: int = None,
    city_code: str = None,
    prefecture_code: str = None,
    road_frontage: str = None,
    land_shape: str = None,
):
    """物件の資産性スコアを算出"""
    try:
        result = asset_score_agent.run(
            lat=lat, lng=lng,
            land_area_sqm=land_area,
            station_distance_min=station_distance_min,
            city_code=city_code,
            prefecture_code=prefecture_code,
            road_frontage=road_frontage,
            land_shape=land_shape,
        )
        return JSONResponse(content=result.to_dict())
    except Exception as e:
        logging.error(f"資産性分析エラー: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/hazard-tiles")
async def get_hazard_tile_urls():
    """ハザードマップタイルURL一覧"""
    return JSONResponse(content={"tiles": HAZARD_TILE_URLS})


@app.get("/api/elevation")
async def get_elevation(lat: float, lng: float):
    """国土地理院標高API"""
    import requests as req
    try:
        resp = req.get(
            "https://cyberjapandata2.gsi.go.jp/general/dem/scripts/getelevation.php",
            params={"lon": lng, "lat": lat, "outtype": "JSON"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e), "elevation": None}, status_code=500)


@app.get("/api/isochrone")
async def get_isochrone(
    lat: float,
    lng: float,
    range_seconds: int = 900,
    profile: str = "foot-walking",
):
    """OpenRouteService アイソクロン（到達圏ポリゴン）"""
    import requests as req
    if not ORS_API_KEY:
        return JSONResponse(content={"error": "ORS_API_KEY未設定", "geojson": None}, status_code=400)
    try:
        resp = req.post(
            f"{ORS_API_BASE}/v2/isochrones/{profile}",
            json={
                "locations": [[lng, lat]],
                "range": [range_seconds],
                "range_type": "time",
            },
            headers={
                "Authorization": ORS_API_KEY,
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return JSONResponse(content={"geojson": resp.json()})
    except Exception as e:
        return JSONResponse(content={"error": str(e), "geojson": None}, status_code=500)


@app.get("/api/population/{city_code}")
async def get_population(city_code: str, prefecture_code: str = "13"):
    """市区町村の人口動態データ"""
    try:
        result = asset_score_agent._analyze_population(city_code, prefecture_code)
        return JSONResponse(content=result.to_dict())
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/export/kml")
async def export_kml():
    """物件データをKML形式でエクスポート（Google Earth用）"""
    from fastapi.responses import Response

    props = db.get_properties(limit=1000)
    _estimate_missing_coords(props)

    land_listings_raw = db.get_land_listings(limit=1000)

    kml_parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2">',
        '<Document>',
        '<name>不動産投資判定マップ</name>',
        '<description>物件・土地物件データ</description>',
        # スタイル定義
        '<Style id="grade-S"><IconStyle><color>ff00ff00</color><scale>1.2</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>',
        '<Style id="grade-A"><IconStyle><color>ff00cc00</color><scale>1.1</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/paddle/grn-circle.png</href></Icon></IconStyle></Style>',
        '<Style id="grade-B"><IconStyle><color>ff00ccff</color><scale>1.0</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/paddle/ylw-circle.png</href></Icon></IconStyle></Style>',
        '<Style id="grade-C"><IconStyle><color>ff0088ff</color><scale>0.9</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/paddle/orange-circle.png</href></Icon></IconStyle></Style>',
        '<Style id="grade-D"><IconStyle><color>ff0000ff</color><scale>0.8</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href></Icon></IconStyle></Style>',
        '<Style id="grade-F"><IconStyle><color>ff000088</color><scale>0.7</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/paddle/red-circle.png</href></Icon></IconStyle></Style>',
        '<Style id="land"><IconStyle><color>ffff8800</color><scale>0.9</scale>'
        '<Icon><href>http://maps.google.com/mapfiles/kml/paddle/blu-circle.png</href></Icon></IconStyle></Style>',
    ]

    # 収益物件フォルダ
    kml_parts.append('<Folder><name>収益物件</name>')
    for p in props:
        if not p.get("latitude") or not p.get("longitude"):
            continue
        grade = p.get("grade", "?")
        price = p.get("asking_price", 0)
        price_label = f"{price // 10000:,}万円" if price else "不明"
        yld = p.get("gross_yield")
        yld_label = f"{yld*100:.1f}%" if yld else "不明"
        name_esc = (p.get("name") or p.get("address") or "物件").replace("&", "&amp;").replace("<", "&lt;")
        addr_esc = (p.get("address") or "").replace("&", "&amp;").replace("<", "&lt;")

        kml_parts.append(f'''<Placemark>
<name>{name_esc}</name>
<description><![CDATA[
<b>価格:</b> {price_label}<br>
<b>利回り:</b> {yld_label}<br>
<b>構造:</b> {p.get("structure", "不明")}<br>
<b>築年数:</b> {p.get("building_age", "不明")}年<br>
<b>住所:</b> {addr_esc}<br>
<b>グレード:</b> {grade}
]]></description>
<styleUrl>#grade-{grade if grade in "SABCDF" else "C"}</styleUrl>
<Point><coordinates>{p["longitude"]},{p["latitude"]},0</coordinates></Point>
</Placemark>''')
    kml_parts.append('</Folder>')

    # 土地物件フォルダ
    kml_parts.append('<Folder><name>土地物件</name>')
    for ll in land_listings_raw:
        lat = ll.get("latitude")
        lng = ll.get("longitude")
        if not lat or not lng:
            continue
        price = ll.get("land_price", 0)
        price_label = f"{price // 10000:,}万円" if price else "不明"
        area = ll.get("land_area_sqm")
        area_label = f"{area:.1f}㎡" if area else "不明"
        addr_esc = (ll.get("address") or "").replace("&", "&amp;").replace("<", "&lt;")

        kml_parts.append(f'''<Placemark>
<name>{addr_esc}</name>
<description><![CDATA[
<b>価格:</b> {price_label}<br>
<b>面積:</b> {area_label}<br>
<b>用途地域:</b> {ll.get("zoning", "不明")}<br>
<b>建蔽率/容積率:</b> {ll.get("building_coverage_ratio", "?")}/{ll.get("floor_area_ratio", "?")}<br>
<b>駅:</b> {ll.get("station", "不明")} 徒歩{ll.get("walk_minutes", "?")}分
]]></description>
<styleUrl>#land</styleUrl>
<Point><coordinates>{lng},{lat},0</coordinates></Point>
</Placemark>''')
    kml_parts.append('</Folder>')

    kml_parts.extend(['</Document>', '</kml>'])
    kml_content = "\n".join(kml_parts)

    return Response(
        content=kml_content,
        media_type="application/vnd.google-earth.kml+xml",
        headers={"Content-Disposition": "attachment; filename=realestate_map.kml"},
    )


# ===== Analyze with Asset Score =====

@app.post("/api/analyze-full")
async def analyze_full(request: Request):
    """物件の投資判定 + 資産性分析を統合実行"""
    data = await request.json()
    prop = Property.from_dict(data)

    # 通常の投資判定
    result = orchestrator.run(prop)
    judgment = result["judgment"]
    critic = result.get("critic_review", {})

    # 資産性分析（座標がある場合）
    asset_score = None
    lat = prop.latitude or data.get("latitude")
    lng = prop.longitude or data.get("longitude")
    if lat and lng:
        try:
            asset_result = asset_score_agent.run(
                lat=float(lat), lng=float(lng),
                land_area_sqm=prop.land_area,
                station_distance_min=prop.station_distance_min,
                city_code=prop.city_code,
                prefecture_code=prop.prefecture_code,
                road_frontage=prop.road_frontage,
                land_shape=prop.land_shape,
            )
            asset_score = asset_result.to_dict()
        except Exception as e:
            logging.warning(f"資産性分析スキップ: {e}")

    return JSONResponse(content={
        "judgment": judgment.to_dict(),
        "valuation": result["valuation"].to_dict(),
        "simulation": result["simulation"].to_dict(),
        "critic_review": critic,
        "summary": judgment.summary_text,
        "asset_score": asset_score,
    })


# ===== 投資分析レイヤーAPI =====

@app.get("/api/layers/land-price")
async def layer_land_price(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
):
    """地価レイヤー: 公示地価(XPT002) DB→GeoJSON ヒートマップ"""
    cached = db.get_api_land_prices(south, west, north, east, limit=5000)
    features = []
    for r in cached:
        if not r.get("latitude") or not r.get("longitude") or not r.get("price_per_sqm"):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
            "properties": {
                "price": r["price_per_sqm"],
                "place": r.get("place_name", ""),
                "zoning": r.get("zoning", ""),
                "station": r.get("station", ""),
                "change_rate": r.get("change_rate"),
                "type": "公示" if r.get("land_price_type") == 0 else "基準",
            },
        })
    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features), "source": "db"},
    })


@app.get("/api/layers/rent")
async def layer_rent(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
    mode: str = "detail",
):
    """賃料レイヤー: 3モード対応
    mode=detail: 個別物件ドット（最高粒度）
    mode=station: 駅×徒歩帯集計
    mode=area: 駅単位サマリー
    """
    features = []

    with db._conn() as conn:
        if mode == "detail":
            # 個別物件ドット（投資用1K帯: 15-35m2を優先表示）
            rows = conn.execute("""
                SELECT rc.address, rc.rent_monthly, rc.area_sqm, rc.rent_per_sqm,
                       rc.layout, rc.structure, rc.built_year,
                       rc.nearest_station, rc.station_distance_min,
                       rc.latitude, rc.longitude
                FROM rental_comps rc
                WHERE rc.rent_per_sqm > 0
                AND rc.latitude IS NOT NULL AND rc.latitude > 0
                AND rc.latitude BETWEEN ? AND ?
                AND rc.longitude BETWEEN ? AND ?
                ORDER BY rc.rent_per_sqm DESC
                LIMIT 5000
            """, (south, north, west, east)).fetchall()

            for r in [dict(x) for x in rows]:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
                    "properties": {
                        "rent": r["rent_monthly"],
                        "rent_sqm": round(r["rent_per_sqm"]),
                        "area": r["area_sqm"],
                        "layout": r.get("layout") or "",
                        "structure": r.get("structure") or "",
                        "age": (datetime.now().year - r["built_year"]) if r.get("built_year") else None,
                        "station": r.get("nearest_station") or "",
                        "walk": r.get("station_distance_min"),
                        "address": r.get("address") or "",
                    },
                })

        elif mode == "station":
            # 駅×徒歩帯集計（5分刻み）
            rows = conn.execute("""
                SELECT rc.nearest_station,
                       CASE
                           WHEN rc.station_distance_min <= 5 THEN '~5分'
                           WHEN rc.station_distance_min <= 10 THEN '6~10分'
                           ELSE '11分~'
                       END as walk_band,
                       AVG(rc.rent_per_sqm) avg_r,
                       MIN(rc.rent_per_sqm) min_r,
                       MAX(rc.rent_per_sqm) max_r,
                       COUNT(*) cnt,
                       AVG(rc.area_sqm) avg_area,
                       AVG(rc.latitude) lat, AVG(rc.longitude) lng
                FROM rental_comps rc
                WHERE rc.rent_per_sqm > 0
                AND rc.latitude IS NOT NULL AND rc.latitude > 0
                AND rc.latitude BETWEEN ? AND ?
                AND rc.longitude BETWEEN ? AND ?
                AND rc.station_distance_min IS NOT NULL
                GROUP BY rc.nearest_station, walk_band
                HAVING cnt >= 2
                ORDER BY avg_r DESC
            """, (south, north, west, east)).fetchall()

            for r in [dict(x) for x in rows]:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
                    "properties": {
                        "station": r["nearest_station"],
                        "walk_band": r["walk_band"],
                        "avg_rent": round(r["avg_r"]),
                        "min_rent": round(r["min_r"]),
                        "max_rent": round(r["max_r"]),
                        "avg_area": round(r.get("avg_area") or 0, 1),
                        "samples": r["cnt"],
                    },
                })

        else:  # mode == "area"
            # 駅単位サマリー
            rows = conn.execute("""
                SELECT sm.station_name, sm.line_name,
                       sm.center_lat, sm.center_lng,
                       sm.avg_rent_per_sqm, sm.sample_count_rent
                FROM station_metrics sm
                WHERE sm.avg_rent_per_sqm > 0
                AND sm.center_lat BETWEEN ? AND ?
                AND sm.center_lng BETWEEN ? AND ?
            """, (south, north, west, east)).fetchall()
            for r in [dict(x) for x in rows]:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [r["center_lng"], r["center_lat"]]},
                    "properties": {
                        "station": r["station_name"],
                        "line": r.get("line_name") or "",
                        "avg_rent": round(r["avg_rent_per_sqm"]),
                        "samples": r["sample_count_rent"],
                    },
                })

    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features), "mode": mode},
    })


@app.get("/api/layers/yield-distortion")
async def layer_yield_distortion(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
):
    """利回り歪みレイヤー: 駅別の想定利回り・歪みスコア→GeoJSON（center_lat/lngを使用）"""
    features = []
    with db._conn() as conn:
        rows = conn.execute("""
            SELECT sm.station_name, sm.line_name,
                   sm.center_lat, sm.center_lng,
                   sm.avg_land_price_sqm, sm.avg_rent_per_sqm,
                   sm.implied_yield, sm.distortion_score,
                   sm.sample_count_land, sm.sample_count_rent
            FROM station_metrics sm
            WHERE sm.implied_yield > 0
            AND sm.center_lat IS NOT NULL AND sm.center_lat > 0
            AND sm.center_lat BETWEEN ? AND ?
            AND sm.center_lng BETWEEN ? AND ?
        """, (south, north, west, east)).fetchall()

    for r in [dict(x) for x in rows]:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["center_lng"], r["center_lat"]]},
            "properties": {
                "station": r["station_name"],
                "line": r.get("line_name") or "",
                "land_price": round(r["avg_land_price_sqm"]),
                "rent": round(r["avg_rent_per_sqm"]),
                "yield": round(r["implied_yield"] * 100, 1),
                "distortion": round(r.get("distortion_score") or 0, 1),
                "samples_land": r["sample_count_land"],
                "samples_rent": r["sample_count_rent"],
            },
        })
    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features)},
    })


@app.get("/api/layers/transactions")
async def layer_transactions(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
    property_type: str = "",
):
    """取引実績レイヤー: 市区町村別取引集計→GeoJSON"""
    from data.reinfolib_client import ReinfolibClient
    city_centers = ReinfolibClient()._get_city_centers("")

    features = []
    with db._conn() as conn:
        sql = """
            SELECT t.city_code, t.property_type,
                   AVG(t.price_per_sqm) avg_psm,
                   AVG(t.transaction_price) avg_tp,
                   COUNT(*) cnt,
                   t.prefecture_code
            FROM transactions t
            WHERE t.price_per_sqm > 0 AND t.price_per_sqm < 50000000
        """
        params = []
        if property_type:
            sql += " AND t.property_type LIKE ?"
            params.append(f"%{property_type}%")
        sql += " GROUP BY t.city_code, t.property_type HAVING cnt >= 3"
        rows = conn.execute(sql, params).fetchall()

    for r in [dict(x) for x in rows]:
        cc = r.get("city_code", "")
        coords = city_centers.get(cc)
        if not coords:
            continue
        lat, lng = coords
        if not (south <= lat <= north and west <= lng <= east):
            continue
        ptype = r.get("property_type", "")
        offset = 0.002 if "土地" in ptype else -0.002 if "マンション" in ptype else 0
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng + offset, lat]},
            "properties": {
                "city_code": cc,
                "city_name": CITY_NAME_MAP.get(cc, cc),
                "property_type": ptype,
                "avg_price_sqm": round(r["avg_psm"]),
                "avg_total_price": round(r["avg_tp"]),
                "count": r["cnt"],
            },
        })
    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features)},
    })


@app.get("/api/layers/population")
async def layer_population(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
):
    """人口動態レイヤー: DB→GeoJSON メッシュ"""
    cached = db.get_api_population_mesh(south, west, north, east, limit=5000)
    features = []
    for r in cached:
        geom = None
        if r.get("geometry_json"):
            try:
                geom = json.loads(r["geometry_json"])
            except Exception:
                pass
        if not geom:
            continue
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "mesh_id": r.get("mesh_id", ""),
                "pop_current": r.get("pop_current"),
                "pop_future": r.get("pop_future"),
                "change_rate": r.get("change_rate"),
            },
        })
    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features)},
    })


@app.get("/api/layers/facilities")
async def layer_facilities(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
    category: str = "",
):
    """施設レイヤー: DB→GeoJSON"""
    cached = db.get_api_facilities(south, west, north, east, category=category, limit=5000)
    features = []
    for r in cached:
        if not r.get("latitude") or not r.get("longitude"):
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
            "properties": {
                "name": r.get("name", ""),
                "category": r.get("category", ""),
                "address": r.get("address", ""),
            },
        })
    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features)},
    })


@app.get("/api/layers/station-power")
async def layer_station_power(
    south: float = 35.3, west: float = 139.3,
    north: float = 36.1, east: float = 140.3,
):
    """駅力分析ヒートマップ: 路線数マスタ対応 + 100点満点 + データ欠損フラグ"""
    import math
    from config.settings import STATION_LINE_COUNT, STATION_POWER_WEIGHTS

    terminals = {
        "東京": (35.6812, 139.7671), "新宿": (35.6896, 139.7006),
        "渋谷": (35.6580, 139.7016), "池袋": (35.7295, 139.7109),
        "品川": (35.6284, 139.7388), "上野": (35.7141, 139.7774),
        "横浜": (35.4660, 139.6226), "大宮": (35.9063, 139.6237),
        "千葉": (35.6131, 140.1134), "立川": (35.6980, 139.4138),
        "町田": (35.5424, 139.4465), "川崎": (35.5309, 139.7030),
        "船橋": (35.7015, 139.9855), "柏": (35.8681, 139.9755),
    }

    def _hdist(lat1, lon1, lat2, lon2):
        R = 6371
        dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _nearest_terminal(lat, lng):
        best_name, best_dist = "", 999
        for tn, (tlat, tlng) in terminals.items():
            d = _hdist(lat, lng, tlat, tlng)
            if d < best_dist:
                best_dist, best_name = d, tn
        return best_name, best_dist

    w = STATION_POWER_WEIGHTS
    features = []

    with db._conn() as conn:
        stations_raw = conn.execute("""
            SELECT station_name, line_name, latitude, longitude, prefecture_code
            FROM stations WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
        """, (south, north, west, east)).fetchall()

        # 地価マップ（駅名部分一致対応）
        lp_map = {}
        for r in conn.execute("SELECT nearest_station, AVG(price_per_sqm) a, COUNT(*) c FROM land_prices WHERE price_per_sqm > 0 GROUP BY nearest_station").fetchall():
            d = dict(r); lp_map[d["nearest_station"]] = d["a"]

        # 賃料マップ（nearest_stationから駅名抽出）
        rent_map = {}
        for r in conn.execute("SELECT nearest_station, AVG(rent_per_sqm) a, COUNT(*) c FROM rental_comps WHERE rent_per_sqm > 0 GROUP BY nearest_station").fetchall():
            d = dict(r)
            rent_map[d["nearest_station"]] = {"rent": d["a"], "cnt": d["c"]}
            # 駅名部分もマップ
            import re as _re
            parts = _re.split(r"[/／]", d["nearest_station"])
            if len(parts) >= 2:
                sname = parts[-1].replace("駅", "")
                if sname not in rent_map:
                    rent_map[sname] = {"rent": d["a"], "cnt": d["c"]}

        # 取引マップ
        tx_map = {}
        for r in conn.execute("SELECT nearest_station, COUNT(*) c FROM transactions WHERE price_per_sqm > 0 AND nearest_station IS NOT NULL GROUP BY nearest_station").fetchall():
            d = dict(r); tx_map[d["nearest_station"]] = d["c"]

    all_scores = []
    missing_data_stations = []  # データ欠損駅リスト

    for s in [dict(x) for x in stations_raw]:
        name = s["station_name"]
        lat, lng = s["latitude"], s["longitude"]
        missing = []

        # 1. ターミナル近接度 (0-100 → ×weight)
        tn, tdist = _nearest_terminal(lat, lng)
        # 距離減衰: 0km=100, 5km=80, 10km=60, 20km=30, 30km=10, 40km+=0
        raw_terminal = max(0, 100 - tdist * 2.5)

        # 2. 路線数 (0-100): マスタ優先、1路線=15, 2=35, 3=55, 4=70, 5=80, 8+=95, 10+=100
        lc = STATION_LINE_COUNT.get(name, 1)
        if lc >= 10: raw_lines = 100
        elif lc >= 8: raw_lines = 95
        elif lc >= 5: raw_lines = 80
        elif lc >= 4: raw_lines = 70
        elif lc >= 3: raw_lines = 55
        elif lc >= 2: raw_lines = 35
        else: raw_lines = 15

        # 3. 地価水準 (0-100)
        lp = lp_map.get(name, 0)
        if not lp:
            # 部分一致
            for k, v in lp_map.items():
                if name in k:
                    lp = v; break
        if lp > 0:
            # 10万=20, 30万=45, 50万=65, 100万=85, 300万+=100
            raw_lp = min(100, max(0, math.log10(max(1, lp)) * 25 - 100))
        else:
            raw_lp = 0
            missing.append("land_price")

        # 4. 賃料水準 (0-100)
        rd = rent_map.get(name, {})
        rent = rd.get("rent", 0)
        if not rent:
            for k, v in rent_map.items():
                if name in k:
                    rent = v["rent"]; break
        if rent > 0:
            # 2000=20, 3000=40, 4000=55, 5000=70, 6000=80, 8000+=100
            raw_rent = min(100, max(0, (rent - 1500) / 65))
        else:
            raw_rent = 0
            missing.append("rental")

        # 5. 取引活性度 (0-100)
        tx_cnt = tx_map.get(name, 0)
        if not tx_cnt:
            for k, v in tx_map.items():
                if name in k:
                    tx_cnt = v; break
        if tx_cnt > 0:
            raw_tx = min(100, math.log10(max(1, tx_cnt)) * 30)
        else:
            raw_tx = 0
            missing.append("transactions")

        # 6. 人口密度 (0-100)
        raw_pop = 0
        try:
            pop_row = conn.execute("""
                SELECT SUM(pop_current) t FROM api_population_mesh
                WHERE center_lat BETWEEN ? AND ? AND center_lng BETWEEN ? AND ?
            """, (lat - 0.005, lat + 0.005, lng - 0.007, lng + 0.007)).fetchone()
            if pop_row and pop_row[0]:
                raw_pop = min(100, pop_row[0] / 80)
        except Exception:
            pass

        # 加重合計 → 100点満点
        total = (raw_terminal * w["terminal"]
                 + raw_lines * w["lines"]
                 + raw_lp * w["land_price"]
                 + raw_rent * w["rent"]
                 + raw_tx * w["transactions"]
                 + raw_pop * w["population"])
        total = round(total, 1)
        all_scores.append(total)

        if missing:
            missing_data_stations.append({"station": name, "lat": lat, "lng": lng, "missing": missing})

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "station": name,
                "line": s.get("line_name", ""),
                "score": total,
                "terminal_name": tn,
                "terminal_dist": round(tdist, 1),
                "terminal_raw": round(raw_terminal, 1),
                "line_count": lc,
                "lines_raw": round(raw_lines, 1),
                "land_price": round(lp),
                "lp_raw": round(raw_lp, 1),
                "rent": round(rent),
                "rent_raw": round(raw_rent, 1),
                "tx_count": tx_cnt,
                "tx_raw": round(raw_tx, 1),
                "pop_raw": round(raw_pop, 1),
                "missing": missing,
            },
        })

    stats = {}
    if all_scores:
        stats = {"min": round(min(all_scores), 1), "max": round(max(all_scores), 1),
                 "avg": round(sum(all_scores) / len(all_scores), 1)}

    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {
            "count": len(features),
            "score_range": [stats.get("min", 0), stats.get("max", 0)],
            "score_avg": stats.get("avg", 0),
            "missing_data_count": len(missing_data_stations),
            "weights": w,
        },
    })


# ===== 能動的データ収集（駅単位欠損補完） =====

@app.get("/api/collection/gaps")
async def collection_gaps():
    """全駅のデータ欠損状況を診断"""
    with db._conn() as conn:
        stations_raw = conn.execute(
            "SELECT station_name, latitude, longitude, prefecture_code FROM stations"
        ).fetchall()

        # 賃料データ有無
        rent_stations = set()
        for r in conn.execute("SELECT DISTINCT nearest_station FROM rental_comps WHERE rent_per_sqm > 0").fetchall():
            rent_stations.add(dict(r)["nearest_station"])

        # 地価データ有無
        lp_stations = set()
        for r in conn.execute("SELECT DISTINCT nearest_station FROM land_prices WHERE price_per_sqm > 0").fetchall():
            lp_stations.add(dict(r)["nearest_station"])

        # 取引データ有無
        tx_stations = set()
        for r in conn.execute("SELECT DISTINCT nearest_station FROM transactions WHERE price_per_sqm > 0 AND nearest_station IS NOT NULL").fetchall():
            tx_stations.add(dict(r)["nearest_station"])

    gaps = {"rental": [], "land_price": [], "transactions": []}
    for s in [dict(x) for x in stations_raw]:
        name = s["station_name"]
        has_rent = any(name in rs for rs in rent_stations)
        has_lp = any(name in ls for ls in lp_stations)
        has_tx = any(name in ts for ts in tx_stations)
        if not has_rent:
            gaps["rental"].append(name)
        if not has_lp:
            gaps["land_price"].append(name)
        if not has_tx:
            gaps["transactions"].append(name)

    return JSONResponse(content={
        "total_stations": len(stations_raw),
        "coverage": {
            "rental": len(stations_raw) - len(gaps["rental"]),
            "land_price": len(stations_raw) - len(gaps["land_price"]),
            "transactions": len(stations_raw) - len(gaps["transactions"]),
        },
        "gaps": {k: len(v) for k, v in gaps.items()},
        "gap_stations_rental": gaps["rental"][:50],
        "gap_stations_land_price": gaps["land_price"][:50],
    })


@app.post("/api/collection/fill-gaps")
async def collection_fill_gaps(
    target: str = "rental",
    max_stations: int = 20,
    max_pages_per_station: int = 3,
):
    """データ欠損駅に対して能動的にスクレイピング/API取得を実行（バックグラウンド）

    target: rental / land_price / transactions / all
    優先度: rental→SUUMO, transactions→reinfolib API, land_price→reinfolib API
    """
    import threading

    def _run():
        try:
            from data.reinfolib_client import ReinfolibClient
            api_client = ReinfolibClient()

            with db._conn() as conn:
                all_stations = conn.execute(
                    "SELECT station_name, latitude, longitude, prefecture_code FROM stations"
                ).fetchall()
                all_stations = [dict(x) for x in all_stations]

                # 既存データ駅名セット
                rent_set = {dict(r)["nearest_station"] for r in
                            conn.execute("SELECT DISTINCT nearest_station FROM rental_comps WHERE rent_per_sqm > 0").fetchall()}
                lp_set = {dict(r)["nearest_station"] for r in
                          conn.execute("SELECT DISTINCT nearest_station FROM land_prices WHERE price_per_sqm > 0").fetchall()}
                tx_set = {dict(r)["nearest_station"] for r in
                          conn.execute("SELECT DISTINCT nearest_station FROM transactions WHERE nearest_station IS NOT NULL").fetchall()}

            targets = [target] if target != "all" else ["rental", "land_price", "transactions"]
            total_collected = {}

            for t in targets:
                collected = 0
                # 欠損駅抽出
                gap_stations = []
                for s in all_stations:
                    name = s["station_name"]
                    has = any(name in x for x in (rent_set if t == "rental" else lp_set if t == "land_price" else tx_set))
                    if not has:
                        gap_stations.append(s)
                gap_stations = gap_stations[:max_stations]

                if t == "rental":
                    # SUUMO賃貸スクレイピング（駅名検索）
                    logging.info(f"=== 賃料収集開始: {len(gap_stations)}駅 ===")
                    for s in gap_stations:
                        try:
                            results = scraper_agent.scrape_rentals(
                                prefecture_code=s["prefecture_code"],
                                max_pages=max_pages_per_station,
                            )
                            if results:
                                saved = db.upsert_rental_comps(results)
                                collected += saved
                                logging.info(f"  賃料: {s['station_name']} → {saved}件")
                        except Exception as e:
                            logging.debug(f"  賃料エラー {s['station_name']}: {e}")

                elif t == "land_price" and api_client.is_configured():
                    # reinfolib XPT002
                    logging.info(f"=== 地価収集開始: {len(gap_stations)}駅 ===")
                    for s in gap_stations:
                        try:
                            points = api_client.get_official_land_prices(
                                s["latitude"], s["longitude"], zoom=14
                            )
                            records = []
                            for p in points:
                                parsed = _parse_xpt002_point(p)
                                if not parsed:
                                    continue
                                records.append({
                                    "point_id": parsed["point_id"],
                                    "place_name": parsed["place_name"],
                                    "price_per_sqm": parsed["price_num"],
                                    "year": datetime.now().year - 1,
                                    "latitude": parsed["latitude"],
                                    "longitude": parsed["longitude"],
                                    "zoning": parsed["zoning"],
                                    "station": parsed["station"],
                                    "change_rate": parsed["change_rate"],
                                    "coverage": parsed["coverage"],
                                    "far": parsed["far"],
                                    "fire_prevention": parsed["fire_prevention"],
                                    "land_price_type": parsed.get("land_price_type", 0),
                                    "prefecture_code": s["prefecture_code"],
                                    "city_code": "",
                                })
                            if records:
                                saved = db.upsert_api_land_prices(records)
                                collected += saved
                        except Exception as e:
                            logging.debug(f"  地価エラー {s['station_name']}: {e}")

                elif t == "transactions" and api_client.is_configured():
                    # reinfolib XIT001
                    logging.info(f"=== 取引収集開始: {len(gap_stations)}駅 ===")
                    from data.station_master import resolve_station_id
                    current_year = datetime.now().year
                    for s in gap_stations:
                        try:
                            # 最寄りの市区町村コードで取得
                            city = s.get("city_code", "")
                            if not city:
                                continue
                            raw = api_client.get_transactions(
                                current_year - 1, 1,
                                area=s["prefecture_code"], city=city,
                            )
                            records = []
                            for item in raw:
                                try:
                                    price = int(item.get("TradePrice", 0))
                                    area = float(item.get("Area", 0)) if item.get("Area") else None
                                    records.append({
                                        "address": item.get("Municipality", "") + item.get("DistrictName", ""),
                                        "transaction_price": price,
                                        "price_per_sqm": price / area if area and area > 0 else None,
                                        "transaction_date": item.get("Period", ""),
                                        "land_area": area,
                                        "property_type": item.get("Type", ""),
                                        "nearest_station": item.get("NearestStation", ""),
                                        "station_distance_min": int(item.get("TimeToNearestStation", 0) or 0),
                                        "prefecture_code": s["prefecture_code"],
                                        "city_code": city,
                                    })
                                except Exception:
                                    continue
                            if records:
                                saved = db.upsert_transactions(records)
                                collected += saved
                        except Exception as e:
                            logging.debug(f"  取引エラー {s['station_name']}: {e}")

                total_collected[t] = collected
                logging.info(f"=== {t}収集完了: {collected}件 ===")

            logging.info(f"=== 全収集完了: {total_collected} ===")
        except Exception as e:
            logging.error(f"データ収集エラー: {e}")

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse(content={
        "status": "started",
        "target": target,
        "max_stations": max_stations,
        "message": f"{target}のデータ欠損補完を開始しました（最大{max_stations}駅）",
    })


# ===== 不動産情報ライブラリAPI連携エンドポイント =====

def _parse_xpt002_point(p: dict) -> dict:
    """XPT002レスポンスを正規化して返す。Noneなら無効"""
    if p.get("pause_flag") == 1 and not p.get("last_years_price"):
        return None
    plat = p.get("_lat")
    plng = p.get("_lng")
    if plat is None or plng is None:
        return None

    price_num = p.get("last_years_price") or 0
    price_str_raw = p.get("u_current_years_price_ja", "")
    if price_num and isinstance(price_num, (int, float)) and price_num > 0:
        price_label = f"{int(price_num):,}"
    elif price_str_raw:
        price_label = str(price_str_raw)
    else:
        return None

    place_name = (p.get("standard_lot_number_ja")
                 or p.get("place_name_ja")
                 or p.get("ward_town_village_name_ja", ""))
    station_name = p.get("nearest_station_name_ja", "")
    zoning = (p.get("regulations_use_category_name_ja")
             or p.get("use_category_name_ja", ""))
    change_rate = p.get("year_on_year_change_rate")
    if change_rate == "-" or change_rate == "":
        change_rate = None
    elif change_rate is not None:
        try:
            change_rate = float(str(change_rate).replace("%", ""))
        except (ValueError, TypeError):
            change_rate = None

    return {
        "latitude": plat, "longitude": plng,
        "price_num": int(price_num) if price_num else 0,
        "price_label": price_label,
        "place_name": place_name,
        "station": station_name,
        "zoning": zoning,
        "change_rate": change_rate,
        "coverage": p.get("u_regulations_building_coverage_ratio_ja", ""),
        "far": p.get("u_regulations_floor_area_ratio_ja", ""),
        "fire_prevention": p.get("regulations_fireproof_name_ja", ""),
        "land_price_type": p.get("land_price_type"),
        "point_id": str(p.get("point_id", "")),
        "city_code": p.get("city_code", ""),
        "prefecture_code": p.get("prefecture_code", ""),
    }


def _land_price_to_feature(r: dict) -> dict:
    """正規化済み地価データ→GeoJSON Feature"""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
        "properties": {
            "price_label": r.get("price_label", f"{r.get('price_per_sqm', r.get('price_num', 0)):,}"),
            "price_num": r.get("price_num") or r.get("price_per_sqm", 0),
            "station": r.get("station", ""),
            "place_name": r.get("place_name", ""),
            "zoning": r.get("zoning", ""),
            "change_rate": r.get("change_rate"),
            "coverage": r.get("coverage", ""),
            "far": r.get("far", ""),
            "fire_prevention": r.get("fire_prevention", ""),
            "land_price_type": r.get("land_price_type"),
            "layer": "official_land_price",
        },
    }


@app.get("/api/reinfolib/land-prices")
async def reinfolib_land_prices(
    south: float = None, west: float = None,
    north: float = None, east: float = None,
    lat: float = None, lng: float = None,
    year: int = None, zoom: int = 13,
    force_fetch: bool = False,
):
    """公示地価取得: bounds指定→複数タイル取得→DB保存→GeoJSON返却。DB既存データがあればそれを返す"""
    # bounds が指定されていなければ lat/lng から概算
    if south is None and lat is not None:
        south, west = lat - 0.05, lng - 0.08
        north, east = lat + 0.05, lng + 0.08
    if south is None:
        return JSONResponse(content={"error": "bounds or lat/lng required"}, status_code=400)

    # 1. DBに既存データがあればそれを返す（force_fetch でない限り）
    if not force_fetch:
        cached = db.get_api_land_prices(south, west, north, east)
        if cached:
            features = [_land_price_to_feature(r) for r in cached]
            return JSONResponse(content={
                "geojson": {"type": "FeatureCollection", "features": features},
                "count": len(features), "source": "db",
            })

    # 2. APIから取得
    from data.reinfolib_client import ReinfolibClient
    client = ReinfolibClient()
    if not client.is_configured():
        return JSONResponse(content={"error": "APIキー未設定", "geojson": None}, status_code=400)

    try:
        points = client.get_official_land_prices_bounds(south, west, north, east, year=year, zoom=zoom)
        features = []
        db_records = []
        for p in points:
            parsed = _parse_xpt002_point(p)
            if not parsed:
                continue
            features.append(_land_price_to_feature(parsed))
            db_records.append({
                "point_id": parsed["point_id"],
                "place_name": parsed["place_name"],
                "price_per_sqm": parsed["price_num"],
                "year": year or (datetime.now().year - 1),
                "latitude": parsed["latitude"],
                "longitude": parsed["longitude"],
                "zoning": parsed["zoning"],
                "station": parsed["station"],
                "change_rate": parsed["change_rate"],
                "coverage": parsed["coverage"],
                "far": parsed["far"],
                "fire_prevention": parsed["fire_prevention"],
                "land_price_type": parsed["land_price_type"],
                "prefecture_code": parsed.get("prefecture_code", ""),
                "city_code": parsed.get("city_code", ""),
            })

        # 3. DBに保存
        if db_records:
            saved = db.upsert_api_land_prices(db_records)
            logging.info(f"公示地価DB保存: {saved}件")

        geojson = {"type": "FeatureCollection", "features": features}
        return JSONResponse(content={"geojson": geojson, "count": len(features), "source": "api"})
    except Exception as e:
        logging.error(f"XPT002エラー: {e}")
        return JSONResponse(content={"error": str(e), "geojson": None}, status_code=500)


@app.get("/api/reinfolib/population")
async def reinfolib_population(
    south: float = None, west: float = None,
    north: float = None, east: float = None,
    lat: float = None, lng: float = None,
    zoom: int = 13, force_fetch: bool = False,
):
    """人口メッシュ取得: bounds指定→複数タイル→DB保存→GeoJSON"""
    if south is None and lat is not None:
        south, west = lat - 0.05, lng - 0.08
        north, east = lat + 0.05, lng + 0.08
    if south is None:
        return JSONResponse(content={"error": "bounds or lat/lng required"}, status_code=400)

    # DB既存
    if not force_fetch:
        cached = db.get_api_population_mesh(south, west, north, east)
        if cached:
            features = []
            for r in cached:
                geom = None
                if r.get("geometry_json"):
                    try:
                        geom = json.loads(r["geometry_json"])
                    except Exception:
                        pass
                if not geom:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "mesh_id": r.get("mesh_id", ""),
                        "pop_current": r.get("pop_current"),
                        "pop_future": r.get("pop_future"),
                        "change_rate": r.get("change_rate"),
                        "layer": "population_mesh",
                    },
                })
            if features:
                return JSONResponse(content={
                    "geojson": {"type": "FeatureCollection", "features": features},
                    "count": len(features), "source": "db",
                })

    from data.reinfolib_client import ReinfolibClient
    client = ReinfolibClient()
    if not client.is_configured():
        return JSONResponse(content={"error": "APIキー未設定", "geojson": None}, status_code=400)

    try:
        meshes = client.get_population_mesh_bounds(south, west, north, east, zoom=zoom)
        features = []
        db_records = []
        for m in meshes:
            geom = m.pop("_geometry", None)
            if not geom:
                continue
            pop_current = m.get("PTN_2020") or m.get("PTN_2025")
            pop_future = m.get("PTN_2050") or m.get("PTN_2045") or m.get("PTN_2040")
            mesh_id = m.get("MESH_ID", "")
            change_rate = None
            if pop_current and pop_future and pop_current > 0:
                change_rate = round((pop_future - pop_current) / pop_current * 100, 1)

            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "mesh_id": mesh_id,
                    "pop_current": pop_current,
                    "pop_future": pop_future,
                    "change_rate": change_rate,
                    "layer": "population_mesh",
                },
            })
            db_records.append({
                "mesh_id": mesh_id,
                "pop_current": pop_current,
                "pop_future": pop_future,
                "change_rate": change_rate,
                "geometry_json": json.dumps(geom),
            })

        # DB保存
        if db_records:
            saved = db.upsert_api_population_mesh(db_records)
            logging.info(f"人口メッシュDB保存: {saved}件")

        geojson = {"type": "FeatureCollection", "features": features}
        return JSONResponse(content={"geojson": geojson, "count": len(features), "source": "api"})
    except Exception as e:
        logging.error(f"XKT013エラー: {e}")
        return JSONResponse(content={"error": str(e), "geojson": None}, status_code=500)


@app.get("/api/reinfolib/did")
async def reinfolib_did(lat: float, lng: float, zoom: int = 13):
    """人口集中地区 DID (XKT031) → GeoJSON"""
    from data.reinfolib_client import ReinfolibClient
    client = ReinfolibClient()
    if not client.is_configured():
        return JSONResponse(content={"error": "APIキー未設定", "geojson": None}, status_code=400)

    try:
        areas = client.get_did_area(lat, lng, zoom=zoom)
        features = []
        for a in areas:
            geom = a.pop("_geometry", None)
            if not geom:
                continue
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "municipality": a.get("A16_003", ""),
                    "population": a.get("A16_005"),
                    "area_km2": a.get("A16_006"),
                    "households": a.get("A16_014"),
                    "pop_ratio": a.get("A16_009"),
                    "layer": "did",
                },
            })
        geojson = {"type": "FeatureCollection", "features": features}
        return JSONResponse(content={"geojson": geojson, "count": len(features)})
    except Exception as e:
        return JSONResponse(content={"error": str(e), "geojson": None}, status_code=500)


@app.get("/api/reinfolib/facilities")
async def reinfolib_facilities(
    south: float = None, west: float = None,
    north: float = None, east: float = None,
    lat: float = None, lng: float = None,
    types: str = "school,medical,childcare",
    zoom: int = 14,
):
    """周辺施設 → DB優先、なければAPIフォールバック"""
    if south is None and lat is not None:
        south, west = lat - 0.03, lng - 0.05
        north, east = lat + 0.03, lng + 0.05
    if south is None:
        return JSONResponse(content={"error": "bounds or lat/lng required"}, status_code=400)

    requested = set(types.split(","))
    features = []

    # DB優先
    for cat in requested:
        cached = db.get_api_facilities(south, west, north, east, category=cat, limit=3000)
        for r in cached:
            if not r.get("latitude") or not r.get("longitude"):
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
                "properties": {
                    "name": r.get("name", ""),
                    "address": r.get("address", ""),
                    "category": cat,
                    "layer": "facility",
                },
            })

    if features:
        geojson = {"type": "FeatureCollection", "features": features}
        return JSONResponse(content={"geojson": geojson, "count": len(features), "source": "db"})

    # APIフォールバック
    from data.reinfolib_client import ReinfolibClient
    client = ReinfolibClient()
    if not client.is_configured():
        return JSONResponse(content={"geojson": {"type": "FeatureCollection", "features": []}, "count": 0})

    ep_map = {"school": "XKT006", "medical": "XKT010", "childcare": "XKT007", "shelter": "XGT001"}
    nk = {
        "XKT006": ["P29_004_ja", "P29_004"], "XKT010": ["name_ja", "P04_002_ja"],
        "XKT007": ["preSchoolName_ja", "name_ja"], "XGT001": ["name_ja", "facility_name_ja"],
    }
    ak = {
        "XKT006": ["P29_005_ja"], "XKT010": ["address_ja", "P04_003_ja"],
        "XKT007": ["location_ja"], "XGT001": ["address_ja"],
    }
    db_records = []
    try:
        center_lat = (south + north) / 2
        center_lng = (west + east) / 2
        for cat in requested:
            ep = ep_map.get(cat)
            if not ep:
                continue
            items = client._facility_request(ep, center_lat, center_lng, zoom=zoom)
            for item in items:
                plat = item.get("_lat")
                plng = item.get("_lng")
                if plat is None or plng is None:
                    continue
                name = next((str(item[k]) for k in nk.get(ep, []) if item.get(k)), "")
                addr = next((str(item[k]) for k in ak.get(ep, []) if item.get(k)), "")
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [plng, plat]},
                    "properties": {"name": name, "address": addr, "category": cat, "layer": "facility"},
                })
                db_records.append({"name": name, "category": cat, "address": addr,
                                   "latitude": plat, "longitude": plng, "extra_json": None})
        if db_records:
            db.upsert_api_facilities(db_records)

        geojson = {"type": "FeatureCollection", "features": features}
        return JSONResponse(content={"geojson": geojson, "count": len(features), "source": "api"})
    except Exception as e:
        logging.error(f"施設APIエラー: {e}")
        return JSONResponse(content={"error": str(e), "geojson": None}, status_code=500)


@app.post("/api/reinfolib/enrich-listing/{listing_id}")
async def reinfolib_enrich_single(listing_id: int):
    """個別土地物件にAPI情報を補完"""
    from data.reinfolib_client import ReinfolibClient
    client = ReinfolibClient()
    if not client.is_configured():
        return JSONResponse(content={"error": "APIキー未設定"}, status_code=400)

    with db._conn() as conn:
        row = conn.execute(
            "SELECT id, latitude, longitude, zoning FROM land_listings WHERE id=?",
            (listing_id,),
        ).fetchone()

    if not row:
        return JSONResponse(content={"error": "物件が見つかりません"}, status_code=404)

    row = dict(row)
    if not row.get("latitude") or not row.get("longitude"):
        return JSONResponse(content={"error": "座標が未設定です"}, status_code=400)

    try:
        data = client.enrich_land_listing(row["latitude"], row["longitude"])

        updates = {}
        if data.get("zoning") and not row.get("zoning"):
            updates["zoning"] = data["zoning"]
        if data.get("building_coverage_ratio"):
            try:
                raw = str(data["building_coverage_ratio"]).replace("%", "").strip()
                val = float(raw)
                updates["building_coverage_ratio"] = val / 100 if val > 1 else val
            except (ValueError, TypeError):
                pass
        if data.get("floor_area_ratio"):
            try:
                raw = str(data["floor_area_ratio"]).replace("%", "").strip()
                val = float(raw)
                updates["floor_area_ratio"] = val / 100 if val > 1 else val
            except (ValueError, TypeError):
                pass
        if "quasi_fireproof" in data:
            updates["quasi_fireproof"] = 1 if data["quasi_fireproof"] else 0

        if updates:
            set_clause = ", ".join(f"{k}=?" for k in updates)
            params = list(updates.values()) + [listing_id]
            with db._conn() as conn:
                conn.execute(
                    f"UPDATE land_listings SET {set_clause}, updated_at=datetime('now','localtime') WHERE id=?",
                    params,
                )

        return JSONResponse(content={
            "status": "ok",
            "enriched_fields": list(updates.keys()),
            "raw_data": data,
        })
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/reinfolib/batch-ingest")
async def reinfolib_batch_ingest(prefectures: str = "13,14,11,12"):
    """一都三県の公示地価+人口メッシュを全市区町村一括取得→DB保存（バックグラウンド）"""
    import threading
    pref_list = [p.strip() for p in prefectures.split(",")]

    def _run():
        from data.reinfolib_client import ReinfolibClient
        client = ReinfolibClient()
        if not client.is_configured():
            logging.warning("APIキー未設定")
            return

        logging.info(f"=== 一括DB取込開始: {pref_list} ===")
        total_lp = 0
        total_pop = 0

        for pref in pref_list:
            centers = client._get_city_centers(pref)
            pref_centers = {k: v for k, v in centers.items() if k.startswith(pref)}
            logging.info(f"  pref={pref}: {len(pref_centers)}市区町村")

            for code, (lat, lng) in pref_centers.items():
                # 公示地価 (XPT002) zoom=13
                try:
                    points = client.get_official_land_prices(lat, lng, zoom=13)
                    db_records = []
                    for p in points:
                        parsed = _parse_xpt002_point(p)
                        if not parsed:
                            continue
                        db_records.append({
                            "point_id": parsed["point_id"],
                            "place_name": parsed["place_name"],
                            "price_per_sqm": parsed["price_num"],
                            "year": datetime.now().year - 1,
                            "latitude": parsed["latitude"],
                            "longitude": parsed["longitude"],
                            "zoning": parsed["zoning"],
                            "station": parsed["station"],
                            "change_rate": parsed["change_rate"],
                            "coverage": parsed["coverage"],
                            "far": parsed["far"],
                            "fire_prevention": parsed["fire_prevention"],
                            "land_price_type": parsed.get("land_price_type", 0),
                            "prefecture_code": pref,
                            "city_code": code,
                        })
                    if db_records:
                        saved = db.upsert_api_land_prices(db_records)
                        total_lp += saved
                except Exception as e:
                    logging.debug(f"  XPT002 skip {code}: {e}")

                # 人口メッシュ (XKT013) zoom=13
                try:
                    meshes = client.get_population_mesh(lat, lng, zoom=13)
                    pop_records = []
                    for m in meshes:
                        geom = m.pop("_geometry", None)
                        if not geom:
                            continue
                        pop_current = m.get("PTN_2020") or m.get("PTN_2025")
                        pop_future = m.get("PTN_2050") or m.get("PTN_2045") or m.get("PTN_2040")
                        mesh_id = m.get("MESH_ID", "")
                        change_rate = None
                        if pop_current and pop_future and pop_current > 0:
                            change_rate = round((pop_future - pop_current) / pop_current * 100, 1)
                        pop_records.append({
                            "mesh_id": mesh_id,
                            "pop_current": pop_current,
                            "pop_future": pop_future,
                            "change_rate": change_rate,
                            "geometry_json": json.dumps(geom),
                        })
                    if pop_records:
                        saved = db.upsert_api_population_mesh(pop_records)
                        total_pop += saved
                except Exception as e:
                    logging.debug(f"  XKT013 skip {code}: {e}")

            logging.info(f"  pref={pref} 完了: 地価{total_lp}件, 人口{total_pop}件 (累計)")

        logging.info(f"=== 一括DB取込完了: 地価{total_lp}件, 人口{total_pop}件 ===")

    threading.Thread(target=_run, daemon=True).start()

    from data.reinfolib_client import ReinfolibClient
    client = ReinfolibClient()
    centers = {}
    for p in pref_list:
        c = client._get_city_centers(p)
        centers.update({k: v for k, v in c.items() if k.startswith(p)})

    return JSONResponse(content={
        "status": "ok",
        "message": f"一括取込開始: {len(centers)}市区町村 × 地価+人口",
        "city_count": len(centers),
        "prefectures": pref_list,
    })


# スケジューラ自動起動
from engine.scheduler import scheduler as _scheduler
_scheduler.start()

if __name__ == "__main__":
    import uvicorn
    land_routes = [r for r in app.routes if hasattr(r, 'path') and 'land' in r.path]
    logging.info(f"土地API: {len(land_routes)}エンドポイント登録済")
    stats = db.get_db_stats()
    logging.info(f"DB: land_listings={stats.get('land_listings',0)}, building_plans={stats.get('building_plans',0)}")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)
