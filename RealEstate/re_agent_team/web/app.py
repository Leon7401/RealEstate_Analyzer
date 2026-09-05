"""FastAPI Webアプリケーション - 地図UI + API"""
import sys
import os
import re
import json
import math
import hashlib
import logging
import statistics
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, Any, Dict, List, Tuple

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
from services.geo_quality import GeoQualityService
from ingest.pipeline import IngestPipeline
from ingest.adapters.registry import register_default_adapters
from contracts import GRADE_PALETTE, grade_color, normalize_grade
from web.routers import ingest_router, listings_router, analysis_router, map_router
from models.property import Property
from models.land_listing import LandListing
from storage.report_store import ReportStore
from storage.database import Database
from data.city_master import CITY_MASTER, CITY_NAME_MAP
from data.station_master import (
    STATIONS,
    STATION_MAP,
    get_stations_by_prefecture,
    resolve_station_id,
)
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
    ANALYZE_AUTOFILL_LAND_PRICE_FACTOR,
    ANALYZE_AUTOFILL_RENT_BASE_FACTOR,
    ANALYZE_AUTOFILL_RENT_MIN_FACTOR,
    ANALYZE_AUTOFILL_RENT_MAX_FACTOR,
    ANALYZE_AUTOFILL_RENT_NEAR_BONUS,
    ANALYZE_AUTOFILL_RENT_FAR_PENALTY,
    VACANCY_RATE,
    MANAGEMENT_FEE_RATE,
    REPAIR_RESERVE_RATE,
    INSURANCE_RATE,
    PROPERTY_TAX_RATE,
    CITY_PLANNING_TAX_RATE,
    LISTING_VERIFY_BATCH,
    LISTING_VERIFY_STALE_HOURS,
    LISTING_VERIFY_CONFIRM_FAILURES,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(title="不動産投資判定システム", version="2.0.0")
app.include_router(ingest_router)
app.include_router(listings_router)
app.include_router(analysis_router)
app.include_router(map_router)

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
geo_service = GeoQualityService(db=db)
_source_adapters = register_default_adapters(scraper_agent, url_scraper)
ingest_pipeline = IngestPipeline(
    db,
    adapters=_source_adapters,
    geo_service=geo_service,
    orchestrator=orchestrator,
    diversify_fn=None,  # wired after _spatial_diversify_ranking is defined
)
batch_processor = BatchProcessor()
area_analyzer = AreaAnalyzer()
asset_score_agent = AssetScoreAgent()
maisoku_agent = MaisokuAgent()

# バックグラウンドタスク状態管理
_bg_task_status = {"running": False, "step": "", "result": None, "error": None}
METRO_PREFECTURE_CODES: Tuple[str, ...] = ("13", "14", "11", "12")
PREF_LABELS: Dict[str, str] = {
    "13": "東京都",
    "14": "神奈川県",
    "11": "埼玉県",
    "12": "千葉県",
}

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


def _expand_prefecture_codes(prefecture_code: str) -> List[str]:
    pref = str(prefecture_code or "").strip()
    if pref in ("metro", "1tokyo3", "13,14,11,12", "all_kanto"):
        return list(METRO_PREFECTURE_CODES)
    if "," in pref:
        vals = [x.strip() for x in pref.split(",") if x.strip()]
        if not vals:
            return []
        expanded: List[str] = []
        for v in vals:
            if v in ("metro", "1tokyo3", "all_kanto"):
                expanded.extend(METRO_PREFECTURE_CODES)
            else:
                expanded.append(v)
        # 順序維持で重複除去
        uniq: List[str] = []
        seen = set()
        for v in expanded:
            if v in seen:
                continue
            seen.add(v)
            uniq.append(v)
        return uniq
    return [pref] if pref else []


def _pref_where_clause(column: str, pref_codes: List[str]) -> Tuple[str, List[str]]:
    if not pref_codes:
        return f"{column} <> ''", []
    if len(pref_codes) == 1:
        return f"{column} = ?", [pref_codes[0]]
    placeholders = ",".join(["?"] * len(pref_codes))
    return f"{column} IN ({placeholders})", list(pref_codes)


def _dedupe_properties_for_display(props: List[dict]) -> List[dict]:
    """表示用に重複候補を除外（DB側統合の取りこぼし対策）"""
    if not props:
        return props

    def _norm_text(v: Any) -> str:
        s = str(v or "").strip().replace("　", " ")
        return re.sub(r"\s+", "", s)

    def _norm_addr(v: Any) -> str:
        s = _norm_text(v)
        s = s.replace("丁目", "-").replace("番地", "-").replace("番", "-").replace("号", "")
        return re.sub(r"-{2,}", "-", s).strip("-")

    def _norm_name(v: Any) -> str:
        s = _norm_text(v)
        s = re.sub(r"(new|NEW|新着|登録|更新|価格改定|値下げ|限定|公開).*", "", s)
        s = re.sub(r"(第?\d+号棟|[A-Z]棟|[A-Z]号棟)", "", s)
        return s.strip("-_")

    seen = set()
    unique_rows: List[dict] = []
    rows = sorted(
        list(props),
        key=lambda x: (
            sum(
                1
                for c in (
                    "source_url", "address", "asking_price", "land_area", "building_area",
                    "nearest_station", "latitude", "longitude",
                )
                if x.get(c) not in (None, "", 0)
            ),
            x.get("updated_at") or x.get("fetched_at") or "",
        ),
        reverse=True,
    )

    for p in rows:
        keys = []
        source_url = db._normalize_source_url(p.get("source_url"))
        if source_url:
            keys.append(f"url:{source_url}")

        address = _norm_addr(p.get("address"))
        station = _norm_text(p.get("nearest_station")).replace("駅", "")
        name = _norm_name(p.get("name"))
        pref = str(p.get("prefecture_code") or "")
        try:
            price = int(float(p.get("asking_price"))) if p.get("asking_price") is not None else None
        except (TypeError, ValueError):
            price = None
        try:
            land = round(float(p.get("land_area")), 1) if p.get("land_area") else None
        except (TypeError, ValueError):
            land = None
        try:
            bld = round(float(p.get("building_area")), 1) if p.get("building_area") else None
        except (TypeError, ValueError):
            bld = None

        if address and price is not None and (land is not None or bld is not None):
            keys.append(f"ap:{pref}|{address}|{price}|{land}|{bld}|{station}")

        try:
            lat = float(p.get("latitude")) if p.get("latitude") is not None else None
            lng = float(p.get("longitude")) if p.get("longitude") is not None else None
        except (TypeError, ValueError):
            lat = lng = None
        if lat is not None and lng is not None and price is not None and bld is not None:
            keys.append(f"geo:{round(lat, 4)}|{round(lng, 4)}|{price}|{bld}")
        if lat is not None and lng is not None and address:
            # ソース差異を跨いだ同一地点重複を抑制
            keys.append(f"addrgeo:{pref}|{address}|{round(lat,4)}|{round(lng,4)}")
        if lat is not None and lng is not None and price is not None:
            # 同座標・同価格帯（10万円単位）を同一候補として扱う
            keys.append(f"gprice:{pref}|{round(lat,4)}|{round(lng,4)}|{int(price/100000)}")
        if ("新築プラン" in str(p.get("name") or "") or "[土地]" in str(p.get("name") or "")) and address and land is not None:
            # 生成系（新築プラン/土地統合）の同住所・同面積重複を抑制
            keys.append(f"gen_addr_land:{pref}|{address}|{land}")
        if lat is not None and lng is not None and price is not None and station:
            dist = p.get("station_distance_min")
            try:
                dist_bucket = int(float(dist) / 2) if dist is not None else -1
            except (TypeError, ValueError):
                dist_bucket = -1
            keys.append(f"sgx:{pref}|{station}|{int(price/500000)}|{round(lat,3)}|{round(lng,3)}|{dist_bucket}")
        if name and station and price is not None:
            keys.append(f"snp:{pref}|{station}|{int(price/500000)}|{name[:24]}")

        if not keys:
            unique_rows.append(p)
            continue
        if any(k in seen for k in keys):
            continue
        unique_rows.append(p)
        seen.update(keys)

    return unique_rows


def _sanitize_station_refs_for_display(props: List[dict], persist_updates: bool = True):
    """表示前に最寄駅の整合性を補正（住所/座標と矛盾する駅名を修正）"""
    if not props:
        return
    try:
        from data.station_master import resolve_station_id, STATION_MAP, find_nearest_station
    except Exception:
        return

    def _pref_from_address(addr: str) -> str:
        a = str(addr or "")
        if "東京都" in a:
            return "13"
        if "神奈川県" in a:
            return "14"
        if "埼玉県" in a:
            return "11"
        if "千葉県" in a:
            return "12"
        return ""

    def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
        r = 6371.0
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lng2 - lng1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))

    prop_updates: List[tuple] = []
    land_updates: List[tuple] = []

    for p in props:
        try:
            lat = float(p.get("latitude")) if p.get("latitude") is not None else None
            lng = float(p.get("longitude")) if p.get("longitude") is not None else None
        except (TypeError, ValueError):
            lat = lng = None
        if lat is None or lng is None:
            continue

        pref = str(p.get("prefecture_code") or "").strip() or _pref_from_address(p.get("address") or "")
        st_name = str(p.get("nearest_station") or "").strip()
        sid = resolve_station_id(st_name, lat=lat, lon=lng, pref_code=pref or None)

        suspicious = False
        if sid and sid in STATION_MAP:
            s = STATION_MAP[sid]
            if pref and str(s.get("pref") or "") and str(s.get("pref")) != pref:
                suspicious = True
            try:
                dkm = _haversine_km(lat, lng, float(s["lat"]), float(s["lon"]))
            except Exception:
                dkm = None
            walk = p.get("station_distance_min")
            try:
                walk = float(walk) if walk is not None else None
            except (TypeError, ValueError):
                walk = None
            if dkm is not None:
                if walk and walk > 0:
                    expected = max(0.08 * walk, 0.2)
                    if dkm > max(2.0, expected * 4.0):
                        suspicious = True
                elif dkm > 8.0:
                    suspicious = True
        else:
            suspicious = bool(st_name)

        if not suspicious:
            continue

        near = find_nearest_station(lat, lng, max_distance_km=8.0, pref_code=pref or None)
        # 駅マスタの都県コードが実地とズレるケースを救済（例: 八王子周辺）
        if (not near) or float(near.get("distance_km") or 999.0) > 20.0:
            near_any = find_nearest_station(lat, lng, max_distance_km=8.0, pref_code=None)
            if near_any:
                near = near_any
        if not near:
            continue
        sid2 = near.get("station_id")
        sname2 = near.get("name")
        dkm2 = float(near.get("distance_km") or 0.0)
        walk2 = max(1, min(120, int(round(dkm2 * 12.5)))) if dkm2 > 0 else (p.get("station_distance_min") or None)

        p["station_id"] = sid2
        p["nearest_station"] = sname2
        p["station_distance_min"] = walk2

        pid = p.get("id")
        if pid:
            prop_updates.append((sid2, sname2, walk2, str(pid)))
        lid = p.get("_land_listing_id")
        if lid:
            try:
                land_updates.append((sname2, walk2, int(lid)))
            except Exception:
                pass

    if persist_updates and (prop_updates or land_updates):
        with db._conn() as conn:
            if prop_updates:
                conn.executemany(
                    "UPDATE properties SET station_id=?, nearest_station=?, station_distance_min=?, updated_at=datetime('now','localtime') WHERE id=?",
                    prop_updates,
                )
            if land_updates:
                conn.executemany(
                    "UPDATE land_listings SET station=?, walk_minutes=?, updated_at=datetime('now','localtime') WHERE id=?",
                    land_updates,
                )


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _is_station_address_consistent(raw: Dict[str, Any]) -> bool:
    """住所/座標/最寄駅の整合性をざっくり判定"""
    try:
        lat = float(raw.get("latitude")) if raw.get("latitude") is not None else None
        lng = float(raw.get("longitude")) if raw.get("longitude") is not None else None
    except (TypeError, ValueError):
        lat = lng = None
    if lat is None or lng is None:
        return False

    sid = resolve_station_id(
        nearest_station_text=raw.get("nearest_station"),
        lat=lat,
        lon=lng,
        pref_code=raw.get("prefecture_code"),
    )
    if not sid or sid not in STATION_MAP:
        return False

    s = STATION_MAP[sid]
    dkm = _haversine_km(lat, lng, float(s["lat"]), float(s["lon"]))
    walk = raw.get("station_distance_min")
    try:
        walk = float(walk) if walk is not None else None
    except (TypeError, ValueError):
        walk = None
    if walk and walk > 0:
        expected = max(0.08 * walk, 0.2)
        return dkm <= max(2.0, expected * 4.0)
    return dkm <= 8.0


def _refresh_property_from_source(raw: Dict[str, Any], use_browser: bool = False) -> Dict[str, Any]:
    """
    source_url を再スクレイプし、住所/駅/座標が明確に改善する場合のみ上書きする。
    """
    src = str(raw.get("source_url") or "").strip()
    if not src:
        return {"updated": False, "reason": "no_source_url"}
    try:
        scraped = url_scraper.run(url=src, use_ocr=True, use_browser=use_browser)
    except Exception as e:
        return {"updated": False, "reason": f"scrape_error:{e}"}
    if not scraped:
        return {"updated": False, "reason": "scrape_none"}

    sdict = scraped.to_dict()
    if not str(sdict.get("address") or "").strip() and not str(sdict.get("nearest_station") or "").strip():
        return {"updated": False, "reason": "scrape_low_quality"}

    merged = dict(raw)
    changed_fields: List[str] = []
    overwrite_fields = [
        "name",
        "address",
        "prefecture_code",
        "city_code",
        "nearest_station",
        "station_distance_min",
        "latitude",
        "longitude",
        "asking_price",
        "land_area",
        "building_area",
        "structure",
        "built_year",
        "building_age",
        "gross_yield",
        "current_rent_annual",
    ]
    for k in overwrite_fields:
        nv = sdict.get(k)
        if nv in (None, ""):
            continue
        ov = merged.get(k)
        if ov != nv:
            merged[k] = nv
            changed_fields.append(k)

    # DB正規化ロジックに通す（station_id再解決など）
    try:
        db.upsert_property(merged)
    except Exception as e:
        return {"updated": False, "reason": f"upsert_error:{e}"}

    # 最新行を再取得
    latest = dict(merged)
    try:
        with db._conn() as conn:
            row = None
            if merged.get("id"):
                row = conn.execute("SELECT * FROM properties WHERE id=? LIMIT 1", (str(merged["id"]),)).fetchone()
            if not row and merged.get("source_url"):
                row = conn.execute(
                    "SELECT * FROM properties WHERE source_url=? ORDER BY updated_at DESC LIMIT 1",
                    (db._normalize_source_url(merged.get("source_url")),),
                ).fetchone()
            if row:
                latest = dict(row)
    except Exception:
        pass
    return {
        "updated": bool(changed_fields),
        "reason": "ok",
        "changed_fields": changed_fields,
        "property": latest,
    }


def _check_source_alive(url: str) -> Tuple[bool, Optional[int], str]:
    if not url:
        return False, None, "no_url"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.head(url, allow_redirects=True, timeout=12, headers=headers)
        status = int(resp.status_code)
        if status in (403, 405):
            resp = requests.get(url, allow_redirects=True, timeout=15, headers=headers, stream=True)
            status = int(resp.status_code)
        return (200 <= status < 400), status, ""
    except Exception as e:
        return False, None, str(e)


# ===== ページ =====

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    import hashlib, time
    cache_bust = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "center": MAP_DEFAULT_CENTER,
            "zoom": MAP_DEFAULT_ZOOM,
            "api_configured": bool(REINFOLIB_API_KEY),
            "cache_bust": cache_bust,
        },
    )


@app.get("/healthz")
async def healthz():
    """クラウド監視用の簡易ヘルスチェック"""
    return JSONResponse(content={"status": "ok"})


# ===== マスタデータAPI =====

@app.post("/api/map/revalidate-coords")
async def revalidate_coords(request: Request):
    """既存DB座標の一括再検証（都県境界チェック・住所ジオコード優先）"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    limit = int(data.get("limit", 2000) or 2000)
    geocode_budget = int(data.get("geocode_budget", 200) or 200)
    dry_run = bool(data.get("dry_run", False))
    prefs = data.get("prefecture_codes") or data.get("prefectures")
    if isinstance(prefs, str):
        prefs = [p.strip() for p in prefs.split(",") if p.strip()]
    result = geo_service.revalidate_all(
        limit=limit,
        geocode_budget=geocode_budget,
        dry_run=dry_run,
        prefecture_codes=prefs,
    )
    return JSONResponse(content=result)


@app.get("/api/contracts/grade-palette")
async def get_grade_palette():
    """投資/資産グレード色の単一ソース"""
    from contracts import ASSET_GRADE_PALETTE
    return JSONResponse(content={
        "investment": GRADE_PALETTE,
        "asset": ASSET_GRADE_PALETTE,
    })



@app.get("/api/cities/{prefecture_code}")
async def get_cities(prefecture_code: str):
    """市区町村一覧"""
    pref_codes = _expand_prefecture_codes(prefecture_code)
    if len(pref_codes) <= 1:
        key = pref_codes[0] if pref_codes else prefecture_code
        cities = CITY_MASTER.get(key, [{"code": "", "name": "全域"}])
    else:
        cities = [{"code": "", "name": "全域(一都三県)"}]
        for pref in pref_codes:
            pref_name = PREF_LABELS.get(pref, pref)
            for c in CITY_MASTER.get(pref, []):
                code = str(c.get("code") or "").strip()
                if not code:
                    continue
                cities.append({"code": code, "name": f"{pref_name} {c.get('name', code)}"})
    return JSONResponse(content={"cities": cities})


@app.get("/api/stations/{prefecture_code}")
async def get_stations(prefecture_code: str):
    """駅一覧（メトリクス付き）"""
    pref_codes = _expand_prefecture_codes(prefecture_code)
    if len(pref_codes) <= 1:
        key = pref_codes[0] if pref_codes else prefecture_code
        stations = get_stations_by_prefecture(key)
        metrics = db.get_station_metrics(prefecture_code=key)
    else:
        stations = []
        metrics = []
        for pref in pref_codes:
            stations.extend(get_stations_by_prefecture(pref))
            metrics.extend(db.get_station_metrics(prefecture_code=pref))
    metrics_map = {m["station_id"]: m for m in metrics}

    result = []
    seen_station_ids = set()
    for s in stations:
        sid = s["station_id"]
        if sid in seen_station_ids:
            continue
        seen_station_ids.add(sid)
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
    include_delisted: bool = False,
    sort_by: str = "updated_at",
    prefecture_code: str = "metro",
    station_filter: str = "",
    min_price: int = None,
    max_price: int = None,
    min_yield: float = None,
):
    """DB物件 + 土地物件（必要時のみ静的サンプルを補完）"""
    props = []
    # DB properties (scraped etc)
    db_props = db.get_properties(limit=1000, active_only=not include_delisted)
    # DBが空の時だけ静的サンプルを補完（古いサンプル混入を防止）
    if not db_props:
        sample_file = DATA_DIR / "sample_properties.json"
        if sample_file.exists():
            with open(sample_file, "r", encoding="utf-8") as f:
                props = json.load(f)
            for p in props:
                p.setdefault("_type", "property")
                p["_building_presence"] = "building" if (p.get("building_area") or p.get("structure")) else "unknown"
    for p in db_props:
        p.pop("data_json", None)
        p["_type"] = "property"
        p["_building_presence"] = "building" if (p.get("building_area") or p.get("structure")) else "unknown"
        props.append(p)

    # 土地物件+ベストプラン統合
    if include_land:
        land_rows = db.get_land_listings(limit=1000, active_only=not include_delisted)
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
                "listing_status": ll.get("listing_status") or "active",
                "_type": "land",
                "_building_presence": "land_only",
                "_land_listing_id": ll.get("id"),
                "_land_price": ll.get("land_price"),
            }
            props.append(prop)

    pref_codes = _expand_prefecture_codes(prefecture_code)
    if pref_codes:
        def _pref_of(p: dict) -> str:
            pref = str(p.get("prefecture_code") or "").strip()
            if pref:
                return pref
            addr = str(p.get("address") or "")
            if "東京都" in addr:
                return "13"
            if "神奈川県" in addr:
                return "14"
            if "埼玉県" in addr:
                return "11"
            if "千葉県" in addr:
                return "12"
            return ""

        props = [p for p in props if _pref_of(p) in pref_codes]

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

    # 表示APIではDB更新を伴う補正を避け、読み取り専用で軽量に座標/駅を補完
    _estimate_missing_coords(props, persist_updates=False, geocode_budget=20)
    _sanitize_station_refs_for_display(props, persist_updates=False)
    # 補正後に重複除外
    props = _dedupe_properties_for_display(props)
    return JSONResponse(content={"properties": props, "total": len(props)})


def _estimate_missing_coords(props: list, persist_updates: bool = True, geocode_budget: int = 120):
    """座標なし/矛盾座標の物件を補正（GeoQualityService 委譲）"""
    return geo_service.estimate_missing_coords(
        props,
        persist_updates=persist_updates,
        geocode_budget=geocode_budget,
    )



def _load_market_context(lat: float, lng: float) -> Optional[dict]:
    """座標近傍のメッシュ/駅データから市場コンテキストを取得"""
    def _is_valid_land_price(v) -> bool:
        return v is not None and 50_000 <= float(v) <= 5_000_000

    def _is_valid_rent(v) -> bool:
        return v is not None and 800 <= float(v) <= 20_000

    with db._conn() as conn:
        # まず250mメッシュを最優先（ヒートマップと同一ソース）
        mesh = conn.execute("""
            SELECT mesh_id, center_lat, center_lng,
                   avg_land_price_sqm, land_price_count,
                   avg_rent_sqm, rent_count,
                   avg_tx_price_sqm, tx_count,
                   nearest_station, station_dist_km,
                   ((center_lat-?)*(center_lat-?) + (center_lng-?)*(center_lng-?)) AS d2
            FROM mesh_250m
            WHERE center_lat BETWEEN ? AND ?
              AND center_lng BETWEEN ? AND ?
              AND (avg_land_price_sqm IS NOT NULL
                   OR avg_rent_sqm IS NOT NULL
                   OR avg_tx_price_sqm IS NOT NULL)
            ORDER BY d2 ASC
            LIMIT 1
        """, (
            lat, lat, lng, lng,
            lat - 0.03, lat + 0.03,
            lng - 0.03, lng + 0.03,
        )).fetchone()

        if mesh:
            m = dict(mesh)
            mesh_lp = m.get("avg_land_price_sqm")
            mesh_rent = m.get("avg_rent_sqm")
            ctx = {
                "source": "mesh_250m",
                "mesh_id": m.get("mesh_id"),
                "land_price_sqm": mesh_lp if _is_valid_land_price(mesh_lp) else None,
                "rent_sqm": mesh_rent if _is_valid_rent(mesh_rent) else None,
                "tx_price_sqm": m.get("avg_tx_price_sqm"),
                "nearest_station": m.get("nearest_station"),
                "station_dist_km": m.get("station_dist_km"),
                "sample_counts": {
                    "land_price": m.get("land_price_count") or 0,
                    "rent": m.get("rent_count") or 0,
                    "transactions": m.get("tx_count") or 0,
                },
            }
        else:
            ctx = None

        # フォールバック: station_metrics
        sm = conn.execute("""
            SELECT station_name, line_name, center_lat, center_lng,
                   avg_land_price_sqm, avg_rent_per_sqm, implied_yield,
                   sample_count_land, sample_count_rent, sample_count_tx,
                   ((center_lat-?)*(center_lat-?) + (center_lng-?)*(center_lng-?)) AS d2
            FROM station_metrics
            WHERE center_lat IS NOT NULL AND center_lng IS NOT NULL
            ORDER BY d2 ASC
            LIMIT 1
        """, (lat, lat, lng, lng)).fetchone()

    # ===== 欠損メトリクスの追加補完（地価カバレッジ強化） =====
    # api_land_prices / land_prices / transactions / rental_comps から順次補完
    with db._conn() as conn:
        if not ctx:
            ctx = {
                "source": "fallback",
                "mesh_id": None,
                "land_price_sqm": None,
                "rent_sqm": None,
                "tx_price_sqm": None,
                "nearest_station": None,
                "station_dist_km": None,
                "sample_counts": {"land_price": 0, "rent": 0, "transactions": 0},
            }

        # nearest station は station_metrics から先に補完
        if sm and not ctx.get("nearest_station"):
            s = dict(sm)
            ctx["station_name"] = s.get("station_name")
            ctx["line_name"] = s.get("line_name")
            ctx["nearest_station"] = s.get("station_name")
            if not ctx.get("land_price_sqm"):
                ctx["land_price_sqm"] = s.get("avg_land_price_sqm")
            if not ctx.get("rent_sqm"):
                ctx["rent_sqm"] = s.get("avg_rent_per_sqm")
            if not ctx.get("implied_yield"):
                ctx["implied_yield"] = s.get("implied_yield")
                if not _is_valid_land_price(ctx.get("land_price_sqm")):
                    ctx["land_price_sqm"] = None
                if not _is_valid_rent(ctx.get("rent_sqm")):
                    ctx["rent_sqm"] = None

                ctx["sample_counts"]["land_price"] = max(
                ctx["sample_counts"].get("land_price", 0),
                s.get("sample_count_land") or 0,
            )
            ctx["sample_counts"]["rent"] = max(
                ctx["sample_counts"].get("rent", 0),
                s.get("sample_count_rent") or 0,
            )
            ctx["sample_counts"]["transactions"] = max(
                ctx["sample_counts"].get("transactions", 0),
                s.get("sample_count_tx") or 0,
            )

        # 地価(1): api_land_prices 近傍中央値
        if not ctx.get("land_price_sqm"):
            rows = conn.execute("""
                SELECT price_per_sqm
                FROM api_land_prices
                WHERE latitude BETWEEN ? AND ?
                  AND longitude BETWEEN ? AND ?
                  AND price_per_sqm > 0
                LIMIT 300
            """, (lat - 0.03, lat + 0.03, lng - 0.03, lng + 0.03)).fetchall()
            vals = [dict(r).get("price_per_sqm") for r in rows if dict(r).get("price_per_sqm")]
            vals = [v for v in vals if _is_valid_land_price(v)]
            if vals:
                ctx["land_price_sqm"] = float(statistics.median(vals))
                ctx["sample_counts"]["land_price"] = max(ctx["sample_counts"].get("land_price", 0), len(vals))
                ctx["source"] = "api_land_prices"

        # 地価(2): land_prices 近傍中央値
        if not ctx.get("land_price_sqm"):
            rows = conn.execute("""
                SELECT price_per_sqm
                FROM land_prices
                WHERE latitude BETWEEN ? AND ?
                  AND longitude BETWEEN ? AND ?
                  AND price_per_sqm > 0
                LIMIT 300
            """, (lat - 0.03, lat + 0.03, lng - 0.03, lng + 0.03)).fetchall()
            vals = [dict(r).get("price_per_sqm") for r in rows if dict(r).get("price_per_sqm")]
            vals = [v for v in vals if _is_valid_land_price(v)]
            if vals:
                ctx["land_price_sqm"] = float(statistics.median(vals))
                ctx["sample_counts"]["land_price"] = max(ctx["sample_counts"].get("land_price", 0), len(vals))
                ctx["source"] = "land_prices"

        # 地価(3): transactions 近傍中央値
        if not ctx.get("land_price_sqm"):
            rows = conn.execute("""
                SELECT price_per_sqm
                FROM transactions
                WHERE latitude BETWEEN ? AND ?
                  AND longitude BETWEEN ? AND ?
                  AND price_per_sqm > 0
                LIMIT 500
            """, (lat - 0.03, lat + 0.03, lng - 0.03, lng + 0.03)).fetchall()
            vals = [dict(r).get("price_per_sqm") for r in rows if dict(r).get("price_per_sqm")]
            vals = [v for v in vals if _is_valid_land_price(v)]
            if vals:
                median_tx = float(statistics.median(vals))
                ctx["tx_price_sqm"] = ctx.get("tx_price_sqm") or median_tx
                # 取引単価を地価推定に準用（若干控えめ）
                ctx["land_price_sqm"] = median_tx * 0.97
                ctx["sample_counts"]["transactions"] = max(ctx["sample_counts"].get("transactions", 0), len(vals))
                ctx["source"] = "transactions_fallback"

        # 賃料補完: rental_comps 近傍中央値
        if not ctx.get("rent_sqm"):
            rows = conn.execute("""
                SELECT rent_per_sqm
                FROM rental_comps
                WHERE latitude BETWEEN ? AND ?
                  AND longitude BETWEEN ? AND ?
                  AND rent_per_sqm > 0
                LIMIT 500
            """, (lat - 0.03, lat + 0.03, lng - 0.03, lng + 0.03)).fetchall()
            vals = [dict(r).get("rent_per_sqm") for r in rows if dict(r).get("rent_per_sqm")]
            vals = [v for v in vals if _is_valid_rent(v)]
            if vals:
                ctx["rent_sqm"] = float(statistics.median(vals))
                ctx["sample_counts"]["rent"] = max(ctx["sample_counts"].get("rent", 0), len(vals))

    # 利回り補完（分析連動用）
    implied = ctx.get("implied_yield")
    if (not implied) and ctx.get("land_price_sqm") and ctx.get("rent_sqm"):
        try:
            y = float(ctx["rent_sqm"]) * 12 / float(ctx["land_price_sqm"])
            if 0.015 <= y <= 0.18:
                ctx["implied_yield"] = y
        except Exception:
            pass

    # 正味利回り基準（レイヤー/分析の共通参照）
    if ctx.get("implied_yield"):
        try:
            expense = (
                VACANCY_RATE + MANAGEMENT_FEE_RATE + REPAIR_RESERVE_RATE
                + INSURANCE_RATE + PROPERTY_TAX_RATE + CITY_PLANNING_TAX_RATE
            )
            # 駅距離が遠いほど空室リスクを加算
            dkm = float(ctx.get("station_dist_km") or 0)
            if dkm > 0.8:
                expense += min(0.06, (dkm - 0.8) * 0.03)
            net_y = float(ctx["implied_yield"]) * max(0.55, (1 - expense))
            if 0.005 <= net_y <= 0.15:
                ctx["net_yield_ref"] = net_y
            ctx["expense_rate_ref"] = expense
        except Exception:
            pass

    # 片側欠損時に利回りから逆算して分析入力補完に使えるようにする
    if ctx.get("implied_yield"):
        try:
            y = float(ctx["implied_yield"])
            if not ctx.get("rent_sqm") and ctx.get("land_price_sqm"):
                ctx["rent_sqm"] = float(ctx["land_price_sqm"]) * y / 12
            if not ctx.get("land_price_sqm") and ctx.get("rent_sqm"):
                ctx["land_price_sqm"] = float(ctx["rent_sqm"]) * 12 / max(y, 1e-6)
        except Exception:
            pass

    # データが何もなければNone
    if not ctx.get("land_price_sqm") and not ctx.get("rent_sqm") and not ctx.get("tx_price_sqm"):
        return None
    return ctx


def _calc_autofill_rent_factor(prop: Property, ctx: dict) -> float:
    """賃料自動補完の動的係数を計算"""
    factor = ANALYZE_AUTOFILL_RENT_BASE_FACTOR
    dist = prop.station_distance_min
    if dist is None and ctx.get("station_dist_km") is not None:
        dist = int(round(float(ctx["station_dist_km"]) * 12.5))

    if dist is not None:
        if dist <= 5:
            factor += ANALYZE_AUTOFILL_RENT_NEAR_BONUS
        elif dist >= 12:
            factor -= ANALYZE_AUTOFILL_RENT_FAR_PENALTY

    sample_rent = (ctx.get("sample_counts") or {}).get("rent", 0)
    if sample_rent >= 30:
        factor += 0.03
    elif sample_rent >= 10:
        factor += 0.015

    return max(ANALYZE_AUTOFILL_RENT_MIN_FACTOR, min(ANALYZE_AUTOFILL_RENT_MAX_FACTOR, factor))


def _apply_market_context_to_property(prop: Property, data: dict) -> tuple[Optional[dict], dict]:
    """ヒートマップ市場データを物件分析入力に反映（未入力項目のみ補完）"""
    auto_filled = {}

    lat = prop.latitude or data.get("latitude")
    lng = prop.longitude or data.get("longitude")

    # 座標未入力なら住所ジオコーディング（1件分析用途）
    if (not lat or not lng) and prop.address:
        try:
            from data.geocoder import Geocoder
            coords = Geocoder().geocode(prop.address)
            if coords:
                lat, lng = coords
                prop.latitude = lat
                prop.longitude = lng
                auto_filled["coordinates"] = {"lat": lat, "lng": lng}
        except Exception:
            pass

    if not lat or not lng:
        return None, auto_filled

    ctx = _load_market_context(float(lat), float(lng))
    if not ctx:
        return None, auto_filled

    land_price_sqm = ctx.get("land_price_sqm")
    rent_sqm = ctx.get("rent_sqm")

    # 地価: ㎡単価を設定（入力が空のとき）
    if land_price_sqm and (not prop.price_per_sqm or prop.price_per_sqm <= 0):
        prop.price_per_sqm = float(land_price_sqm) * ANALYZE_AUTOFILL_LAND_PRICE_FACTOR
        auto_filled["price_per_sqm"] = prop.price_per_sqm

    # 売出価格: 面積と地価から補完（入力が空/0のとき）
    if land_price_sqm and prop.land_area and (not prop.asking_price or prop.asking_price <= 0):
        est_asking = int(float(land_price_sqm) * float(prop.land_area))
        prop.asking_price = est_asking
        auto_filled["asking_price"] = est_asking

    # 賃料: 建物面積と賃料単価から補完（入力が空/0のとき）
    if rent_sqm and prop.building_area and (not prop.current_rent_annual or prop.current_rent_annual <= 0):
        rent_factor = _calc_autofill_rent_factor(prop, ctx)
        est_annual_rent = int(float(rent_sqm) * float(prop.building_area) * 12 * rent_factor)
        prop.current_rent_annual = est_annual_rent
        auto_filled["current_rent_annual"] = est_annual_rent
        auto_filled["rent_factor"] = rent_factor

    # 利回りから年額賃料/売出価格を補完（建物面積不明でも分析反映）
    implied_yield = ctx.get("implied_yield")
    if implied_yield:
        try:
            y = float(implied_yield)
            if 0.015 <= y <= 0.18:
                if prop.asking_price and prop.asking_price > 0 and (not prop.current_rent_annual or prop.current_rent_annual <= 0):
                    est_annual_rent = int(float(prop.asking_price) * y)
                    prop.current_rent_annual = est_annual_rent
                    auto_filled["current_rent_annual"] = est_annual_rent
                    auto_filled["implied_yield_used"] = y
                elif prop.current_rent_annual and prop.current_rent_annual > 0 and (not prop.asking_price or prop.asking_price <= 0):
                    est_price = int(float(prop.current_rent_annual) / max(y, 1e-6))
                    prop.asking_price = est_price
                    auto_filled["asking_price"] = est_price
                    auto_filled["implied_yield_used"] = y
        except Exception:
            pass

    # 駅情報補完
    if not prop.nearest_station and ctx.get("nearest_station"):
        prop.nearest_station = ctx["nearest_station"]
        auto_filled["nearest_station"] = prop.nearest_station
    if (not prop.station_distance_min or prop.station_distance_min <= 0) and ctx.get("station_dist_km"):
        prop.station_distance_min = int(round(float(ctx["station_dist_km"]) * 12.5))
        auto_filled["station_distance_min"] = prop.station_distance_min

    return ctx, auto_filled


# ===== 地価データAPI =====

@app.get("/api/land-prices/{prefecture_code}")
async def get_land_prices(
    prefecture_code: str,
    city_code: str = "",
    year: int = None,
):
    """地価データをGeoJSON（市区町村ベース集計）で返す"""
    features = []
    pref_codes = _expand_prefecture_codes(prefecture_code)
    pref_where, pref_params = _pref_where_clause("t.prefecture_code", pref_codes)

    with db._conn() as conn:
        sql = """
            SELECT t.city_code, AVG(t.price_per_sqm) as avg_price,
                   COUNT(*) as cnt
            FROM transactions t
            WHERE """ + pref_where + """ AND t.property_type = '宅地(土地)'
            AND t.price_per_sqm > 0 AND t.price_per_sqm < 10000000
        """
        params = list(pref_params)
        if city_code:
            sql += " AND t.city_code = ?"
            params.append(city_code)
        sql += " GROUP BY t.city_code HAVING cnt >= 2"
        rows = conn.execute(sql, params).fetchall()

    # 市区町村の代表座標（reinfolib_clientから取得）
    from data.reinfolib_client import ReinfolibClient
    city_centers = {}
    for pref in pref_codes or [prefecture_code]:
        city_centers.update(ReinfolibClient()._get_city_centers(pref))
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
            "city_name": CITY_NAME_MAP.get(city_code, "一都三県" if len(pref_codes) > 1 else ""),
        },
    })


@app.get("/api/transactions/{prefecture_code}")
async def get_transactions(
    prefecture_code: str,
    city_code: str = "",
):
    """取引データをGeoJSON（市区町村ベース集計）で返す"""
    features = []
    pref_codes = _expand_prefecture_codes(prefecture_code)
    pref_where, pref_params = _pref_where_clause("t.prefecture_code", pref_codes)

    with db._conn() as conn:
        sql = """
            SELECT t.city_code, t.property_type,
                   AVG(t.price_per_sqm) as avg_price,
                   AVG(t.transaction_price) as avg_total,
                   COUNT(*) as cnt
            FROM transactions t
            WHERE """ + pref_where + """ AND t.price_per_sqm > 0
        """
        params = list(pref_params)
        if city_code:
            sql += " AND t.city_code = ?"
            params.append(city_code)
        sql += " GROUP BY t.city_code, t.property_type HAVING cnt >= 2"
        rows = conn.execute(sql, params).fetchall()

    from data.reinfolib_client import ReinfolibClient
    city_centers = {}
    for pref in pref_codes or [prefecture_code]:
        city_centers.update(ReinfolibClient()._get_city_centers(pref))

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
    market_context = None
    if prop.latitude and prop.longitude:
        market_context, _ = _apply_market_context_to_property(prop, data)
    result = orchestrator.run(prop)
    judgment = result["judgment"]
    critic = result.get("critic_review", {})

    # 市場データ基準との整合を評価へ反映（要件: データ基準と投資評価の紐づけ）
    market_benchmark = {}
    try:
        val = result.get("valuation")
        prop_net = getattr(val, "net_yield", None)
        prop_gross = getattr(val, "gross_yield", None)
        m_net = (market_context or {}).get("net_yield_ref")
        m_gross = (market_context or {}).get("implied_yield")
        if m_net is not None or m_gross is not None:
            market_benchmark = {
                "market_net_yield": m_net,
                "market_gross_yield": m_gross,
                "property_net_yield": prop_net,
                "property_gross_yield": prop_gross,
                "source": (market_context or {}).get("source"),
            }
        if m_net is not None and prop_net is not None:
            gap = float(prop_net) - float(m_net)
            market_benchmark["net_yield_gap"] = gap
            # key_metrics に明示して投資判断UIへ反映
            judgment.key_metrics["市場正味利回り"] = f"{float(m_net)*100:.1f}%"
            judgment.key_metrics["物件-市場乖離"] = f"{gap*100:+.1f}%"
            if gap >= 0.006:
                judgment.strengths.append("市場基準の正味利回りを有意に上回る")
            elif gap <= -0.006:
                judgment.weaknesses.append("市場基準の正味利回りを下回る")
                judgment.risks.append("市場比で収益性が弱く、下振れ時の耐性が低い")
    except Exception:
        pass
    return JSONResponse(content={
        "judgment": judgment.to_dict(),
        "valuation": result["valuation"].to_dict(),
        "simulation": result["simulation"].to_dict(),
        "critic_review": critic,
        "summary": judgment.summary_text,
    })


def _normalize_property_input(raw: dict, default_name: str = "物件") -> dict:
    """分析API投入前の必須項目/型を正規化"""
    d = dict(raw or {})
    if not d.get("name"):
        d["name"] = d.get("address") or default_name
    if not d.get("address"):
        d["address"] = d.get("name") or "住所不明"
    if not d.get("prefecture_code"):
        d["prefecture_code"] = "13"
    if not d.get("city_code"):
        pref = str(d.get("prefecture_code") or "13")
        d["city_code"] = "13104" if pref == "13" else f"{pref}000"
    if d.get("building_age") is None and d.get("built_year"):
        try:
            d["building_age"] = max(0, datetime.now().year - int(d["built_year"]))
        except Exception:
            pass
    return d


def _build_rebuild_scenario_input(base: dict, market_context: Optional[dict]) -> Optional[tuple[dict, dict]]:
    """中古物件向け: PlanAgentで建替え最適プランを生成して分析入力化"""
    land_area = base.get("land_area")
    building_area = base.get("building_area")
    building_age = base.get("building_age")
    asking_price = base.get("asking_price")

    if not land_area or not building_area or building_age is None:
        return None
    if float(building_age) <= 0:
        return None

    try:
        # 建蔽率/容積率の正規化（0-1 or 0-100の両対応）
        cov_raw = float(base.get("building_coverage") or 0.60)
        far_raw = float(base.get("floor_area_ratio") or 2.00)
        cov_ratio = cov_raw / 100.0 if cov_raw > 1 else cov_raw
        far_ratio = far_raw / 100.0 if far_raw > 8 else far_raw
        cov_ratio = max(0.30, min(0.90, cov_ratio))
        far_ratio = max(0.80, min(8.00, far_ratio))

        listing = LandListing(
            address=base.get("address") or "",
            station=base.get("nearest_station"),
            walk_minutes=base.get("station_distance_min"),
            land_price=int(float(base.get("asking_price") or 0)),
            land_area_sqm=float(land_area),
            building_coverage_ratio=cov_ratio,
            floor_area_ratio=far_ratio,
            zoning=base.get("land_use_zone"),
            latitude=base.get("latitude"),
            longitude=base.get("longitude"),
            source=base.get("source"),
            source_url=base.get("source_url"),
        )

        rent_hint = None
        if market_context and market_context.get("rent_sqm"):
            rent_hint = float(market_context["rent_sqm"])
        summary = plan_agent.run(listing, rent_per_sqm=rent_hint)
        if not summary.plans:
            return None

        # PlanAgentは内部で多角評価ソート済み。先頭を採用。
        best = summary.plans[0]
        demo_unit = 25_000
        try:
            profile = getattr(plan_agent, "_cost_profiles", {}).get(best.structure_type)
            if profile and getattr(profile, "demolition_cost_per_sqm", None):
                demo_unit = int(profile.demolition_cost_per_sqm)
        except Exception:
            pass
        demolition_cost = int(float(building_area) * demo_unit)
        rebuild_incremental_cost = int(
            (best.estimated_construction_cost or 0)
            + (best.construction_overhead or 0)
            + (best.setback_cost_premium or 0)
            + demolition_cost
        )

        rebuilt = dict(base)
        rebuilt["name"] = f"{base.get('name') or '物件'}（建替）"
        rebuilt["structure"] = best.structure_type or (base.get("structure") or "重量鉄骨")
        rebuilt["building_area"] = round(best.actual_total_floor_area_sqm or float(building_area), 1)
        rebuilt["units"] = best.max_units
        rebuilt["building_age"] = 0
        rebuilt["built_year"] = datetime.now().year

        if asking_price and asking_price > 0:
            rebuilt["asking_price"] = int(float(asking_price) + rebuild_incremental_cost)
        else:
            rebuilt["asking_price"] = int(rebuild_incremental_cost)

        # 賃料は建築プラン推定を優先（市場ヒントはPlanAgent側へ投入済み）
        est_annual = int(best.estimated_annual_income or 0)
        if est_annual <= 0 and market_context and market_context.get("rent_sqm"):
            est_annual = int(float(market_context["rent_sqm"]) * float(rebuilt["building_area"]) * 12 * 0.92)
        elif est_annual <= 0 and base.get("current_rent_annual"):
            est_annual = int(float(base["current_rent_annual"]) * 1.15)
        if est_annual > 0:
            rebuilt["current_rent_annual"] = est_annual

        assumptions = {
            "selected_plan": best.plan_label,
            "target_building_area_sqm": rebuilt["building_area"],
            "target_units": best.max_units,
            "rebuild_incremental_cost": rebuild_incremental_cost,
            "rebuild_cost_hard": int(best.estimated_construction_cost or 0),
            "rebuild_cost_overhead": int(best.construction_overhead or 0),
            "rebuild_cost_setback": int(best.setback_cost_premium or 0),
            "demolition_cost": demolition_cost,
            "plan_total_investment": int(best.total_investment or 0),
            "plan_estimated_yield": best.estimated_yield,
            "plan_rank_score": getattr(best, "_rank_score", None),
            "far_ratio_used": far_ratio,
            "coverage_ratio_used": cov_ratio,
        }
        return rebuilt, assumptions
    except Exception:
        return None


def _enrich_listing_regulations_if_needed(listing_id: int, listing_data: dict) -> dict:
    """
    用途地域/建蔽率/容積率が欠損している場合、reinfolibから補完してDBへ反映。
    単発分析APIでも規制条件を自動読取できるようにする。
    """
    if not listing_data:
        return listing_data
    if not listing_data.get("latitude") or not listing_data.get("longitude"):
        return listing_data
    needs_enrich = (
        not listing_data.get("zoning")
        or listing_data.get("building_coverage_ratio") is None
        or listing_data.get("floor_area_ratio") is None
    )
    if not needs_enrich:
        return listing_data

    from data.reinfolib_client import ReinfolibClient

    client = ReinfolibClient()
    if not client.is_configured():
        return listing_data

    try:
        data = client.enrich_land_listing(
            float(listing_data["latitude"]),
            float(listing_data["longitude"]),
        )
    except Exception:
        return listing_data
    if not data:
        return listing_data

    def _to_ratio(v):
        try:
            vv = float(str(v).replace("%", "").strip())
            return vv / 100 if vv > 1 else vv
        except Exception:
            return None

    updates = {}
    if not listing_data.get("zoning") and data.get("zoning"):
        updates["zoning"] = str(data["zoning"])
    if listing_data.get("building_coverage_ratio") is None and data.get("building_coverage_ratio") is not None:
        cov = _to_ratio(data.get("building_coverage_ratio"))
        if cov is not None:
            updates["building_coverage_ratio"] = cov
    if listing_data.get("floor_area_ratio") is None and data.get("floor_area_ratio") is not None:
        far = _to_ratio(data.get("floor_area_ratio"))
        if far is not None:
            updates["floor_area_ratio"] = far
    if data.get("quasi_fireproof"):
        updates["quasi_fireproof"] = 1

    if not updates:
        return listing_data

    set_clause = ", ".join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [listing_id]
    with db._conn() as conn:
        conn.execute(
            f"UPDATE land_listings SET {set_clause}, updated_at=datetime('now','localtime') WHERE id=?",
            params,
        )

    merged = dict(listing_data)
    merged.update(updates)
    return merged


def _analysis_digest(res: dict, scenario: str, market_context: Optional[dict] = None, assumptions: Optional[dict] = None) -> dict:
    j = res["judgment"]
    v = res["valuation"]
    s = res["simulation"]
    digest = {
        "scenario": scenario,
        "grade": j.grade,
        "score": round(j.overall_score, 2),
        "recommendation": j.recommendation,
        "confidence": j.confidence,
        "gross_yield": v.gross_yield,
        "net_yield": v.net_yield,
        "expense_rate": v.expense_rate,
        "irr": s.irr,
        "dscr": s.dscr,
        "year1_cash_flow": s.year1_cash_flow,
        "exit_cap_rate": getattr(s, "hold_sell_exit_cap_base", None),
        "exit_cap_rate_stress": getattr(s, "hold_sell_exit_cap_stress", None),
        "hold_sell_roi": getattr(s, "hold_sell_roi_65", None),
        "hold_sell_total_return": getattr(s, "hold_sell_total_return_65", None),
        "summary": j.summary_text,
        "market_context": market_context,
    }
    if assumptions:
        digest["assumptions"] = assumptions
    return digest


def _analysis_cache_key(raw: dict) -> str:
    """物件分析キャッシュキー（DB永続用）"""
    d = dict(raw or {})
    if d.get("_type") == "land" or d.get("_land_listing_id"):
        lid = str(d.get("_land_listing_id") or d.get("id") or "").strip()
        if lid:
            return f"land:{lid}"
    pid = str(d.get("id") or "").strip()
    if pid:
        return f"property:{pid}"
    src = str(d.get("source_url") or "").strip().lower()
    if src:
        return f"url:{src}"
    fallback = f"{d.get('name','')}|{d.get('address','')}"
    return f"fallback:{hashlib.md5(fallback.encode('utf-8')).hexdigest()}"


def _save_analysis_cache(raw: dict, selected: dict, as_is: Optional[dict], rebuild: Optional[dict]):
    """分析結果をDBへ永続化"""
    key = _analysis_cache_key(raw)
    property_type = "land" if (raw.get("_type") == "land" or raw.get("_land_listing_id")) else "property"
    if property_type == "land":
        property_id = str(raw.get("_land_listing_id") or raw.get("id") or "")
    else:
        property_id = str(raw.get("id") or "")
    db.upsert_property_analysis_cache(
        analysis_key=key,
        property_id=property_id or None,
        property_type=property_type,
        name=str(raw.get("name") or ""),
        address=str(raw.get("address") or ""),
        grade=selected.get("grade"),
        score=selected.get("score"),
        scenario=selected.get("scenario"),
        selected=selected,
        as_is=as_is or {},
        rebuild=rebuild or {},
    )


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間距離(km)"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _location_bucket(row: dict) -> str:
    """ランキング分散用のエリアキー"""
    city = str(row.get("city_code") or "").strip()
    if city:
        return f"city:{city}"
    st = str(row.get("nearest_station") or "").strip()
    if st:
        return f"st:{st}"
    addr = str(row.get("address") or "").strip()
    return f"addr:{addr[:8]}" if addr else "unknown"


def _spatial_diversify_ranking(rows: list[dict]) -> list[dict]:
    """
    スコア上位を維持しつつ、同一エリア集中を緩和して並べ替える。
    - 同一bucketの重複を段階ペナルティ
    - 近接距離（~2km）を追加ペナルティ
    """
    if not rows:
        return []
    remaining = list(rows)
    selected = []

    while remaining:
        best_idx = 0
        best_adj = -10**9
        for i, cand in enumerate(remaining):
            score = float(cand.get("score") or 0.0)
            bucket = _location_bucket(cand)
            same_bucket = sum(1 for s in selected if _location_bucket(s) == bucket)
            penalty = same_bucket * 4.0

            clat = cand.get("latitude")
            clng = cand.get("longitude")
            if clat is not None and clng is not None:
                near_1km = 0
                near_2km = 0
                for s in selected:
                    slat, slng = s.get("latitude"), s.get("longitude")
                    if slat is None or slng is None:
                        continue
                    d = _haversine_km(float(clat), float(clng), float(slat), float(slng))
                    if d < 1.0:
                        near_1km += 1
                    elif d < 2.0:
                        near_2km += 1
                penalty += near_1km * 6.0 + near_2km * 2.5

            adjusted = score - penalty
            if adjusted > best_adj:
                best_adj = adjusted
                best_idx = i

        selected.append(remaining.pop(best_idx))
    return selected


# IngestPipeline に空間分散ソートを配線
ingest_pipeline.diversify_fn = _spatial_diversify_ranking


@app.post("/api/analyze-batch")
async def analyze_batch(request: Request):
    """複数物件の一括判定"""
    data = await request.json()
    properties = [Property.from_dict(p) for p in data.get("properties", [])]
    results = orchestrator.run_batch(properties)
    ranked = []
    for idx, r in enumerate(results):
        j = r["judgment"]
        p = properties[idx] if idx < len(properties) else None
        ranked.append({
            "name": j.property_name,
            "grade": j.grade,
            "score": j.overall_score,
            "recommendation": j.recommendation,
            "critic": r.get("critic_review", {}).get("reliability_grade", "?"),
            "latitude": getattr(p, "latitude", None),
            "longitude": getattr(p, "longitude", None),
            "city_code": getattr(p, "city_code", None),
            "nearest_station": getattr(p, "nearest_station", None),
            "address": getattr(p, "address", None),
        })
    ranked.sort(key=lambda x: x["score"] or 0, reverse=True)
    ranked = _spatial_diversify_ranking(ranked)
    return JSONResponse(content={
        "results": [r["judgment"].to_dict() for r in results],
        "ranking": [
            {
                "rank": i + 1,
                "name": x["name"],
                "grade": x["grade"],
                "score": x["score"],
                "recommendation": x["recommendation"],
                "critic": x["critic"],
            }
            for i, x in enumerate(ranked)
        ],
    })


@app.post("/api/properties/auto-analyze")
async def auto_analyze_properties(request: Request):
    """一覧物件を自動分析し、地図/リスト反映用の結果を返す"""
    payload = await request.json()
    raw_props = payload.get("properties", [])
    include_rebuild = bool(payload.get("include_rebuild", True))
    limit = int(payload.get("limit", 300) or 300)
    raw_props = raw_props[: max(1, min(limit, 600))]

    results = []
    for i, raw in enumerate(raw_props, 1):
        try:
            normalized = _normalize_property_input(raw, default_name=f"物件{i}")
            prop = Property.from_dict(normalized)

            market_context = None
            auto_filled = {}
            if prop.latitude and prop.longitude:
                market_context, auto_filled = _apply_market_context_to_property(prop, normalized)

            as_is_res = orchestrator.run(prop)
            as_is = _analysis_digest(as_is_res, "as_is", market_context=market_context)
            selected = as_is
            rebuild = None

            # 中古（既存建物あり）について建替え案を比較
            if include_rebuild:
                rebuild_pair = _build_rebuild_scenario_input(normalized, market_context)
                if rebuild_pair:
                    rebuild_input, assumptions = rebuild_pair
                    rb_prop = Property.from_dict(_normalize_property_input(rebuild_input, default_name=f"物件{i} 建替"))
                    if rb_prop.latitude and rb_prop.longitude:
                        _apply_market_context_to_property(rb_prop, rebuild_input)
                    rb_res = orchestrator.run(rb_prop)
                    rebuild = _analysis_digest(rb_res, "rebuild", market_context=market_context, assumptions=assumptions)
                    if (rebuild.get("score") or 0) > (as_is.get("score") or 0):
                        selected = rebuild

            row = {
                "index": i - 1,
                "client_index": raw.get("_client_index"),
                "name": normalized.get("name"),
                "address": normalized.get("address"),
                "city_code": normalized.get("city_code"),
                "nearest_station": normalized.get("nearest_station"),
                "latitude": normalized.get("latitude"),
                "longitude": normalized.get("longitude"),
                "auto_filled": auto_filled,
                "selected": selected,
                "as_is": as_is,
                "rebuild": rebuild,
            }
            results.append(row)
            try:
                _save_analysis_cache(raw=raw, selected=selected, as_is=as_is, rebuild=rebuild)
            except Exception as e:
                logging.warning(f"analysis cache save warning: {e}")
        except Exception as e:
            results.append({
                "index": i - 1,
                "client_index": raw.get("_client_index"),
                "name": raw.get("name") or raw.get("address") or f"物件{i}",
                "error": str(e),
            })

    valid = [r for r in results if r.get("selected") and not r.get("error")]
    ranking_candidates = []
    for r in valid:
        sel = r.get("selected") or {}
        ranking_candidates.append({
            "row": r,
            "score": sel.get("score") or 0,
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
            "city_code": r.get("city_code"),
            "nearest_station": (r.get("selected") or {}).get("market_context", {}).get("nearest_station") or r.get("nearest_station"),
            "address": r.get("address"),
        })
    ranking_candidates.sort(key=lambda x: x.get("score") or 0, reverse=True)
    ranking = [x["row"] for x in _spatial_diversify_ranking(ranking_candidates)]
    ranking_rows = [
        {
            "rank": idx + 1,
            "name": r.get("name"),
            "grade": (r.get("selected") or {}).get("grade"),
            "score": (r.get("selected") or {}).get("score"),
            "recommendation": (r.get("selected") or {}).get("recommendation"),
            "scenario": (r.get("selected") or {}).get("scenario"),
            "gross_yield": (r.get("selected") or {}).get("gross_yield"),
            "net_yield": (r.get("selected") or {}).get("net_yield"),
            "hold_sell_roi": (r.get("selected") or {}).get("hold_sell_roi"),
            "exit_cap_rate": (r.get("selected") or {}).get("exit_cap_rate"),
            "client_index": r.get("client_index"),
            "id": raw_props[r.get("client_index")].get("id") if isinstance(r.get("client_index"), int) and 0 <= r.get("client_index") < len(raw_props) else None,
        }
        for idx, r in enumerate(ranking)
    ]

    return JSONResponse(content={
        "count": len(raw_props),
        "success": len(valid),
        "results": results,
        "ranking": ranking_rows,
    })


@app.post("/api/properties/analysis-cache/bulk")
async def get_analysis_cache_bulk(request: Request):
    payload = await request.json()
    keys = payload.get("keys", []) or []
    data = db.get_property_analysis_cache_bulk(keys)
    return JSONResponse(content={"count": len(data), "items": data})


@app.get("/api/properties/analysis-cache/{analysis_key}")
async def get_analysis_cache(analysis_key: str):
    row = db.get_property_analysis_cache(analysis_key)
    return JSONResponse(content={"item": row})


@app.post("/api/properties/analyze-unanalyzed")
async def analyze_unanalyzed_properties(request: Request):
    """未分析物件のみ自動分析してDBへ保存"""
    payload = await request.json()
    raw_props = payload.get("properties", []) or []
    include_rebuild = bool(payload.get("include_rebuild", True))
    limit = max(1, min(int(payload.get("limit", 600) or 600), 1000))
    raw_props = raw_props[:limit]

    key_map = {idx: _analysis_cache_key(raw) for idx, raw in enumerate(raw_props)}
    existing = db.get_property_analysis_cache_bulk(list(key_map.values()))
    pending = [raw for i, raw in enumerate(raw_props) if key_map.get(i) not in existing]

    analyzed = []
    errors = []
    for i, raw in enumerate(pending, 1):
        try:
            normalized = _normalize_property_input(raw, default_name=f"未分析物件{i}")
            prop = Property.from_dict(normalized)

            market_context = None
            if prop.latitude and prop.longitude:
                market_context, _ = _apply_market_context_to_property(prop, normalized)

            as_is_res = orchestrator.run(prop)
            as_is = _analysis_digest(as_is_res, "as_is", market_context=market_context)
            selected = as_is
            rebuild = None

            if include_rebuild:
                rebuild_pair = _build_rebuild_scenario_input(normalized, market_context)
                if rebuild_pair:
                    rebuild_input, assumptions = rebuild_pair
                    rb_prop = Property.from_dict(_normalize_property_input(rebuild_input, default_name=f"未分析物件{i} 建替"))
                    if rb_prop.latitude and rb_prop.longitude:
                        _apply_market_context_to_property(rb_prop, rebuild_input)
                    rb_res = orchestrator.run(rb_prop)
                    rebuild = _analysis_digest(rb_res, "rebuild", market_context=market_context, assumptions=assumptions)
                    if (rebuild.get("score") or 0) > (as_is.get("score") or 0):
                        selected = rebuild

            _save_analysis_cache(raw=raw, selected=selected, as_is=as_is, rebuild=rebuild)
            analyzed.append({
                "analysis_key": _analysis_cache_key(raw),
                "name": normalized.get("name"),
                "selected": selected,
                "as_is": as_is,
                "rebuild": rebuild,
            })
        except Exception as e:
            errors.append({"name": raw.get("name") or raw.get("address") or f"物件{i}", "error": str(e)})

    return JSONResponse(content={
        "requested": len(raw_props),
        "skipped_already_analyzed": len(raw_props) - len(pending),
        "analyzed": len(analyzed),
        "errors": len(errors),
        "results": analyzed,
        "error_rows": errors[:100],
    })


# ===== スクレイピングAPI =====

@app.get("/api/scrape")
async def scrape_properties(
    prefecture_code: str = "13",
    sources: str = "rakumachi,kenbiya,rals",
    max_pages: int = 10,
    split_by_price: bool = False,
    auto_judge: bool = True,
    analyze_limit: int = 80,
    run_in_background: bool = True,
):
    """複数ソースから収益物件をスクレイピング → 重複統合 → 座標検証 → 自動判定

    既定ではバックグラウンド実行し、進捗は /api/task-status で確認する。
    （同期実行はブラウザの Failed to fetch タイムアウトを起こしやすい）
    """
    import threading

    global _bg_task_status
    pref_codes = _expand_prefecture_codes(prefecture_code) or ["13"]
    source_list = [s.strip() for s in sources.split(",") if s.strip()] or ["rakumachi"]
    max_pages = max(1, min(int(max_pages or 1), 20))
    analyze_limit = max(1, min(int(analyze_limit or 1), 200))

    def _run_scrape():
        global _bg_task_status
        try:
            _bg_task_status["step"] = (
                f"スクレイピング中... pref={','.join(pref_codes)} sources={','.join(source_list)}"
            )
            result = ingest_pipeline.scrape_and_process(
                prefecture_codes=pref_codes,
                sources=source_list,
                max_pages=max_pages,
                split_by_price=split_by_price,
                auto_judge=auto_judge,
                analyze_limit=analyze_limit,
            )
            _bg_task_status["result"] = result
            _bg_task_status["step"] = (
                f"完了: {result.get('count', 0)}件取得 / "
                f"自動判定 {result.get('auto_judged', 0)}件"
            )
        except Exception as e:
            logging.exception("property scrape failed")
            _bg_task_status["error"] = str(e)
            _bg_task_status["step"] = f"エラー: {e}"
        finally:
            _bg_task_status["running"] = False

    if run_in_background:
        if _bg_task_status.get("running"):
            return JSONResponse(content={
                "status": "running",
                "message": f"実行中: {_bg_task_status.get('step', '')}",
                "count": 0,
                "properties": [],
            })
        _bg_task_status = {
            "running": True,
            "step": "スクレイピング開始...",
            "result": None,
            "error": None,
            "task_type": "property_scrape",
        }
        threading.Thread(target=_run_scrape, daemon=True).start()
        return JSONResponse(content={
            "status": "started",
            "message": "スクレイピングをバックグラウンドで開始しました",
            "prefecture_codes": pref_codes,
            "sources": source_list,
            "max_pages": max_pages,
            "count": 0,
            "properties": [],
        })

    try:
        result = ingest_pipeline.scrape_and_process(
            prefecture_codes=pref_codes,
            sources=source_list,
            max_pages=max_pages,
            split_by_price=split_by_price,
            auto_judge=auto_judge,
            analyze_limit=analyze_limit,
        )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "count": 0, "properties": []},
            status_code=500,
        )




def _normalize_ratio_input(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        raw = float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    return raw / 100.0 if raw > 1 else raw


@app.post("/api/land/quick-evaluate")
async def quick_evaluate_land(request: Request):
    """
    地図UIの簡易入力から土地の収益性と価格妥当ラインを即時提示
    Body:
      {
        "address": "...",
        "land_area_sqm": 120,
        "land_price": 50000000,              # 任意
        "building_coverage_ratio": 60,       # 任意(% or ratio)
        "floor_area_ratio": 200,             # 任意(% or ratio)
        "walk_minutes": 8                    # 任意
      }
    """
    data = await request.json()
    address = (data.get("address") or "").strip()
    if not address:
        return JSONResponse(content={"error": "住所が未入力です"}, status_code=400)

    try:
        land_area_sqm = float(data.get("land_area_sqm") or 0)
    except (TypeError, ValueError):
        land_area_sqm = 0
    if land_area_sqm <= 0:
        return JSONResponse(content={"error": "土地面積(㎡)を正しく入力してください"}, status_code=400)

    try:
        from data.geocoder import Geocoder
        coords = Geocoder().geocode(address)
    except Exception as e:
        return JSONResponse(content={"error": f"ジオコーディング失敗: {e}"}, status_code=500)

    if not coords:
        return JSONResponse(content={"error": "住所の座標を取得できませんでした"}, status_code=422)

    lat, lng = coords
    market_ctx = _load_market_context(float(lat), float(lng)) or {}

    land_price_sqm = market_ctx.get("land_price_sqm")
    tx_price_sqm = market_ctx.get("tx_price_sqm")
    if not land_price_sqm and tx_price_sqm:
        land_price_sqm = float(tx_price_sqm) * 0.97
    if not land_price_sqm:
        land_price_sqm = 220000.0

    estimated_land_price = int(float(land_price_sqm) * land_area_sqm)
    input_land_price = data.get("land_price")
    try:
        land_price = int(input_land_price) if input_land_price else estimated_land_price
    except (TypeError, ValueError):
        land_price = estimated_land_price

    bcr = _normalize_ratio_input(data.get("building_coverage_ratio")) or 0.60
    far = _normalize_ratio_input(data.get("floor_area_ratio")) or 2.00
    walk_minutes = None
    try:
        if data.get("walk_minutes") is not None:
            walk_minutes = int(data.get("walk_minutes"))
    except (TypeError, ValueError):
        walk_minutes = None

    listing = LandListing(
        address=address,
        land_price=land_price,
        land_area_sqm=land_area_sqm,
        building_coverage_ratio=bcr,
        floor_area_ratio=far,
        walk_minutes=walk_minutes,
        latitude=float(lat),
        longitude=float(lng),
        source="地図クイック診断",
    )

    plan_summary = plan_agent.run(
        listing,
        rent_per_sqm=market_ctx.get("rent_sqm"),
        equipment_grade="premium",
    )
    best_plan = plan_summary.best_plan

    implied_yield = market_ctx.get("implied_yield")
    if not implied_yield and market_ctx.get("rent_sqm") and land_price_sqm:
        try:
            implied_yield = float(market_ctx["rent_sqm"]) * 12 / float(land_price_sqm)
        except Exception:
            implied_yield = None

    fair_mid = None
    fair_low = None
    fair_high = None
    valuation_note = "市場基準の妥当レンジ内です"

    if best_plan and best_plan.estimated_annual_income and implied_yield and implied_yield > 0:
        annual_income = float(best_plan.estimated_annual_income)
        fair_mid = int(annual_income / float(implied_yield))
        fair_low = int(annual_income / (float(implied_yield) * 1.15))
        fair_high = int(annual_income / (float(implied_yield) * 0.85))
    else:
        fair_mid = estimated_land_price
        fair_low = int(fair_mid * 0.9)
        fair_high = int(fair_mid * 1.1)

    if land_price > fair_high:
        valuation_note = "割高寄りです（相場より高値）"
    elif land_price < fair_low:
        valuation_note = "割安寄りです（仕入候補）"

    profitability = {
        "gross_yield": best_plan.estimated_yield if best_plan else None,
        "annual_income": best_plan.estimated_annual_income if best_plan else None,
        "total_investment": best_plan.total_investment if best_plan else land_price,
        "plan_label": best_plan.plan_label if best_plan else None,
        "units": best_plan.max_units if best_plan else None,
    }

    return JSONResponse(content={
        "status": "ok",
        "input": {
            "address": address,
            "land_area_sqm": land_area_sqm,
            "land_price": land_price,
            "building_coverage_ratio": bcr,
            "floor_area_ratio": far,
        },
        "coordinates": {"lat": lat, "lng": lng},
        "market_context": {
            "source": market_ctx.get("source"),
            "land_price_sqm": land_price_sqm,
            "rent_sqm": market_ctx.get("rent_sqm"),
            "tx_price_sqm": market_ctx.get("tx_price_sqm"),
            "implied_yield": implied_yield,
            "sample_counts": market_ctx.get("sample_counts") or {},
        },
        "profitability": profitability,
        "fair_price_line": {
            "low": fair_low,
            "mid": fair_mid,
            "high": fair_high,
            "estimated_land_only_price": estimated_land_price,
            "judgment": valuation_note,
        },
        "auto_regulation_enriched": {
            "zoning": listing.zoning,
            "building_coverage_ratio": listing.building_coverage_ratio,
            "floor_area_ratio": listing.floor_area_ratio,
            "quasi_fireproof": listing.quasi_fireproof,
        },
    })


@app.get("/api/scrape-rentals")
async def scrape_rentals(
    prefecture_code: str = "13",
    city_code: str = "",
    max_pages: int = 10,
):
    """SUUMO賃貸から賃料データをスクレイピング"""
    try:
        pref_codes = _expand_prefecture_codes(prefecture_code) or ["13"]
        rentals = []
        for pref in pref_codes:
            rentals.extend(scraper_agent.scrape_rentals(
                prefecture_code=pref,
                city_code=city_code,
                max_pages=max_pages,
            ))
        # DB保存
        saved = db.upsert_rental_comps(rentals)
        try:
            db.reconcile_station_refs("rental_comps", limit=10000)
        except Exception:
            pass
        dedupe = db.merge_duplicate_rental_comps(
            dry_run=False,
            min_group_size=2,
            max_groups=5000,
        )
        # インメモリのrental_agentも更新
        _reload_rental_agent()
        return JSONResponse(content={
            "count": len(rentals),
            "saved": saved,
            "prefecture_codes": pref_codes,
            "dedupe": dedupe,
            "rentals": rentals[:50],
        })
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "count": 0},
            status_code=500,
        )


@app.post("/api/properties/dedupe")
async def dedupe_properties(request: Request):
    """
    重複物件を検出して統合する
    Body: {"dry_run": true/false, "min_group_size": 2, "max_groups": 500}
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    dry_run = bool(data.get("dry_run", False))
    min_group_size = int(data.get("min_group_size", 2) or 2)
    max_groups = int(data.get("max_groups", 500) or 500)

    try:
        result = db.merge_duplicate_properties(
            dry_run=dry_run,
            min_group_size=max(2, min_group_size),
            max_groups=max(1, min(5000, max_groups)),
        )
        return JSONResponse(content={"status": "ok", **result})
    except Exception as e:
        return JSONResponse(
            content={"status": "error", "error": str(e)},
            status_code=500,
        )


@app.put("/api/properties/{property_id}")
async def update_property(property_id: str, request: Request):
    """物件プロット/DB情報の手動更新"""
    try:
        data = await request.json()
    except Exception:
        data = {}

    with db._conn() as conn:
        row = conn.execute("SELECT * FROM properties WHERE id=? LIMIT 1", (property_id,)).fetchone()
        if not row:
            return JSONResponse(content={"error": "not found"}, status_code=404)
        merged = dict(row)

        allowed = [
            "name", "address", "prefecture_code", "city_code",
            "latitude", "longitude", "asking_price",
            "land_area", "building_area", "structure",
            "built_year", "building_age", "units",
            "current_rent_annual", "gross_yield",
            "nearest_station", "station_distance_min",
            "source", "source_url",
        ]
        touched = False
        for f in allowed:
            if f in data:
                merged[f] = data.get(f)
                touched = True
        if not touched:
            return JSONResponse(content={"error": "no fields to update"}, status_code=400)

        if "source_url" in data:
            merged["source_url"] = db._normalize_source_url(merged.get("source_url"))

        try:
            sid = resolve_station_id(
                nearest_station_text=merged.get("nearest_station"),
                lat=merged.get("latitude"),
                lon=merged.get("longitude"),
                pref_code=merged.get("prefecture_code"),
            )
            merged["station_id"] = sid
            if sid and sid in STATION_MAP:
                merged["nearest_station"] = STATION_MAP[sid]["name"]
            elif not (merged.get("nearest_station") or "").strip():
                merged["station_id"] = None
        except Exception:
            pass

        merged["data_json"] = json.dumps(merged, ensure_ascii=False)

        conn.execute(
            """
            UPDATE properties
            SET name=?, address=?, prefecture_code=?, city_code=?,
                latitude=?, longitude=?, asking_price=?,
                land_area=?, building_area=?, structure=?,
                built_year=?, building_age=?, units=?,
                current_rent_annual=?, gross_yield=?,
                nearest_station=?, station_distance_min=?, station_id=?,
                source=?, source_url=?, data_json=?,
                updated_at=datetime('now','localtime')
            WHERE id=?
            """,
            (
                merged.get("name"),
                merged.get("address"),
                merged.get("prefecture_code"),
                merged.get("city_code"),
                merged.get("latitude"),
                merged.get("longitude"),
                merged.get("asking_price"),
                merged.get("land_area"),
                merged.get("building_area"),
                merged.get("structure"),
                merged.get("built_year"),
                merged.get("building_age"),
                merged.get("units"),
                merged.get("current_rent_annual"),
                merged.get("gross_yield"),
                merged.get("nearest_station"),
                merged.get("station_distance_min"),
                merged.get("station_id"),
                merged.get("source"),
                merged.get("source_url"),
                merged.get("data_json"),
                property_id,
            ),
        )

        updated = conn.execute("SELECT * FROM properties WHERE id=? LIMIT 1", (property_id,)).fetchone()
    return JSONResponse(content={"status": "ok", "property": dict(updated)})


@app.post("/api/listings/verify-source")
async def verify_listing_sources(request: Request):
    """
    掲載URLの生存確認を実行し、消失確認物件をdelisted化する。
    """
    try:
        data = await request.json()
    except Exception:
        data = {}

    limit = int(data.get("limit", LISTING_VERIFY_BATCH) or LISTING_VERIFY_BATCH)
    stale_hours = int(data.get("stale_hours", LISTING_VERIFY_STALE_HOURS) or LISTING_VERIFY_STALE_HOURS)
    confirm_failures = int(data.get("confirm_failures", LISTING_VERIFY_CONFIRM_FAILURES) or LISTING_VERIFY_CONFIRM_FAILURES)

    out: Dict[str, Dict[str, int]] = {}
    for table in ("properties", "land_listings"):
        rows = db.get_source_verification_targets(
            table=table,
            limit=max(1, limit),
            stale_hours=max(1, stale_hours),
        )
        checked = 0
        alive_cnt = 0
        failed_cnt = 0
        for row in rows:
            checked += 1
            alive, status, note = _check_source_alive(str(row.get("source_url") or ""))
            if alive:
                alive_cnt += 1
            else:
                failed_cnt += 1
            db.record_source_verification_result(
                table=table,
                row_id=row.get("id"),
                is_alive=alive,
                http_status=status,
                note=note,
                confirm_failures=max(1, confirm_failures),
            )
        out[table] = {"checked": checked, "alive": alive_cnt, "failed": failed_cnt}

    return JSONResponse(content={
        "status": "ok",
        "limit": max(1, limit),
        "stale_hours": max(1, stale_hours),
        "confirm_failures": max(1, confirm_failures),
        "result": out,
    })


@app.post("/api/stations/reconcile")
async def reconcile_stations(request: Request):
    """既存データの駅名/駅IDを実在駅に補正（物件/賃料/土地 + 重複整理）"""
    try:
        data = await request.json()
    except Exception:
        data = {}
    limit = int(data.get("limit", 10000) or 10000)
    with_dedupe = bool(data.get("with_dedupe", True))
    strict_nearest = bool(data.get("strict_nearest", True))
    try:
        p = db.reconcile_station_refs("properties", limit=limit, force_nearest=strict_nearest)
        r = db.reconcile_station_refs("rental_comps", limit=limit, force_nearest=strict_nearest)
        l = db.reconcile_land_listing_station_refs(limit=limit, force_nearest=strict_nearest)
        dedupe_props = {}
        dedupe_rentals = {}
        land_dup_marked = 0
        if with_dedupe:
            dedupe_props = db.merge_duplicate_properties(
                dry_run=False,
                min_group_size=2,
                max_groups=5000,
            )
            dedupe_rentals = db.merge_duplicate_rental_comps(
                dry_run=False,
                min_group_size=2,
                max_groups=5000,
            )
            land_dup_marked = db.detect_duplicates()
        return JSONResponse(content={
            "status": "ok",
            "updated_properties": p,
            "updated_rentals": r,
            "updated_land_listings": l,
            "updated_total": p + r + l,
            "dedupe_properties": dedupe_props,
            "dedupe_rentals": dedupe_rentals,
            "land_duplicates_marked": land_dup_marked,
            "strict_nearest": strict_nearest,
        })
    except Exception as e:
        return JSONResponse(content={"status": "error", "error": str(e)}, status_code=500)


@app.post("/api/properties/consistency-check")
async def consistency_check_properties(request: Request):
    """
    住所/最寄駅/座標の整合チェックを行い、疑義件はsource_url再取得で補正する。
    Body:
      {
        "limit": 50000,
        "max_rescrape": 300,
        "use_browser": false,
        "strict_nearest": true,
        "with_dedupe": true
      }
    """
    import threading
    import time
    global _bg_task_status

    try:
        data = await request.json()
    except Exception:
        data = {}
    limit = int(data.get("limit", 50000) or 50000)
    max_rescrape = int(data.get("max_rescrape", 120) or 120)
    use_browser = bool(data.get("use_browser", False))
    strict_nearest = bool(data.get("strict_nearest", True))
    with_dedupe = bool(data.get("with_dedupe", True))
    run_in_background = bool(data.get("run_in_background", True))
    max_runtime_sec = int(data.get("max_runtime_sec", 900) or 900)

    def _run_job() -> Dict[str, Any]:
        started = time.time()
        scanned = 0
        suspicious = 0
        rescape_attempted = 0
        rescape_updated = 0
        timed_out = False
        samples: List[Dict[str, Any]] = []

        with db._conn() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM properties
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        scanned = len(rows)

        for idx, row in enumerate(rows, 1):
            if (time.time() - started) >= max_runtime_sec:
                timed_out = True
                break

            raw = dict(row)
            if _is_station_address_consistent(raw):
                if idx % 100 == 0:
                    _bg_task_status["step"] = f"整合チェック中... {idx}/{scanned}"
                continue
            suspicious += 1
            if len(samples) < 20:
                samples.append({
                    "id": raw.get("id"),
                    "name": raw.get("name"),
                    "address": raw.get("address"),
                    "nearest_station": raw.get("nearest_station"),
                    "station_distance_min": raw.get("station_distance_min"),
                    "source_url": raw.get("source_url"),
                })
            if rescape_attempted < max(1, max_rescrape) and str(raw.get("source_url") or "").strip():
                rescape_attempted += 1
                _bg_task_status["step"] = (
                    f"再スクレイプ中... {rescape_attempted}/{max_rescrape} "
                    f"(checked {idx}/{scanned})"
                )
                refreshed = _refresh_property_from_source(raw, use_browser=use_browser)
                if refreshed.get("updated"):
                    rescape_updated += 1
            elif idx % 100 == 0:
                _bg_task_status["step"] = f"整合チェック中... {idx}/{scanned}"

        _bg_task_status["step"] = "駅再突合中..."
        rp = db.reconcile_station_refs("properties", limit=limit, force_nearest=strict_nearest)
        rr = db.reconcile_station_refs("rental_comps", limit=limit, force_nearest=strict_nearest)
        rl = db.reconcile_land_listing_station_refs(limit=limit, force_nearest=strict_nearest)

        dedupe_props = {}
        dedupe_rentals = {}
        land_dup_marked = 0
        if with_dedupe:
            _bg_task_status["step"] = "重複整理中..."
            dedupe_props = db.merge_duplicate_properties(dry_run=False, min_group_size=2, max_groups=5000)
            dedupe_rentals = db.merge_duplicate_rental_comps(dry_run=False, min_group_size=2, max_groups=5000)
            land_dup_marked = db.detect_duplicates()

        return {
            "status": "ok",
            "scanned": scanned,
            "suspicious": suspicious,
            "rescrape_attempted": rescape_attempted,
            "rescrape_updated": rescape_updated,
            "reconciled_properties": rp,
            "reconciled_rentals": rr,
            "reconciled_land_listings": rl,
            "strict_nearest": strict_nearest,
            "dedupe_properties": dedupe_props,
            "dedupe_rentals": dedupe_rentals,
            "land_duplicates_marked": land_dup_marked,
            "sample_suspicious": samples,
            "timed_out": timed_out,
            "elapsed_sec": round(time.time() - started, 2),
        }

    if run_in_background:
        if _bg_task_status.get("running"):
            return JSONResponse(content={
                "status": "running",
                "message": f"実行中: {_bg_task_status.get('step', '')}",
            })

        def _runner():
            global _bg_task_status
            _bg_task_status = {
                "running": True,
                "step": "整合チェック開始...",
                "result": None,
                "error": None,
                "task_type": "properties_consistency_check",
            }
            try:
                result = _run_job()
                _bg_task_status["result"] = result
                _bg_task_status["step"] = "完了"
            except Exception as e:
                _bg_task_status["error"] = str(e)
                _bg_task_status["step"] = f"エラー: {e}"
            finally:
                _bg_task_status["running"] = False

        threading.Thread(target=_runner, daemon=True).start()
        return JSONResponse(content={
            "status": "ok",
            "message": "整合チェックをバックグラウンド開始しました。/api/task-status で進捗確認できます。",
            "task_type": "properties_consistency_check",
            "limit": limit,
            "max_rescrape": max_rescrape,
            "max_runtime_sec": max_runtime_sec,
        })

    try:
        _bg_task_status = {
            "running": True,
            "step": "整合チェック実行中...",
            "result": None,
            "error": None,
            "task_type": "properties_consistency_check",
        }
        result = _run_job()
        _bg_task_status["result"] = result
        _bg_task_status["step"] = "完了"
        return JSONResponse(content=result)
    except Exception as e:
        _bg_task_status["error"] = str(e)
        _bg_task_status["step"] = f"エラー: {e}"
        return JSONResponse(content={"status": "error", "error": str(e)}, status_code=500)
    finally:
        _bg_task_status["running"] = False


# ===== URL物件取込API =====

@app.post("/api/scrape-url")
async def scrape_url(request: Request):
    """
    URLから1件の物件情報を取込（Adapter → 重複統合 → 座標検証 → 任意で判定）

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
        result = ingest_pipeline.ingest_url(
            url,
            use_ocr=use_ocr,
            use_browser=use_browser,
            auto_analyze=auto_analyze,
        )
        if result.get("error") and result.get("status") != "ok":
            code = 422 if "取得できません" in str(result.get("error")) else 500
            return JSONResponse(content=result, status_code=code)
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
    include_delisted: bool = False,
):
    """土地物件一覧"""
    rows = db.get_land_listings(
        station=station, min_price=min_price, max_price=max_price,
        min_area=min_area, status=status, limit=limit,
        active_only=not include_delisted,
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

    updated = db.get_land_listing_by_id(listing_id)
    return JSONResponse(content={"status": "ok", "listing": updated})


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
            pref_codes = data.get("prefecture_codes")
            if isinstance(pref_codes, str):
                pref_codes = _expand_prefecture_codes(pref_codes)
            if not pref_codes:
                pref_codes = _expand_prefecture_codes(data.get("prefecture_code", "13")) or ["13"]

            total = {"listings_saved": 0, "plans_generated": 0, "asset_scores_generated": 0}
            for pref in pref_codes:
                part = batch_processor.run_land_pipeline(
                    source=data.get("source", "suumo"),
                    pref=pref,
                    price_min=data.get("price_min"),
                    price_max=data.get("price_max"),
                    area_min=data.get("area_min"),
                    walk_max=data.get("walk_max"),
                    max_pages=data.get("max_pages", 3),
                ) or {}
                total["listings_saved"] += int(part.get("listings_saved") or 0)
                total["plans_generated"] += int(part.get("plans_generated") or 0)
                total["asset_scores_generated"] += int(part.get("asset_scores_generated") or 0)
            result = {"prefecture_codes": pref_codes, **total}
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
        listing_data = _enrich_listing_regulations_if_needed(listing_id, listing_data)
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
        # 分析直前に用途地域/建蔽率/容積率の欠損をAPI補完して取りこぼしを減らす
        enriched = batch_processor.batch_enrich_from_api(limit=2000)
        plans = batch_processor.batch_building_plans()
        return JSONResponse(content={
            "status": "ok",
            "regulations_enriched": enriched,
            "plans_generated": plans,
        })
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
        listing_data = _enrich_listing_regulations_if_needed(listing_id, listing_data)
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
                   NULL as building_coverage_ratio,
                   NULL as floor_area_ratio, NULL as zoning,
                   p.latitude, p.longitude, p.source, p.source_url,
                   p.structure as structure_type, NULL as floors, NULL as unit_size_sqm,
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


@app.get("/api/compare/high-rank-picks")
async def get_high_rank_picks(
    min_grade: str = "B",
    min_score: float = 55.0,
    limit: int = 30,
):
    """
    高ランク物件を即ピックアップするための軽量API。
    - grade優先（S > A > B > C > D > F）
    - 同grade内は score / yield で降順
    """
    min_grade = (min_grade or "B").upper().strip()
    allowed = {"S", "A", "B", "C", "D", "F"}
    if min_grade not in allowed:
        min_grade = "B"

    grade_order = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "F": 5}
    max_idx = grade_order[min_grade]
    target_grades = [g for g, idx in grade_order.items() if idx <= max_idx]
    placeholders = ",".join("?" for _ in target_grades)
    safe_limit = max(1, min(int(limit), 200))

    rows = []
    with db._conn() as conn:
        # 収益物件（latest judgment）
        prop_sql = f"""
            SELECT
                p.id AS id,
                'property' AS type,
                COALESCE(p.name, p.address, '収益物件') AS name,
                p.address AS address,
                p.latitude AS latitude,
                p.longitude AS longitude,
                p.source_url AS source_url,
                p.gross_yield AS estimated_yield,
                j.grade AS grade,
                j.overall_score AS score,
                j.recommendation AS recommendation
            FROM properties p
            JOIN judgments j ON j.property_id = p.id
            AND j.id = (
                SELECT j2.id FROM judgments j2
                WHERE j2.property_id = p.id
                ORDER BY j2.id DESC LIMIT 1
            )
            WHERE j.grade IN ({placeholders})
            AND COALESCE(j.overall_score, 0) >= ?
            LIMIT ?
        """
        prop_params = [*target_grades, float(min_score), safe_limit]
        prop_rows = conn.execute(prop_sql, prop_params).fetchall()
        rows.extend([dict(r) for r in prop_rows])

        # 土地物件（latest judgment + best plan）
        land_sql = f"""
            SELECT
                ll.id AS id,
                'land' AS type,
                COALESCE(ll.address, '土地物件') AS name,
                ll.address AS address,
                ll.latitude AS latitude,
                ll.longitude AS longitude,
                ll.source_url AS source_url,
                bp.estimated_yield AS estimated_yield,
                lj.grade AS grade,
                lj.overall_score AS score,
                lj.recommendation AS recommendation
            FROM land_listings ll
            JOIN land_judgments lj ON lj.land_listing_id = ll.id
            AND lj.id = (
                SELECT lj2.id FROM land_judgments lj2
                WHERE lj2.land_listing_id = ll.id
                ORDER BY lj2.id DESC LIMIT 1
            )
            LEFT JOIN building_plans bp ON bp.land_listing_id = ll.id
            AND bp.id = (
                SELECT bp2.id FROM building_plans bp2
                WHERE bp2.land_listing_id = ll.id
                ORDER BY bp2.estimated_yield DESC LIMIT 1
            )
            WHERE ll.duplicate_of_id IS NULL
            AND lj.grade IN ({placeholders})
            AND COALESCE(lj.overall_score, 0) >= ?
            LIMIT ?
        """
        land_params = [*target_grades, float(min_score), safe_limit]
        land_rows = conn.execute(land_sql, land_params).fetchall()
        rows.extend([dict(r) for r in land_rows])

    def _sort_key(r):
        return (
            -grade_order.get((r.get("grade") or "F"), 9),
            float(r.get("score") or 0.0),
            float(r.get("estimated_yield") or 0.0),
        )

    rows.sort(key=_sort_key, reverse=True)
    rows = rows[:safe_limit]

    picks = []
    for idx, r in enumerate(rows, start=1):
        picks.append({
            "rank": idx,
            "id": r.get("id"),
            "type": r.get("type"),
            "name": r.get("name") or r.get("address") or "物件",
            "address": r.get("address") or "",
            "grade": r.get("grade") or "?",
            "score": float(r.get("score") or 0.0),
            "estimated_yield": float(r.get("estimated_yield") or 0.0),
            "recommendation": r.get("recommendation") or "",
            "latitude": r.get("latitude"),
            "longitude": r.get("longitude"),
            "source_url": r.get("source_url"),
        })

    return JSONResponse(content={
        "count": len(picks),
        "min_grade": min_grade,
        "min_score": float(min_score),
        "picks": picks,
    })


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
    scheduler.set_pipeline(ingest_pipeline)
    scheduler.start()
    return JSONResponse(content={"status": "ok", **scheduler.get_status()})


@app.post("/api/scheduler/stop")
async def stop_scheduler():
    """定期スクレイピング停止"""
    from engine.scheduler import scheduler
    scheduler.stop()
    return JSONResponse(content={"status": "ok", **scheduler.get_status()})


@app.post("/api/scheduler/run-now")
async def run_scheduler_now():
    """自動取得を即時1回実行（バックグラウンド）"""
    import threading
    from engine.scheduler import scheduler

    scheduler.set_pipeline(ingest_pipeline)

    def _run():
        try:
            scheduler._run_property_ingest()
        except Exception as e:
            logging.exception("manual scheduler run failed: %s", e)
            scheduler.status["property"]["last_error"] = str(e)

    if not scheduler.is_running:
        scheduler.start()
    threading.Thread(target=_run, daemon=True, name="scheduler-run-now").start()
    return JSONResponse(content={
        "status": "started",
        "message": "自動取得を即時開始しました",
        **scheduler.get_status(),
    })


@app.get("/api/scheduler/status")
async def scheduler_status():
    """スケジューラ状態（自動スクレイプの進捗含む）"""
    from engine.scheduler import scheduler
    from storage.database import Database

    payload = scheduler.get_status()
    try:
        stats = Database().get_db_stats()
        payload["db"] = {
            "properties": stats.get("properties", 0),
            "land_listings": stats.get("land_listings", 0),
            "judgments": stats.get("judgments", 0),
        }
    except Exception:
        payload["db"] = {}
    return JSONResponse(content=payload)


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
    pref_codes = _expand_prefecture_codes(prefecture_code)
    if len(pref_codes) <= 1:
        key = pref_codes[0] if pref_codes else prefecture_code
        results = area_analyzer.analyze_all_areas(key)
    else:
        results = []
        for pref in pref_codes:
            results.extend(area_analyzer.analyze_all_areas(pref))
        results.sort(key=lambda x: x.distortion_score or 0, reverse=True)
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
    return templates.TemplateResponse(
        request,
        "analysis.html",
        {
            "center": MAP_DEFAULT_CENTER,
            "zoom": MAP_DEFAULT_ZOOM,
        },
    )


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
    verify_source_on_analyze = bool(data.get("verify_source_on_analyze", True))
    verify_source_use_browser = bool(data.get("verify_source_use_browser", False))
    source_refresh = {"checked": False, "updated": False, "reason": "skipped"}
    if verify_source_on_analyze and str(data.get("source_url") or "").strip():
        source_refresh["checked"] = True
        refreshed = _refresh_property_from_source(data, use_browser=verify_source_use_browser)
        source_refresh.update({
            "updated": bool(refreshed.get("updated")),
            "reason": refreshed.get("reason"),
            "changed_fields": refreshed.get("changed_fields", []),
        })
        if refreshed.get("property"):
            # 分析入力を最新DB値へ置換
            data = dict(refreshed["property"])

    prop = Property.from_dict(data)
    analysis_input_before = {
        "asking_price": prop.asking_price,
        "price_per_sqm": prop.price_per_sqm,
        "current_rent_annual": prop.current_rent_annual,
        "nearest_station": prop.nearest_station,
        "station_distance_min": prop.station_distance_min,
    }

    # ヒートマップ由来の市場データを分析入力へ反映
    market_context, auto_filled = _apply_market_context_to_property(prop, data)
    analysis_input_after = {
        "asking_price": prop.asking_price,
        "price_per_sqm": prop.price_per_sqm,
        "current_rent_annual": prop.current_rent_annual,
        "nearest_station": prop.nearest_station,
        "station_distance_min": prop.station_distance_min,
    }

    # 通常の投資判定
    result = orchestrator.run(prop)
    judgment = result["judgment"]
    critic = result.get("critic_review", {})

    # 市場データ基準との整合を評価へ反映
    market_benchmark = {}
    try:
        val = result.get("valuation")
        prop_net = getattr(val, "net_yield", None)
        prop_gross = getattr(val, "gross_yield", None)
        m_net = (market_context or {}).get("net_yield_ref")
        m_gross = (market_context or {}).get("implied_yield")
        if m_net is not None or m_gross is not None:
            market_benchmark = {
                "market_net_yield": m_net,
                "market_gross_yield": m_gross,
                "property_net_yield": prop_net,
                "property_gross_yield": prop_gross,
                "source": (market_context or {}).get("source"),
            }
        if m_net is not None and prop_net is not None:
            gap = float(prop_net) - float(m_net)
            market_benchmark["net_yield_gap"] = gap
            judgment.key_metrics["市場正味利回り"] = f"{float(m_net)*100:.1f}%"
            judgment.key_metrics["物件-市場乖離"] = f"{gap*100:+.1f}%"
            if gap >= 0.006:
                judgment.strengths.append("市場基準の正味利回りを有意に上回る")
            elif gap <= -0.006:
                judgment.weaknesses.append("市場基準の正味利回りを下回る")
                judgment.risks.append("市場比で収益性が弱く、下振れ時の耐性が低い")
    except Exception:
        pass

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

    # 単体分析結果も永続化（後から即再表示できるようにする）
    try:
        selected = {
            "scenario": "as_is",
            "grade": judgment.grade,
            "score": round(float(judgment.overall_score or 0), 2),
            "recommendation": judgment.recommendation,
            "confidence": judgment.confidence,
            "gross_yield": getattr(result.get("valuation"), "gross_yield", None),
            "net_yield": getattr(result.get("valuation"), "net_yield", None),
            "expense_rate": getattr(result.get("valuation"), "expense_rate", None),
            "irr": getattr(result.get("simulation"), "irr", None),
            "dscr": getattr(result.get("simulation"), "dscr", None),
            "year1_cash_flow": getattr(result.get("simulation"), "year1_cash_flow", None),
            "exit_cap_rate": getattr(result.get("simulation"), "hold_sell_exit_cap_base", None),
            "exit_cap_rate_stress": getattr(result.get("simulation"), "hold_sell_exit_cap_stress", None),
            "hold_sell_roi": getattr(result.get("simulation"), "hold_sell_roi_65", None),
            "hold_sell_total_return": getattr(result.get("simulation"), "hold_sell_total_return_65", None),
            "summary": judgment.summary_text,
            "market_context": market_context,
        }
        _save_analysis_cache(raw=data, selected=selected, as_is=selected, rebuild=None)
    except Exception as e:
        logging.warning(f"analyze-full cache save warning: {e}")

    return JSONResponse(content={
        "judgment": judgment.to_dict(),
        "valuation": result["valuation"].to_dict(),
        "simulation": result["simulation"].to_dict(),
        "critic_review": critic,
        "summary": judgment.summary_text,
        "asset_score": asset_score,
        "market_context": market_context,
        "market_benchmark": market_benchmark,
        "auto_filled": auto_filled,
        "analysis_input_before": analysis_input_before,
        "analysis_input_after": analysis_input_after,
        "source_refresh": source_refresh,
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


# ===== 250mメッシュ統合 =====

def _latlng_to_mesh250(lat: float, lng: float) -> str:
    """緯度経度→250mメッシュID(10桁)変換 (JIS X 0410 5次メッシュ)"""
    # 1次メッシュ
    m1_lat = int(lat * 1.5)
    m1_lng = int(lng) - 100
    # 2次メッシュ
    rlat = (lat * 1.5 - m1_lat) * 8
    rlng = (lng - int(lng)) * 8
    m2_lat = int(rlat)
    m2_lng = int(rlng)
    # 3次メッシュ
    rlat2 = (rlat - m2_lat) * 10
    rlng2 = (rlng - m2_lng) * 10
    m3_lat = int(rlat2)
    m3_lng = int(rlng2)
    # 4次メッシュ (2分の1)
    h_lat = int((rlat2 - m3_lat) * 2)
    h_lng = int((rlng2 - m3_lng) * 2)
    m4 = h_lat * 2 + h_lng + 1
    # 5次メッシュ (4分の1 = 250m)
    q_lat = int((rlat2 - m3_lat - h_lat * 0.5) * 4)
    q_lng = int((rlng2 - m3_lng - h_lng * 0.5) * 4)
    m5 = q_lat * 2 + q_lng + 1
    return f"{m1_lat:02d}{m1_lng:02d}{m2_lat}{m2_lng}{m3_lat}{m3_lng}{m4}{m5}"


def _mesh250_center(mesh_id: str):
    """250mメッシュID→中心座標"""
    if len(mesh_id) < 10:
        return None, None
    m1_lat = int(mesh_id[0:2])
    m1_lng = int(mesh_id[2:4])
    m2_lat = int(mesh_id[4])
    m2_lng = int(mesh_id[5])
    m3_lat = int(mesh_id[6])
    m3_lng = int(mesh_id[7])
    m4 = int(mesh_id[8])
    m5 = int(mesh_id[9])

    h_lat = (m4 - 1) // 2
    h_lng = (m4 - 1) % 2
    q_lat = (m5 - 1) // 2
    q_lng = (m5 - 1) % 2

    lat = (m1_lat + (m2_lat + (m3_lat + (h_lat + (q_lat + 0.5) / 2) / 2) / 10) / 8) / 1.5
    lng = m1_lng + 100 + (m2_lng + (m3_lng + (h_lng + (q_lng + 0.5) / 2) / 2) / 10) / 8
    return lat, lng


@app.post("/api/mesh/compute")
async def compute_mesh_250m():
    """全データを250mメッシュに集約計算 + 空間補間で欠損メッシュ推定（バックグラウンド）"""
    import threading

    def _run():
        import math
        logging.info("=== 250mメッシュ集計開始 ===")
        mesh_data = {}  # mesh_id -> {lp: [], rent: [], tx: [], ...}

        with db._conn() as conn:
            # 地価データ
            for r in conn.execute("SELECT latitude, longitude, price_per_sqm FROM api_land_prices WHERE latitude IS NOT NULL AND price_per_sqm > 0").fetchall():
                d = dict(r)
                mid = _latlng_to_mesh250(d["latitude"], d["longitude"])
                mesh_data.setdefault(mid, {"lp": [], "rent": [], "tx": []})
                mesh_data[mid]["lp"].append(d["price_per_sqm"])

            # 賃料データ
            for r in conn.execute("SELECT latitude, longitude, rent_per_sqm FROM rental_comps WHERE latitude IS NOT NULL AND latitude > 0 AND rent_per_sqm > 0").fetchall():
                d = dict(r)
                mid = _latlng_to_mesh250(d["latitude"], d["longitude"])
                mesh_data.setdefault(mid, {"lp": [], "rent": [], "tx": []})
                mesh_data[mid]["rent"].append(d["rent_per_sqm"])

            # 取引データ
            for r in conn.execute("SELECT latitude, longitude, price_per_sqm FROM transactions WHERE latitude IS NOT NULL AND price_per_sqm > 0 AND price_per_sqm < 50000000").fetchall():
                d = dict(r)
                mid = _latlng_to_mesh250(d["latitude"], d["longitude"])
                mesh_data.setdefault(mid, {"lp": [], "rent": [], "tx": []})
                mesh_data[mid]["tx"].append(d["price_per_sqm"])

            # 人口データ
            pop_by_mesh = {}
            for r in conn.execute("SELECT mesh_id, pop_current, pop_future, change_rate FROM api_population_mesh WHERE pop_current IS NOT NULL").fetchall():
                d = dict(r)
                pop_by_mesh[d["mesh_id"]] = d

            # 施設データ
            fac_counts = {}
            for r in conn.execute("SELECT latitude, longitude, category FROM api_facilities WHERE latitude IS NOT NULL").fetchall():
                d = dict(r)
                mid = _latlng_to_mesh250(d["latitude"], d["longitude"])
                fac_counts.setdefault(mid, {"school": 0, "medical": 0, "childcare": 0})
                cat = d.get("category", "")
                if cat in fac_counts[mid]:
                    fac_counts[mid][cat] += 1

            # 駅データ
            stations_list = conn.execute("SELECT station_name, latitude, longitude FROM stations WHERE latitude IS NOT NULL").fetchall()
            stations_coords = [(dict(r)["station_name"], dict(r)["latitude"], dict(r)["longitude"]) for r in stations_list]
            # 既存メッシュID（過去集計の残件も再計算対象に含める）
            existing_mesh_ids = set(
                dict(r)["mesh_id"]
                for r in conn.execute("SELECT mesh_id FROM mesh_250m").fetchall()
                if dict(r).get("mesh_id")
            )

        # 集計してDB保存
        import statistics
        records = []
        all_mids = set(mesh_data.keys()) | set(pop_by_mesh.keys()) | set(fac_counts.keys()) | set(existing_mesh_ids)

        for mid in all_mids:
            clat, clng = _mesh250_center(mid)
            if clat is None:
                continue

            md = mesh_data.get(mid, {"lp": [], "rent": [], "tx": []})
            pop = pop_by_mesh.get(mid, {})
            fac = fac_counts.get(mid, {})

            # 最寄駅
            nearest_st = ""
            nearest_dist = 999
            for sname, slat, slng in stations_coords:
                d = math.sqrt((clat - slat) ** 2 + (clng - slng) ** 2) * 111
                if d < nearest_dist:
                    nearest_dist = d
                    nearest_st = sname

            records.append({
                "mesh_id": mid,
                "center_lat": clat,
                "center_lng": clng,
                "avg_land_price_sqm": statistics.mean(md["lp"]) if md["lp"] else None,
                "land_price_count": len(md["lp"]),
                "avg_rent_sqm": statistics.mean(md["rent"]) if md["rent"] else None,
                "rent_count": len(md["rent"]),
                "avg_tx_price_sqm": statistics.mean(md["tx"]) if md["tx"] else None,
                "tx_count": len(md["tx"]),
                "pop_current": pop.get("pop_current"),
                "pop_future": pop.get("pop_future"),
                "pop_change_rate": pop.get("change_rate"),
                "school_count": fac.get("school", 0),
                "medical_count": fac.get("medical", 0),
                "childcare_count": fac.get("childcare", 0),
                "nearest_station": nearest_st,
                "station_dist_km": round(nearest_dist, 2),
            })

        # === 空間補間: 人口ありメッシュのみ対象、駅距離減衰モデル ===
        logging.info("=== 空間補間開始 ===")
        observed = {r["mesh_id"]: r for r in records}

        # --- 陸地マスク: 人口/施設/実データありメッシュのいずれかを補間対象 ---
        data_meshes = set(mid for mid, md in mesh_data.items()
                         if md["lp"] or md["rent"] or md["tx"])
        land_meshes = set(pop_by_mesh.keys()) | set(fac_counts.keys()) | data_meshes | set(existing_mesh_ids)
        logging.info(f"  陸地メッシュ(人口/施設/実データ): {len(land_meshes)}")

        # --- グリッドインデックス構築 ---
        GRID_SIZE = 0.02  # 約2km単位
        def _grid_key(lat, lng):
            return (int(lat / GRID_SIZE), int(lng / GRID_SIZE))

        station_grid = {}
        for sname, slat, slng in stations_coords:
            gk = _grid_key(slat, slng)
            station_grid.setdefault(gk, []).append((sname, slat, slng))

        def _nearest_station_fast(lat, lng):
            gk = _grid_key(lat, lng)
            best_name, best_dist = "", 999
            for di in range(-1, 2):
                for dj in range(-1, 2):
                    for sn, sl, sg in station_grid.get((gk[0]+di, gk[1]+dj), []):
                        d = math.sqrt((lat - sl) ** 2 + (lng - sg) ** 2) * 111
                        if d < best_dist:
                            best_dist = d
                            best_name = sn
            return best_name, best_dist

        def _build_point_grid(points):
            grid = {}
            for plat, plng, val, st_km in points:
                gk = _grid_key(plat, plng)
                grid.setdefault(gk, []).append((plat, plng, val, st_km))
            return grid

        lp_points = [(r["center_lat"], r["center_lng"], r["avg_land_price_sqm"], r["station_dist_km"])
                     for r in records if r["avg_land_price_sqm"]]
        rent_points = [(r["center_lat"], r["center_lng"], r["avg_rent_sqm"], r["station_dist_km"])
                       for r in records if r["avg_rent_sqm"]]
        tx_points = [(r["center_lat"], r["center_lng"], r["avg_tx_price_sqm"], r["station_dist_km"])
                     for r in records if r["avg_tx_price_sqm"]]

        # 地価推定にtransactionsも活用（api_land_pricesが疎な地域のカバレッジ向上）
        for r in records:
            if r["avg_tx_price_sqm"] and not r["avg_land_price_sqm"]:
                lp_points.append((r["center_lat"], r["center_lng"], r["avg_tx_price_sqm"], r["station_dist_km"]))

        lp_grid = _build_point_grid(lp_points)
        rent_grid = _build_point_grid(rent_points)
        tx_grid = _build_point_grid(tx_points)

        # --- 賃料推定用: 地価→賃料変換の基準利回り（駅別 + 全体） ---
        station_yield_map = {}
        station_rent_map = {}
        global_yields = []
        global_rents = []
        rent_values = sorted([v for _, _, v, _ in rent_points if v and v > 0])
        rent_min = rent_values[max(0, int(len(rent_values) * 0.03))] if rent_values else 500
        rent_max = rent_values[min(len(rent_values) - 1, int(len(rent_values) * 0.97))] if rent_values else 15000
        tmp_yields = {}
        tmp_rents = {}
        for r in records:
            lp0 = r.get("avg_land_price_sqm")
            rent0 = r.get("avg_rent_sqm")
            st0 = r.get("nearest_station") or ""
            if rent0 and rent0 > 0:
                global_rents.append(rent0)
                tmp_rents.setdefault(st0, []).append(rent0)
            if not lp0 or not rent0 or lp0 <= 0 or rent0 <= 0:
                continue
            y = rent0 * 12 / lp0  # 年間賃料 / ㎡地価
            if 0.015 <= y <= 0.18:
                global_yields.append(y)
                tmp_yields.setdefault(st0, []).append(y)
        for st, ys in tmp_yields.items():
            if len(ys) >= 5:
                station_yield_map[st] = statistics.median(ys)
        for st, rs in tmp_rents.items():
            if len(rs) >= 5:
                station_rent_map[st] = statistics.median(rs)
        default_yield = statistics.median(global_yields) if global_yields else 0.055
        default_rent = statistics.median(global_rents) if global_rents else 3200

        # --- 駅距離による賃料減衰関数 ---
        # 賃料は駅距離に対して対数減衰: 徒歩1分≈80m=0.08km
        # 実測データから: 徒歩5分を基準(1.0)、10分で0.93、15分で0.88、20分で0.80
        def _station_distance_factor(dist_km):
            """駅距離(km) → 賃料補正係数 (0.4km=徒歩5分を1.0基準)"""
            base_km = 0.4  # 徒歩5分
            if dist_km <= 0.08:  # 駅直結
                return 1.06
            if dist_km <= base_km:
                return 1.0 + 0.06 * (1 - dist_km / base_km)  # 1.0〜1.06
            # 対数減衰: dist増で緩やかに下落、2km超で0.75まで
            ratio = dist_km / base_km  # >1
            return max(0.70, 1.0 - 0.08 * math.log(ratio))

        def _idw_estimate(target_lat, target_lng, target_st_km, point_grid,
                          k=10, power=2.0, max_dist_km=2.5, min_points=3):
            """IDW補間 + 駅距離減衰モデル"""
            gk = _grid_key(target_lat, target_lng)
            dists = []
            # GRID_SIZE=0.02 なので半径2.5kmは±2セル検索
            for di in range(-2, 3):
                for dj in range(-2, 3):
                    for plat, plng, val, st_km in point_grid.get((gk[0]+di, gk[1]+dj), []):
                        d = math.sqrt((target_lat - plat) ** 2 + (target_lng - plng) ** 2) * 111
                        if d < max_dist_km:
                            dists.append((d, val, st_km))

            if len(dists) < min_points:
                return None
            dists.sort(key=lambda x: x[0])
            dists = dists[:k]

            # IDW: 逆距離2乗加重平均（駅距離正規化済み値で補間）
            w_sum = 0.0
            v_sum = 0.0
            for d, val, st_km in dists:
                # 各参照ポイントの値を「駅距離5分相当」に正規化
                src_factor = _station_distance_factor(st_km) if st_km and st_km > 0 else 1.0
                normalized_val = val / src_factor  # 駅距離の影響を除去した基準値

                w = 1.0 / max(d, 0.02) ** power
                w_sum += w
                v_sum += w * normalized_val

            if w_sum == 0:
                return None

            # 基準値（駅5分相当）のIDW
            base_val = v_sum / w_sum

            # ターゲットの駅距離で減衰適用
            target_factor = _station_distance_factor(target_st_km) if target_st_km and target_st_km > 0 else 0.9
            result = base_val * target_factor

            return round(result)

        # --- 補間候補: 人口/施設ありメッシュのうち「価格系実データなし」のもの ---
        # observed にはpopだけのメッシュも含まれるので、価格データの有無で判定
        has_price_data = set()
        for r in records:
            if r["avg_land_price_sqm"] or r["avg_rent_sqm"] or r["avg_tx_price_sqm"]:
                has_price_data.add(r["mesh_id"])
        candidate_meshes = land_meshes - has_price_data
        logging.info(f"  補間候補(陸地&価格データなし): {len(candidate_meshes)}メッシュ")

        interpolated_count = 0
        for mid in candidate_meshes:
            clat, clng = _mesh250_center(mid)
            if clat is None:
                continue

            n_st, n_dist = _nearest_station_fast(clat, clng)

            # まず近傍IDW（精度優先）
            est_lp = _idw_estimate(clat, clng, n_dist, lp_grid, k=10, power=2.0, max_dist_km=2.5, min_points=3)
            est_rent = _idw_estimate(clat, clng, n_dist, rent_grid, k=10, power=2.0, max_dist_km=2.5, min_points=3)
            est_tx = _idw_estimate(clat, clng, n_dist, tx_grid, k=10, power=2.0, max_dist_km=2.5, min_points=3)

            # 近傍で不足する場合は広域IDW（埋め率優先）
            if est_lp is None:
                est_lp = _idw_estimate(clat, clng, n_dist, lp_grid, k=6, power=1.5, max_dist_km=8.0, min_points=1)
            if est_rent is None:
                est_rent = _idw_estimate(clat, clng, n_dist, rent_grid, k=6, power=1.5, max_dist_km=8.0, min_points=1)
            if est_tx is None:
                est_tx = _idw_estimate(clat, clng, n_dist, tx_grid, k=6, power=1.5, max_dist_km=8.0, min_points=1)

            # 賃料が未推定なら、地価（推定/実測）と駅別利回りから逆算
            if est_rent is None:
                base_lp = est_lp or est_tx
                if base_lp and base_lp > 0:
                    y = station_yield_map.get(n_st) or default_yield
                    est_rent = round(base_lp * y / 12)
                    est_rent = max(int(rent_min), min(int(rent_max), est_rent))
            # それでも未推定なら駅別/全体賃料に駅距離減衰を適用
            if est_rent is None:
                base_rent = station_rent_map.get(n_st) or default_rent
                # 極端に遠方は減衰を強める
                dist_factor = _station_distance_factor(min(max(n_dist, 0.08), 6.0))
                est_rent = round(base_rent * dist_factor)
                est_rent = max(int(rent_min), min(int(rent_max), est_rent))

            if not est_lp and not est_rent and not est_tx:
                continue

            # 人口データを引き継ぎ
            pop_d = pop_by_mesh.get(mid, {})
            fac_d = fac_counts.get(mid, {})

            rec = {
                "mesh_id": mid,
                "center_lat": clat,
                "center_lng": clng,
                "avg_land_price_sqm": est_lp,
                "land_price_count": -1 if est_lp else 0,
                "avg_rent_sqm": est_rent,
                "rent_count": -1 if est_rent else 0,
                "avg_tx_price_sqm": est_tx,
                "tx_count": -1 if est_tx else 0,
                "pop_current": pop_d.get("pop_current"),
                "pop_future": pop_d.get("pop_future"),
                "pop_change_rate": pop_d.get("change_rate"),
                "school_count": fac_d.get("school", 0),
                "medical_count": fac_d.get("medical", 0),
                "childcare_count": fac_d.get("childcare", 0),
                "nearest_station": n_st,
                "station_dist_km": round(n_dist, 2),
            }
            records.append(rec)
            observed[mid] = rec
            interpolated_count += 1

        # --- 最終フォールバック: 残欠損を駅別/全体基準で埋める（充填率最大化） ---
        station_lp_map, station_tx_map = {}, {}
        global_lp_vals, global_tx_vals = [], []
        tmp_lp, tmp_tx = {}, {}
        for r in records:
            st = r.get("nearest_station") or ""
            lpv = r.get("avg_land_price_sqm")
            txv = r.get("avg_tx_price_sqm")
            if lpv and lpv > 0:
                global_lp_vals.append(lpv)
                tmp_lp.setdefault(st, []).append(lpv)
            if txv and txv > 0:
                global_tx_vals.append(txv)
                tmp_tx.setdefault(st, []).append(txv)
        for st, vals in tmp_lp.items():
            if len(vals) >= 3:
                station_lp_map[st] = statistics.median(vals)
        for st, vals in tmp_tx.items():
            if len(vals) >= 3:
                station_tx_map[st] = statistics.median(vals)
        global_lp = statistics.median(global_lp_vals) if global_lp_vals else None
        global_tx = statistics.median(global_tx_vals) if global_tx_vals else global_lp
        # ハードフォールバック（全域定義保証）
        HARD_DEFAULT_LP = 220000.0
        HARD_DEFAULT_TX = 220000.0
        HARD_DEFAULT_RENT = 2500.0
        if not global_lp or global_lp <= 0:
            global_lp = HARD_DEFAULT_LP
        if not global_tx or global_tx <= 0:
            global_tx = max(global_lp, HARD_DEFAULT_TX)

        fallback_count = 0
        for r in records:
            st = r.get("nearest_station") or ""
            dkm = float(r.get("station_dist_km") or 1.0)
            dist_factor = max(0.72, min(1.10, _station_distance_factor(dkm)))
            pop_cr = r.get("pop_change_rate")
            pop_in = max(0.0, float(pop_cr) / 100.0) if pop_cr is not None else 0.0
            core_bias = max(0.0, (1.2 - min(dkm, 1.2)) / 1.2)
            pressure_idx = max(0.0, min(1.0, 0.65 * min(0.25, pop_in) / 0.25 + 0.35 * core_bias))
            price_pressure = 1.0 + 0.18 * pressure_idx
            # 賃料は上がるが、価格の上がりよりは弱い（利回り圧縮）
            rent_pressure = 1.0 + 0.08 * pressure_idx

            if not (r.get("avg_land_price_sqm") and r.get("avg_land_price_sqm") > 0):
                base_lp = station_lp_map.get(st) or global_lp
                if base_lp:
                    r["avg_land_price_sqm"] = round(base_lp * dist_factor * price_pressure)
                    r["land_price_count"] = -1
                    fallback_count += 1

            if not (r.get("avg_tx_price_sqm") and r.get("avg_tx_price_sqm") > 0):
                base_tx = station_tx_map.get(st) or global_tx or r.get("avg_land_price_sqm")
                if not base_tx:
                    # 賃料があれば基準利回りから逆算
                    rent_for_tx = r.get("avg_rent_sqm")
                    if rent_for_tx and default_yield and default_yield > 0:
                        base_tx = float(rent_for_tx) * 12 / float(default_yield)
                    elif global_lp:
                        base_tx = global_lp
                    else:
                        # 最終フォールバック: 首都圏郊外帯の保守値
                        base_tx = 220000
                if base_tx:
                    r["avg_tx_price_sqm"] = round(base_tx * dist_factor * (1.0 + 0.24 * pressure_idx))
                    r["tx_count"] = -1
                    fallback_count += 1

            if not (r.get("avg_rent_sqm") and r.get("avg_rent_sqm") > 0):
                base_lp_for_rent = r.get("avg_land_price_sqm") or r.get("avg_tx_price_sqm")
                if base_lp_for_rent:
                    y = station_yield_map.get(st) or default_yield
                    y_adj = max(0.018, min(0.12, float(y) * (1.0 - 0.22 * pressure_idx)))
                    est_rent = round(float(base_lp_for_rent) * y_adj / 12 * rent_pressure)
                    est_rent = max(int(rent_min), min(int(rent_max), est_rent))
                    r["avg_rent_sqm"] = est_rent
                    r["rent_count"] = -1
                    fallback_count += 1
                else:
                    # 最終保証値（全メッシュ定義）
                    est_rent = round(HARD_DEFAULT_RENT * max(0.72, min(1.08, dist_factor)))
                    r["avg_rent_sqm"] = max(int(rent_min), min(int(rent_max), est_rent))
                    r["rent_count"] = -1
                    fallback_count += 1

            # 全領域定義の最終安全網
            if not (r.get("avg_land_price_sqm") and r.get("avg_land_price_sqm") > 0):
                r["avg_land_price_sqm"] = round(global_lp)
                r["land_price_count"] = -1
            if not (r.get("avg_tx_price_sqm") and r.get("avg_tx_price_sqm") > 0):
                r["avg_tx_price_sqm"] = round(global_tx)
                r["tx_count"] = -1
            if not (r.get("avg_rent_sqm") and r.get("avg_rent_sqm") > 0):
                y = station_yield_map.get(st) or default_yield
                r["avg_rent_sqm"] = round(max(900, min(18000, float(r["avg_land_price_sqm"]) * float(y) / 12)))
                r["rent_count"] = -1

        logging.info(f"  空間補間完了: {interpolated_count}メッシュ推定値生成 / 最終補完: {fallback_count}項目")

        # DB保存（一括UPSERT、zoning/road列は既存値を保持）
        with db._conn() as conn:
            conn.executemany("""
                INSERT INTO mesh_250m
                (mesh_id, center_lat, center_lng,
                 avg_land_price_sqm, land_price_count,
                 avg_rent_sqm, rent_count,
                 avg_tx_price_sqm, tx_count,
                 pop_current, pop_future, pop_change_rate,
                 school_count, medical_count, childcare_count,
                 nearest_station, station_dist_km, computed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
                ON CONFLICT(mesh_id) DO UPDATE SET
                    center_lat=excluded.center_lat, center_lng=excluded.center_lng,
                    avg_land_price_sqm=excluded.avg_land_price_sqm,
                    land_price_count=excluded.land_price_count,
                    avg_rent_sqm=excluded.avg_rent_sqm,
                    rent_count=excluded.rent_count,
                    avg_tx_price_sqm=excluded.avg_tx_price_sqm,
                    tx_count=excluded.tx_count,
                    pop_current=excluded.pop_current,
                    pop_future=excluded.pop_future,
                    pop_change_rate=excluded.pop_change_rate,
                    school_count=excluded.school_count,
                    medical_count=excluded.medical_count,
                    childcare_count=excluded.childcare_count,
                    nearest_station=excluded.nearest_station,
                    station_dist_km=excluded.station_dist_km,
                    computed_at=datetime('now','localtime')
            """, [
                (r["mesh_id"], r["center_lat"], r["center_lng"],
                 r["avg_land_price_sqm"], r["land_price_count"],
                 r["avg_rent_sqm"], r["rent_count"],
                 r["avg_tx_price_sqm"], r["tx_count"],
                 r["pop_current"], r["pop_future"], r["pop_change_rate"],
                 r["school_count"], r["medical_count"], r["childcare_count"],
                 r["nearest_station"], r["station_dist_km"])
                for r in records
            ])

        logging.info(f"=== 250mメッシュ集計完了: {len(records)}メッシュ (実データ+推定{interpolated_count}) ===")

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse(content={"status": "started", "message": "250mメッシュ集計+空間補間を開始しました"})


@app.get("/api/layers/mesh-transactions")
async def layer_mesh_transactions(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
):
    """取引実績レイヤー（250mメッシュ粒度）: 座標付きtransactionsから直接GeoJSON"""
    features = []
    with db._conn() as conn:
        # 250mメッシュで集計
        rows = conn.execute("""
            SELECT
                CAST(latitude * 4000 AS INTEGER) AS lat_bin,
                CAST(longitude * 3200 AS INTEGER) AS lng_bin,
                AVG(latitude) clat, AVG(longitude) clng,
                AVG(price_per_sqm) avg_psm,
                MIN(price_per_sqm) min_psm,
                MAX(price_per_sqm) max_psm,
                COUNT(*) cnt,
                AVG(transaction_price) avg_total,
                property_type
            FROM transactions
            WHERE price_per_sqm > 0 AND price_per_sqm < 50000000
            AND latitude IS NOT NULL
            AND latitude BETWEEN ? AND ?
            AND longitude BETWEEN ? AND ?
            GROUP BY lat_bin, lng_bin, property_type
            HAVING cnt >= 2
            LIMIT 5000
        """, (south, north, west, east)).fetchall()

    for r in [dict(x) for x in rows]:
        psm = round(r["avg_psm"])
        color = ('#880e4f' if psm > 1000000 else '#d32f2f' if psm > 500000 else
                 '#ff6f00' if psm > 300000 else '#fbc02d' if psm > 150000 else '#66bb6a')
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["clng"], r["clat"]]},
            "properties": {
                "avg_price_sqm": psm,
                "min_price_sqm": round(r["min_psm"]),
                "max_price_sqm": round(r["max_psm"]),
                "avg_total": round(r["avg_total"]),
                "count": r["cnt"],
                "type": r.get("property_type", ""),
                "color": color,
            },
        })

    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features)},
    })


@app.get("/api/layers/mesh-250m")
async def layer_mesh_250m(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
    metric: str = "land_price",
    layout: str = "",
):
    """250mメッシュ統合レイヤー: 地価/賃料/取引/人口を1メッシュで比較表示

    metric: land_price / rent / tx_price / population / pop_density / yield / facility
    layout: 賃料間取りフィルタ（1K, 1LDK等。カンマ区切り可）
    """
    features = []

    # 人口系メトリクスはapi_population_meshから直接取得（カバレッジ最大化）
    if metric in ("population", "pop_density"):
        with db._conn() as conn:
            rows = conn.execute("""
                SELECT p.mesh_id, p.center_lat, p.center_lng,
                       p.pop_current, p.pop_future, p.change_rate
                FROM api_population_mesh p
                WHERE p.center_lat BETWEEN ? AND ? AND p.center_lng BETWEEN ? AND ?
                AND p.pop_current IS NOT NULL
                LIMIT 20000
            """, (south, north, west, east)).fetchall()

        dLat = 0.00208 / 2
        dLng = 0.003125 / 2
        for r in [dict(x) for x in rows]:
            pop = r.get("pop_current") or 0
            pop_cr = r.get("change_rate")
            pop_density = round(pop * 16) if pop else 0

            if metric == "population":
                value = pop_cr if pop_cr is not None else 0
            else:
                value = pop_density

            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["center_lng"], r["center_lat"]]},
                "properties": {
                    "mesh_id": r["mesh_id"],
                    "value": value,
                    "estimated": False,
                    "pop": round(pop) if pop else None,
                    "pop_density": pop_density,
                    "pop_change": pop_cr,
                    "pop_future": round(r.get("pop_future") or 0) if r.get("pop_future") else None,
                },
            })

        return JSONResponse(content={
            "type": "FeatureCollection", "features": features,
            "_meta": {"count": len(features), "metric": metric},
        })

    # 間取りフィルタ付き賃料: rental_compsから動的集計
    rent_override = {}
    if layout and metric in ("rent", "yield"):
        layout_patterns = [l.strip() for l in layout.split(",")]
        with db._conn() as conn:
            where_clauses = " OR ".join(["rc.layout LIKE ?" for _ in layout_patterns])
            params = [f"%{lp}%" for lp in layout_patterns]
            params.extend([south, north, west, east])
            rows_rc = conn.execute(f"""
                SELECT CAST(rc.latitude * 4000 AS INTEGER) AS lat_bin,
                       CAST(rc.longitude * 3200 AS INTEGER) AS lng_bin,
                       AVG(rc.latitude) clat, AVG(rc.longitude) clng,
                       AVG(rc.rent_per_sqm) avg_r, COUNT(*) cnt
                FROM rental_comps rc
                WHERE rc.rent_per_sqm > 0 AND rc.latitude > 0
                AND ({where_clauses})
                AND rc.latitude BETWEEN ? AND ? AND rc.longitude BETWEEN ? AND ?
                GROUP BY lat_bin, lng_bin HAVING cnt >= 1
            """, params).fetchall()
            for r in rows_rc:
                d = dict(r)
                key = f"{d['lat_bin']}_{d['lng_bin']}"
                rent_override[key] = {"rent": d["avg_r"], "cnt": d["cnt"],
                                      "lat": d["clat"], "lng": d["clng"]}

    with db._conn() as conn:
        rows = conn.execute("""
            SELECT mesh_id, center_lat, center_lng,
                   avg_land_price_sqm, land_price_count,
                   avg_rent_sqm, rent_count,
                   avg_tx_price_sqm, tx_count,
                   pop_current, pop_future, pop_change_rate,
                   school_count, medical_count, childcare_count,
                   nearest_station, station_dist_km,
                   zoning, coverage_ratio, floor_area_ratio,
                   front_road, road_width_m
            FROM mesh_250m
            WHERE center_lat BETWEEN ? AND ? AND center_lng BETWEEN ? AND ?
            LIMIT 15000
        """, (south, north, west, east)).fetchall()
        station_rows = conn.execute("""
            SELECT station_name, implied_yield, sample_count_rent, sample_count_land
            FROM station_metrics
            WHERE implied_yield > 0
        """).fetchall()

    station_yield_map = {}
    station_yield_tx_map = {}
    global_yields = []
    global_yields_tx = []
    for s in [dict(x) for x in station_rows]:
        y = s.get("implied_yield")
        if y is None:
            continue
        y = float(y)
        if not (0.015 <= y <= 0.18):
            continue
        global_yields.append(y)
        if (s.get("sample_count_rent") or 0) >= 5 and (s.get("sample_count_land") or 0) >= 3:
            station_yield_map[s.get("station_name", "")] = y
    global_yield = statistics.median(global_yields) if global_yields else 0.055

    # 取引実績寄りの利回り基準（rent / tx_price）を同時に構築
    tmp_station_tx = {}
    for rr in [dict(x) for x in rows]:
        tx0 = rr.get("avg_tx_price_sqm") or 0
        rent0 = rr.get("avg_rent_sqm") or 0
        tx_cnt0 = rr.get("tx_count") or 0
        if tx0 <= 0 or rent0 <= 0:
            continue
        y_tx = (float(rent0) * 12.0) / float(tx0)
        if not (0.015 <= y_tx <= 0.18):
            continue
        # 実観測取引を優先（推定tx_count=-1は弱め）
        if tx_cnt0 > 0:
            global_yields_tx.append(y_tx)
            st0 = rr.get("nearest_station") or ""
            tmp_station_tx.setdefault(st0, []).append(y_tx)
    for st, ys in tmp_station_tx.items():
        if len(ys) >= 3:
            station_yield_tx_map[st] = statistics.median(ys)
    global_yield_tx = statistics.median(global_yields_tx) if global_yields_tx else None

    # 都心圧力（資金流入）を反映するための分位点
    def _q(vals: list[float], q: float, default: float = 0.0) -> float:
        if not vals:
            return default
        xs = sorted(vals)
        i = int((len(xs) - 1) * max(0.0, min(1.0, q)))
        return float(xs[i])

    lp_vals = [float((dict(x).get("avg_land_price_sqm") or 0)) for x in rows if (dict(x).get("avg_land_price_sqm") or 0) > 0]
    tx_vals = [float((dict(x).get("avg_tx_price_sqm") or 0)) for x in rows if (dict(x).get("avg_tx_price_sqm") or 0) > 0]
    rent_vals = [float((dict(x).get("avg_rent_sqm") or 0)) for x in rows if (dict(x).get("avg_rent_sqm") or 0) > 0]
    fac_vals = [float(((dict(x).get("school_count") or 0) + (dict(x).get("medical_count") or 0) + (dict(x).get("childcare_count") or 0))) for x in rows]
    global_lp_median = statistics.median(lp_vals) if lp_vals else 220000.0
    global_tx_median = statistics.median(tx_vals) if tx_vals else 240000.0
    global_rent_median = statistics.median(rent_vals) if rent_vals else 3000.0
    lp_p70, lp_p90 = _q(lp_vals, 0.70, 180000), _q(lp_vals, 0.90, 380000)
    tx_p70, tx_p90 = _q(tx_vals, 0.70, 210000), _q(tx_vals, 0.90, 430000)
    rent_min_clip, rent_max_clip = _q(rent_vals, 0.03, 800), _q(rent_vals, 0.97, 15000)
    lp_min_clip, lp_max_clip = _q(lp_vals, 0.02, 50000), _q(lp_vals, 0.98, 5000000)
    tx_min_clip, tx_max_clip = _q(tx_vals, 0.02, 50000), _q(tx_vals, 0.98, 5000000)
    fac_p70 = _q(fac_vals, 0.70, 4.0)

    def _rank_between(v: float, lo: float, hi: float) -> float:
        if hi <= lo:
            return 0.0
        return max(0.0, min(1.0, (v - lo) / (hi - lo)))

    def _tx_reliability_weight(tx_count_value: int) -> float:
        """tx_count件数帯ごとの取引実績信頼度
        1件: 弱、2-3件: 中、4件以上: 強
        """
        c = int(tx_count_value or 0)
        if c >= 4:
            return 0.78
        if c >= 2:
            return 0.58
        if c >= 1:
            return 0.38
        return 0.18

    for r in [dict(x) for x in rows]:
        lp = r.get("avg_land_price_sqm") or 0
        rent = r.get("avg_rent_sqm") or 0

        # 間取りフィルタoverride
        if rent_override:
            lat_bin = int(r["center_lat"] * 4000)
            lng_bin = int(r["center_lng"] * 3200)
            key = f"{lat_bin}_{lng_bin}"
            ov = rent_override.get(key)
            if ov:
                rent = ov["rent"]
            elif metric == "rent":
                continue  # フィルタ指定時はデータなしメッシュをスキップ
        tx = r.get("avg_tx_price_sqm") or 0
        pop = r.get("pop_current") or 0
        pop_cr = r.get("pop_change_rate")

        # 取得信頼度に応じた取得価格（地価/取引の加重ブレンド）
        lp_cnt = r.get("land_price_count") or 0
        tx_cnt = r.get("tx_count") or 0
        w_lp = 1.0 if lp_cnt > 0 else (0.6 if lp_cnt < 0 else 0.0)
        w_tx = 0.9 if tx_cnt > 0 else (0.5 if tx_cnt < 0 else 0.0)
        if lp > 0 and tx > 0 and (w_lp + w_tx) > 0:
            effective_lp = (lp * w_lp + tx * w_tx) / (w_lp + w_tx)
        else:
            effective_lp = lp or tx

        # ---- 都心圧力による推定整合補正 ----
        dkm = float(r.get("station_dist_km") or 0.9)
        pop_cr = r.get("pop_change_rate")
        pop_in = max(0.0, float(pop_cr) / 10.0) if pop_cr is not None else 0.0  # +10%で1.0
        near_core = max(0.0, (1.2 - min(dkm, 1.2)) / 1.2)
        fac_total = (r.get("school_count") or 0) + (r.get("medical_count") or 0) + (r.get("childcare_count") or 0)
        fac_rank = _rank_between(float(fac_total), fac_p70, max(fac_p70 + 3.0, fac_p70 * 1.8))
        lp_rank = _rank_between(float(effective_lp or 0), lp_p70, max(lp_p90, lp_p70 + 1))
        tx_rank = _rank_between(float(tx or 0), tx_p70, max(tx_p90, tx_p70 + 1))
        pressure = max(0.0, min(1.0, 0.33 * lp_rank + 0.26 * tx_rank + 0.22 * pop_in + 0.14 * near_core + 0.05 * fac_rank))
        speculative_heat = max(0.0, min(1.0, ((float(tx or 0) / max(float(effective_lp or 1), 1.0)) - 1.02) / 0.20))

        # ほかのレイヤーも整合するよう、推定値を中心に価格先行バイアスを付与
        is_lp_est = (r.get("land_price_count") or 0) < 0
        is_tx_est = (r.get("tx_count") or 0) < 0
        is_rent_est = (r.get("rent_count") or 0) < 0
        if is_lp_est and effective_lp:
            effective_lp = float(effective_lp) * (1.0 + 0.18 * pressure + 0.08 * speculative_heat)
        if is_tx_est and tx:
            tx = float(tx) * (1.0 + 0.24 * pressure + 0.14 * speculative_heat)
        if is_rent_est and rent:
            rent = float(rent) * (1.0 + 0.08 * pressure)
        if effective_lp:
            effective_lp = max(lp_min_clip, min(lp_max_clip, float(effective_lp)))
        if tx:
            tx = max(tx_min_clip, min(tx_max_clip, float(tx)))
        if rent:
            rent = max(rent_min_clip, min(rent_max_clip, float(rent)))

        # 想定利回り（正味）
        yield_estimated_fallback = False
        tx_rel = _tx_reliability_weight(tx_cnt)
        # 利回り算定の分母価格は、取引実績がある場合は取引単価を優先
        if tx and tx > 0 and tx_cnt > 0:
            # 件数が少ないときは地価系と混合してブレを抑制
            if effective_lp and effective_lp > 0:
                yield_lp = float(tx) * tx_rel + float(effective_lp) * (1.0 - tx_rel)
            else:
                yield_lp = float(tx)
        elif tx and tx > 0 and effective_lp:
            # 推定txは実勢寄りにするため混合
            yield_lp = float(effective_lp) * 0.55 + float(tx) * 0.45
        else:
            yield_lp = effective_lp
        yield_rent = rent
        st_name = r.get("nearest_station") or ""
        yref_station_tx = station_yield_tx_map.get(st_name)
        yref_station_lp = station_yield_map.get(st_name)
        # 取引実績基準を強める（存在時は優先、なければ既存基準）
        if yref_station_tx and yref_station_lp:
            yref = yref_station_tx * 0.70 + yref_station_lp * 0.30
        elif yref_station_tx:
            yref = yref_station_tx
        elif yref_station_lp:
            yref = yref_station_lp
        elif global_yield_tx:
            yref = global_yield_tx * 0.70 + global_yield * 0.30
        else:
            yref = global_yield

        # 片側欠損は駅別/全体利回りで逆算（利回りレイヤー埋め）
        if not (yield_lp > 0 and yield_rent > 0):
            if yref and yref > 0:
                if yield_lp > 0 and yield_rent <= 0:
                    yield_rent = yield_lp * yref / 12
                    yield_estimated_fallback = True
                elif yield_rent > 0 and yield_lp <= 0:
                    yield_lp = yield_rent * 12 / yref
                    yield_estimated_fallback = True

        implied_yield = None  # 表面
        net_yield = None      # 正味
        if yield_lp > 0 and yield_rent > 0:
            implied_yield = round(yield_rent * 12 / yield_lp * 100, 1)
        elif yref and yref > 0:
            implied_yield = round(yref * 100, 1)
            yield_estimated_fallback = True

        # 外れ値補正（実態に沿う帯域へ収束）
        if implied_yield is not None:
            if implied_yield < 1.5 or implied_yield > 20.0:
                if yref and yref > 0:
                    implied_yield = round(yref * 100, 1)
                    yield_estimated_fallback = True
                else:
                    implied_yield = max(1.5, min(20.0, implied_yield))

        # 取引実績がある場合は、推定利回りを取引基準へ寄せる
        if implied_yield is not None and tx and tx > 0 and yield_rent and yield_rent > 0:
            y_tx_now = (float(yield_rent) * 12.0 / float(tx)) * 100.0
            if 1.5 <= y_tx_now <= 20.0:
                # 件数帯で段階化（1件/2-3件/4件以上）
                if tx_cnt >= 4:
                    trust = 0.68
                elif tx_cnt >= 2:
                    trust = 0.52
                elif tx_cnt >= 1:
                    trust = 0.34
                else:
                    trust = 0.22
                implied_yield = round(float(implied_yield) * (1.0 - trust) + y_tx_now * trust, 1)

        # 資金流入・投機熱の強いエリアは利回りを圧縮
        if implied_yield is not None:
            compress = max(0.55, 1.0 - (0.34 * pressure + 0.22 * speculative_heat))
            implied_yield = round(implied_yield * compress, 1)
            if pressure >= 0.80:
                implied_yield = min(implied_yield, 4.6)
            elif pressure >= 0.65:
                implied_yield = min(implied_yield, 5.0)
            elif pressure >= 0.50:
                implied_yield = min(implied_yield, 5.8)
            # 都心・高価格帯はさらに上限を厳格化
            if near_core >= 0.65 and (lp_rank >= 0.70 or tx_rank >= 0.70):
                implied_yield = min(implied_yield, 4.8)
            elif near_core >= 0.50 and (lp_rank >= 0.55 or tx_rank >= 0.55):
                implied_yield = min(implied_yield, 5.4)
            if speculative_heat >= 0.60 and lp_rank >= 0.60:
                implied_yield = min(implied_yield, 4.6)
            # 高価格帯そのものに上限を適用（都心判定を駅距離に依存しすぎない）
            if pressure >= 0.35 and (lp_rank >= 0.70 or tx_rank >= 0.70):
                implied_yield = min(implied_yield, 5.4)
            if lp_rank >= 0.85 or tx_rank >= 0.85:
                implied_yield = min(implied_yield, 5.0)
            if lp_rank >= 0.92 and tx_rank >= 0.80:
                implied_yield = min(implied_yield, 4.6)
            # 絶対単価ベースの都心上限
            if effective_lp and effective_lp >= lp_p70 and dkm <= 1.2:
                implied_yield = min(implied_yield, 5.8)
            if effective_lp and effective_lp >= lp_p90 and dkm <= 1.0:
                implied_yield = min(implied_yield, 5.0)
            if (pop_cr is not None and float(pop_cr) >= 0.0) and dkm <= 0.8:
                implied_yield = min(implied_yield, 5.2)
            implied_yield = max(1.6, min(14.0, implied_yield))

        if implied_yield:
            # メッシュ正味利回り: 共通経費 + 立地/人口リスク補正
            expense = (
                VACANCY_RATE + MANAGEMENT_FEE_RATE + REPAIR_RESERVE_RATE
                + INSURANCE_RATE + PROPERTY_TAX_RATE + CITY_PLANNING_TAX_RATE
            )
            if dkm > 0.8:
                expense += min(0.06, (dkm - 0.8) * 0.03)
            if pop_cr is not None and pop_cr < 0:
                expense += min(0.04, abs(float(pop_cr)) / 100 * 0.6)
            # 人気化・投機化エリアは運営難易度と取得競争を加味
            expense += min(0.03, 0.012 * pressure + 0.010 * speculative_heat)
            net_yield = round(implied_yield * max(0.55, (1 - expense)), 1)
            if pressure >= 0.80:
                net_yield = min(net_yield, 3.6)
            elif pressure >= 0.65:
                net_yield = min(net_yield, 4.2)
            if lp_rank >= 0.85 or tx_rank >= 0.85:
                net_yield = min(net_yield, 3.8)
            net_yield = max(0.8, min(12.0, net_yield))

        # 施設密度
        fac_total = (r.get("school_count") or 0) + (r.get("medical_count") or 0) + (r.get("childcare_count") or 0)

        # 表示値とカラー計算
        value = 0
        hard_fallback_used = False
        if metric == "land_price":
            value = effective_lp  # 地価なしなら取引価格でフォールバック
            if not value:
                if tx and tx > 0:
                    value = tx
                elif rent and rent > 0 and yref and yref > 0:
                    value = (rent * 12) / yref
                else:
                    value = global_lp_median
                hard_fallback_used = True
        elif metric == "rent":
            value = rent
            if not value:
                if effective_lp and effective_lp > 0 and yref and yref > 0:
                    value = effective_lp * yref / 12
                elif tx and tx > 0 and yref and yref > 0:
                    value = tx * yref / 12
                else:
                    value = global_rent_median
                hard_fallback_used = True
        elif metric == "tx_price":
            value = tx
            if not value:
                if effective_lp and effective_lp > 0:
                    value = effective_lp
                elif rent and rent > 0 and yref and yref > 0:
                    value = (rent * 12) / yref
                else:
                    value = global_tx_median
                hard_fallback_used = True
        elif metric == "population":
            value = pop_cr if pop_cr is not None else 0
        elif metric == "pop_density":
            # 人口密度: 250mメッシュ(0.0625km²)あたりの人口 → 人/km²
            value = round(pop * 16) if pop else 0
        elif metric == "yield":
            value = net_yield or implied_yield or 0
        elif metric == "facility":
            value = fac_total
        elif metric == "zoning":
            value = 1 if r.get("zoning") else 0

        # データなしメッシュはスキップ（人口系は0も有効）
        if value == 0 and metric not in ("population", "pop_density"):
            continue

        # 推定値フラグ (count == -1 は空間補間による推定)
        is_estimated = False
        if metric == "land_price":
            is_estimated = (r.get("land_price_count") or 0) < 0
        elif metric == "rent":
            is_estimated = (r.get("rent_count") or 0) < 0
        elif metric == "tx_price":
            is_estimated = (r.get("tx_count") or 0) < 0
        elif metric == "yield":
            is_estimated = ((r.get("land_price_count") or 0) < 0) or ((r.get("rent_count") or 0) < 0) or yield_estimated_fallback
        if hard_fallback_used:
            is_estimated = True

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["center_lng"], r["center_lat"]]},
            "properties": {
                "mesh_id": r["mesh_id"],
                "value": value,
                "estimated": is_estimated,
                "land_price": round(effective_lp) if effective_lp else None,
                "rent": round(rent) if rent else None,
                "tx_price": round(tx) if tx else None,
                "yield": net_yield if net_yield is not None else implied_yield,
                "gross_yield": implied_yield,
                "net_yield": net_yield,
                "pressure_index": round(pressure, 3),
                "speculative_heat": round(speculative_heat, 3),
                "pop": round(pop) if pop else None,
                "pop_density": round(pop * 16) if pop else None,
                "pop_change": pop_cr,
                "lp_count": r["land_price_count"],
                "rent_count": r["rent_count"],
                "tx_count": r["tx_count"],
                "schools": r["school_count"],
                "medical": r["medical_count"],
                "childcare": r.get("childcare_count", 0),
                "fac_total": fac_total,
                "station": r.get("nearest_station") or "",
                "station_km": r.get("station_dist_km"),
                "zoning": r.get("zoning") or "",
                "coverage": r.get("coverage_ratio") or "",
                "far": r.get("floor_area_ratio") or "",
                "front_road": r.get("front_road") or "",
                "road_width": r.get("road_width_m"),
            },
        })

    return JSONResponse(content={
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features), "metric": metric},
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
            name = dict(r).get("nearest_station")
            if name:
                rent_stations.add(name)

        # 地価データ有無
        lp_stations = set()
        for r in conn.execute("SELECT DISTINCT nearest_station FROM land_prices WHERE price_per_sqm > 0").fetchall():
            name = dict(r).get("nearest_station")
            if name:
                lp_stations.add(name)

        # 取引データ有無
        tx_stations = set()
        for r in conn.execute("SELECT DISTINCT nearest_station FROM transactions WHERE price_per_sqm > 0 AND nearest_station IS NOT NULL").fetchall():
            name = dict(r).get("nearest_station")
            if name:
                tx_stations.add(name)

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


def _build_lp_record(parsed: dict, year: int, pref_code: str = "", city_code: str = "") -> dict:
    """_parse_xpt002_point結果 → api_land_prices用レコード"""
    return {
        "point_id": parsed["point_id"],
        "place_name": parsed["place_name"],
        "price_per_sqm": parsed["price_num"],
        "year": year,
        "latitude": parsed["latitude"],
        "longitude": parsed["longitude"],
        "zoning": parsed["zoning"],
        "station": parsed["station"],
        "change_rate": parsed["change_rate"],
        "coverage": parsed["coverage"],
        "far": parsed["far"],
        "fire_prevention": parsed["fire_prevention"],
        "land_price_type": parsed.get("land_price_type", 0),
        "prefecture_code": pref_code,
        "city_code": city_code,
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


# ===== 事実ベースレイヤー（用途地域ポイント・路線別駅） =====

@app.get("/api/layers/zoning-points")
async def layer_zoning_points(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
):
    """用途地域を領域ポリゴンで返す（XKT002 GeoJSONベース）"""
    zoning_colors = {
        '第一種低層住居専用地域': '#81c784', '第二種低層住居専用地域': '#a5d6a7',
        '第一種中高層住居専用地域': '#4caf50', '第二種中高層住居専用地域': '#66bb6a',
        '第一種住居地域': '#26a69a', '第二種住居地域': '#4db6ac',
        '準住居地域': '#80cbc4', '田園住居地域': '#c8e6c9',
        '近隣商業地域': '#ffb74d', '商業地域': '#ff9800',
        '準工業地域': '#90a4ae', '工業地域': '#78909c', '工業専用地域': '#546e7a',
    }
    cache_key = (round(south, 3), round(west, 3), round(north, 3), round(east, 3))
    if not hasattr(layer_zoning_points, "_cache"):
        layer_zoning_points._cache = {}
    cache = layer_zoning_points._cache
    now_ts = datetime.now().timestamp()
    ttl_sec = 60 * 60 * 6
    if cache_key in cache and now_ts - cache[cache_key]["ts"] < ttl_sec:
        return JSONResponse(content=cache[cache_key]["data"])

    def _pick(props: dict, keys: list[str]) -> str:
        for k in keys:
            v = props.get(k)
            if v:
                return str(v)
        return ""

    def _norm_zoning(z: str) -> str:
        z = (z or "").strip()
        if not z:
            return ""
        # API表記ゆれを主要名称に寄せる
        if "第一種低層" in z:
            return "第一種低層住居専用地域"
        if "第二種低層" in z:
            return "第二種低層住居専用地域"
        if "第一種中高層" in z:
            return "第一種中高層住居専用地域"
        if "第二種中高層" in z:
            return "第二種中高層住居専用地域"
        if "第一種住居" in z:
            return "第一種住居地域"
        if "第二種住居" in z:
            return "第二種住居地域"
        if "準住居" in z:
            return "準住居地域"
        if "近隣商業" in z:
            return "近隣商業地域"
        if "商業" in z:
            return "商業地域"
        if "準工業" in z:
            return "準工業地域"
        if "工業専用" in z:
            return "工業専用地域"
        if "工業" in z:
            return "工業地域"
        return z

    from data.reinfolib_client import ReinfolibClient
    client = ReinfolibClient()
    features = []
    if client.is_configured():
        try:
            zoom = 15
            tiles = client._bounds_to_tiles(south, west, north, east, zoom)
            if len(tiles) > 80:
                tiles = tiles[:80]
            raw = client._multi_tile_request(
                "XKT002",
                tiles,
                extra_params={"z": str(zoom)},
                cache_hours=24 * 7,
            )
            for f in raw:
                geom = f.get("geometry") or {}
                gtype = geom.get("type")
                if gtype not in ("Polygon", "MultiPolygon"):
                    continue
                props = f.get("properties", {}) or {}
                zoning = _norm_zoning(_pick(props, [
                    "use_area_ja", "用途地域名", "land_use_ja", "用途地域",
                ]))
                if not zoning:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "zoning": zoning,
                        "color": zoning_colors.get(zoning, "#b0bec5"),
                        "coverage": _pick(props, ["u_building_coverage_ratio_ja", "building_coverage_ratio", "建蔽率"]),
                        "far": _pick(props, ["u_floor_area_ratio_ja", "floor_area_ratio", "容積率"]),
                        "fire_prevention": _pick(props, ["fire_prevention_ja", "防火地域"]),
                    },
                })
        except Exception as e:
            logging.warning(f"zoning polygon fetch failed: {e}")

    # API未設定/取得失敗時は既存DB点群から250m疑似ポリゴンを返す
    if not features:
        with db._conn() as conn:
            rows = conn.execute("""
                SELECT latitude, longitude, zoning, coverage, far,
                       place_name, price_per_sqm, station, fire_prevention
                FROM api_land_prices
                WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
                AND zoning IS NOT NULL AND zoning != ''
                LIMIT 5000
            """, (south, north, west, east)).fetchall()
        d_lat = 0.00208 / 2
        d_lng = 0.003125 / 2
        for r in [dict(x) for x in rows]:
            z = _norm_zoning(r.get("zoning", ""))
            if not z:
                continue
            lat = r["latitude"]
            lng = r["longitude"]
            poly = [
                [lng - d_lng, lat - d_lat],
                [lng + d_lng, lat - d_lat],
                [lng + d_lng, lat + d_lat],
                [lng - d_lng, lat + d_lat],
                [lng - d_lng, lat - d_lat],
            ]
            features.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [poly]},
                "properties": {
                    "zoning": z,
                    "color": zoning_colors.get(z, '#b0bec5'),
                    "coverage": r.get("coverage") or "",
                    "far": r.get("far") or "",
                    "place": r.get("place_name") or "",
                    "price": r.get("price_per_sqm") or 0,
                    "station": r.get("station") or "",
                    "fire_prevention": r.get("fire_prevention") or "",
                },
            })

    data = {
        "type": "FeatureCollection", "features": features,
        "_meta": {"count": len(features), "mode": "polygon"},
    }
    cache[cache_key] = {"ts": now_ts, "data": data}
    return JSONResponse(content=data)


@app.get("/api/layers/railway-lines")
async def layer_railway_lines(
    south: float = 35.5, west: float = 139.3,
    north: float = 36.0, east: float = 140.2,
):
    """路線別に色分けした駅ポイントを返す（公式ラインカラー準拠）"""
    features = []

    with db._conn() as conn:
        rows = conn.execute("""
            SELECT station_name, line_name, latitude, longitude,
                   prefecture_code, city_code
            FROM stations
            WHERE latitude BETWEEN ? AND ? AND longitude BETWEEN ? AND ?
            AND latitude IS NOT NULL
        """, (south, north, west, east)).fetchall()

    # 公式ラインカラー（各社HP・路線図準拠）
    # https://www.jreast.co.jp/railway/ / https://www.tokyometro.jp/ 等
    LINE_COLORS = {
        # JR東日本
        'JR山手線': '#9acd32', 'JR中央線': '#f15a22', 'JR京浜東北線': '#00b2e5',
        'JR総武線': '#ffd400', 'JR埼京線': '#00ac9b', 'JR横浜線': '#7fc97f',
        'JR南武線': '#ffd400', 'JR武蔵野線': '#f15a22', 'JR常磐線': '#00b261',
        'JR東海道線': '#f68b1e', 'JR東海道本線': '#f68b1e',
        'JR宇都宮線': '#f68b1e', 'JR高崎線': '#f68b1e',
        'JR京葉線': '#c9252f', 'JR横須賀線': '#0067c0',
        'JR根岸線': '#00b2e5', 'JR川越線': '#00ac9b',
        'JR総武本線': '#ffd400', 'JR成田線': '#ffd400',
        # 東京メトロ (公式CI)
        '東京メトロ銀座線': '#f39700', '東京メトロ丸ノ内線': '#e60012',
        '東京メトロ日比谷線': '#9caeb7', '東京メトロ東西線': '#009bbf',
        '東京メトロ千代田線': '#00a650', '東京メトロ有楽町線': '#c1a470',
        '東京メトロ半蔵門線': '#8f76d6', '東京メトロ南北線': '#00ada9',
        '東京メトロ副都心線': '#9c7e31',
        # 都営地下鉄
        '都営浅草線': '#e85298', '都営三田線': '#006ab6',
        '都営新宿線': '#b6007a', '都営大江戸線': '#ce045c',
        # 東急 (公式)
        '東急東横線': '#da0442', '東急田園都市線': '#00a23a',
        '東急目黒線': '#009cd2', '東急大井町線': '#f18c43',
        '東急池上線': '#ee86a7', '東急多摩川線': '#ae0378',
        # 小田急
        '小田急線': '#003f8e', '小田急小田原線': '#003f8e',
        '小田急江ノ島線': '#003f8e', '小田急多摩線': '#003f8e',
        # 京王
        '京王線': '#c9167e', '京王井の頭線': '#9b7cb6',
        '京王相模原線': '#c9167e',
        # 西武
        '西武池袋線': '#003e9e', '西武新宿線': '#003e9e',
        # 東武
        '東武東上線': '#004098', '東武スカイツリーライン': '#004098',
        '東武アーバンパークライン': '#004098',
        # 京急
        '京急線': '#e5171f', '京急本線': '#e5171f', '京急空港線': '#e5171f',
        # 京成
        '京成本線': '#003f8e', '京成押上線': '#003f8e',
        # 相鉄
        '相鉄本線': '#2e59a7', '相鉄いずみ野線': '#2e59a7',
        # その他
        'つくばエクスプレス': '#243f97',
        '横浜市営地下鉄': '#0068b7', '横浜市営地下鉄ブルーライン': '#0068b7',
        'りんかい線': '#00a4db', 'ゆりかもめ': '#b5e61d',
        '多摩モノレール': '#ff6f61', '多摩都市モノレール': '#ff6f61',
        '湘南モノレール': '#00b0f0', '千葉都市モノレール': '#e60012',
        '北総鉄道': '#c1328e', '北総線': '#c1328e',
        '東葉高速鉄道': '#51a6db', '埼玉高速鉄道': '#ff6f61',
        '埼玉新都市交通': '#008cce', '江ノ島電鉄': '#006f3c',
        '箱根登山鉄道': '#e60012', '小湊鉄道': '#e60012',
        '東京モノレール': '#00a0de',
        '横浜高速鉄道': '#da0442', 'みなとみらい線': '#da0442',
        '東京臨海高速鉄道': '#00a4db',
    }

    # 事業者名→代表色マッピング（拡張駅用: line_nameが事業者名の場合）
    OPERATOR_COLORS = {
        '東日本旅客鉄道': '#00b261', 'JR東日本': '#00b261', 'East Japan Railway': '#00b261',
        '東日本旅客鉄道株式会社': '#00b261', 'JR貨物': '#808080',
        '東京地下鉄': '#009bbf', '東京メトロ': '#009bbf',
        '東京都交通局': '#006ab6',
        '東急電鉄': '#da0442', '東京急行電鉄': '#da0442', '東急': '#da0442',
        '小田急電鉄': '#003f8e', '京王電鉄': '#c9167e',
        '西武鉄道': '#003e9e', '西武鉄道株式会社': '#003e9e',
        '東武鉄道': '#004098',
        '京成電鉄': '#003f8e', '京浜急行電鉄': '#e5171f',
        '相模鉄道': '#2e59a7', '横浜市交通局': '#0068b7',
        '多摩都市モノレール': '#ff6f61', 'ゆりかもめ': '#b5e61d',
        '首都圏新都市鉄道': '#243f97',
        '北総鉄道': '#c1328e', '東葉高速鉄道': '#51a6db',
        '埼玉高速鉄道': '#ff6f61', '埼玉新都市交通': '#008cce',
        '千葉都市モノレール': '#e60012', '小湊鉄道': '#e60012',
        '江ノ島電鉄': '#006f3c', '箱根登山鉄道': '#e60012',
        '東京モノレール': '#00a0de', '東京モノレール株式会社': '#00a0de',
        '湘南モノレール': '#00b0f0', '横浜高速鉄道': '#da0442',
        '横浜シーサイドライン': '#00b5f0', '東京臨海高速鉄道': '#00a4db',
        '流鉄': '#f9a11b', '秩父鉄道': '#005bac', '山万': '#009944',
        '舞浜リゾートライン': '#ff69b4', '芝山鉄道': '#003f8e',
        '伊豆箱根鉄道': '#0075c2', '銚子電気鉄道': '#e5171f',
        '神奈川臨海鉄道': '#808080', '小田急箱根': '#003f8e',
    }

    # 表示名の名寄せ（同一事業者の表記ゆれ統一）
    DISPLAY_NAME_MAP = {
        '東京地下鉄': '東京メトロ',
        '東京急行電鉄': '東急電鉄', '東急': '東急電鉄',
        '東京急行電鉄;東京地下鉄': '東急電鉄/メトロ',
        '東日本旅客鉄道株式会社': 'JR東日本', 'East Japan Railway': 'JR東日本',
        'JR東日本': 'JR東日本', 'JR  東日本旅客鉄道': 'JR東日本',
        '東日本旅客鉄道': 'JR東日本',
        '東日本旅客鉄道;東京臨海高速鉄道': 'JR/りんかい線',
        '東日本旅客鉄道;東京地下鉄': 'JR/メトロ',
        '西武鉄道株式会社': '西武鉄道',
        '西武鉄道;東京地下鉄': '西武/メトロ',
        '小田急電鉄;東京地下鉄': '小田急/メトロ',
        '東武鉄道;東日本旅客鉄道': '東武/JR',
        '京成電鉄;東京都交通局': '京成/都営',
        '京成電鉄;北総鉄道': '京成/北総',
        '相模鉄道;東日本旅客鉄道': '相鉄/JR',
        '東京地下鉄;東京都交通局': 'メトロ/都営',
        '小湊鉄道;いすみ鉄道': '小湊/いすみ',
        '京浜急行電鉄 (Keikyu Corporation)': '京急電鉄',
        '横浜市交通局 横浜市営地下鉄グリーンライン': '横浜市営グリーンライン',
        '首都圏新都市鉄道': 'つくばエクスプレス',
        '東京臨海高速鉄道': 'りんかい線',
        'モノレール浜松町': '東京モノレール',
        '東京モノレール株式会社': '東京モノレール',
        '横浜高速鉄道': 'みなとみらい線',
        '横浜シーサイドライン': 'シーサイドライン',
        '多摩都市モノレール': '多摩モノレール',
        '千葉都市モノレール': '千葉モノレール',
        '埼玉新都市交通': 'ニューシャトル',
    }

    def _resolve_color(line_name):
        if not line_name:
            return '#90a4ae'
        if line_name in LINE_COLORS:
            return LINE_COLORS[line_name]
        base = line_name.split(';')[0].strip()
        if base in OPERATOR_COLORS:
            return OPERATOR_COLORS[base]
        for key, color in LINE_COLORS.items():
            if key in line_name or line_name in key:
                return color
        for key, color in OPERATOR_COLORS.items():
            if key in line_name or line_name in key:
                return color
        return '#90a4ae'

    def _display_name(line_name):
        if not line_name:
            return '不明'
        if line_name in DISPLAY_NAME_MAP:
            return DISPLAY_NAME_MAP[line_name]
        base = line_name.split(';')[0].strip()
        if base in DISPLAY_NAME_MAP:
            return DISPLAY_NAME_MAP[base]
        return line_name

    def _split_lines(raw_line: str) -> list[str]:
        if not raw_line:
            return []
        parts = re.split(r"[;／/,|]+", raw_line)
        return [p.strip() for p in parts if p and p.strip()]

    line_color_map = {}
    # 実線路レイヤー: OSM Overpass から取得（駅間擬似接続は廃止）
    if not hasattr(layer_railway_lines, "_cache"):
        layer_railway_lines._cache = {}
    cache = layer_railway_lines._cache
    import time
    cache_key = (round(south, 3), round(west, 3), round(north, 3), round(east, 3))
    now_ts = time.time()
    if cache_key in cache and now_ts - cache[cache_key]["ts"] < 3600:
        return JSONResponse(content=cache[cache_key]["data"])

    lat_range = north - south
    lng_range = east - west
    track_enabled = True
    # 広域表示ではOverpass負荷が高いため、駅表示のみ（実線路は近接ズーム時）
    if lat_range > 0.12 or lng_range > 0.12:
        track_enabled = False
    elif lat_range > 0.06 or lng_range > 0.06:
        clat = (south + north) / 2
        clng = (west + east) / 2
        south, north = clat - 0.03, clat + 0.03
        west, east = clng - 0.03, clng + 0.03

    import requests
    track_features = []
    query = (
        f"[out:json][timeout:25];"
        f"way[railway~\"rail|subway|light_rail|monorail|tram\"]({south},{west},{north},{east});"
        f"out tags geom;"
    )
    OVERPASS_MIRRORS = [
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass-api.de/api/interpreter",
    ]
    if track_enabled:
        for ep in OVERPASS_MIRRORS:
            try:
                resp = requests.post(ep, data={"data": query}, timeout=3)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                elements = data.get("elements", [])
                for e in elements:
                    geom = e.get("geometry") or []
                    if len(geom) < 2:
                        continue
                    tags = e.get("tags", {}) or {}
                    raw_name = (
                        tags.get("name:ja")
                        or tags.get("line")
                        or tags.get("ref")
                        or tags.get("name")
                        or tags.get("operator")
                        or tags.get("network")
                        or "不明"
                    )
                    line_name = _display_name(str(raw_name))
                    color = _resolve_color(str(raw_name))
                    line_color_map.setdefault(line_name, color)
                    track_features.append({
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[p["lon"], p["lat"]] for p in geom if "lat" in p and "lon" in p],
                        },
                        "properties": {
                            "feature_kind": "track",
                            "line": line_name,
                            "color": color,
                            "railway": tags.get("railway", ""),
                            "operator": tags.get("operator", ""),
                            "ref": tags.get("ref", ""),
                        },
                    })
                if track_features:
                    break
            except Exception:
                continue

    # 乗換ハブ作成（同一駅に複数路線）
    hub_map = {}
    for n in [dict(x) for x in rows]:
        name = n.get("station_name", "")
        lat = n.get("latitude")
        lng = n.get("longitude")
        if not name or lat is None or lng is None:
            continue
        hub_key = f"{name}|{round(lat, 5)}|{round(lng, 5)}"
        if hub_key not in hub_map:
            hub_map[hub_key] = {
                "name": name,
                "lat": lat,
                "lng": lng,
                "lines": set(),
            }
        split = _split_lines(n.get("line_name", "") or "")
        if split:
            for one in split:
                hub_map[hub_key]["lines"].add(_display_name(one))
        elif n.get("line_name"):
            hub_map[hub_key]["lines"].add(_display_name(n.get("line_name")))

    station_features = []
    for h in hub_map.values():
        lines = sorted(h["lines"])
        primary = lines[0] if lines else "不明"
        color = _resolve_color(primary)
        line_color_map.setdefault(primary, color)
        station_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [h["lng"], h["lat"]]},
            "properties": {
                "name": h["name"],
                "line": primary,
                "lines": lines,
                "color": color,
                "feature_kind": "station",
                "is_transfer": len(lines) >= 2,
                "transfer_count": len(lines),
                "transfer_lines": lines[:12],
            },
        })

    features = track_features + station_features
    data = {
        "type": "FeatureCollection", "features": features,
        "_meta": {
            "count": len(features),
            "stations": len(station_features),
            "segments": len(track_features),  # 互換維持: frontendはsegmentsとして扱う
            "tracks": len(track_features),
            "track_enabled": track_enabled,
            "transfer_stations": sum(1 for f in station_features if f["properties"].get("is_transfer")),
            "lines": line_color_map,
        },
    }
    cache[cache_key] = {"ts": now_ts, "data": data}
    return JSONResponse(content=data)


# ===== 道路種別レイヤー（Overpass API） =====

_road_cache = {}  # (south,west,north,east) rounded -> {features, ts}
_ROAD_CACHE_TTL = 3600  # 1時間キャッシュ

@app.get("/api/layers/roads")
async def layer_roads(
    south: float = 35.6, west: float = 139.7,
    north: float = 35.8, east: float = 139.9,
):
    """道路種別を色分けしたGeoJSONを返す（Overpass API経由、キャッシュ付き）"""
    import time
    import requests as req

    # bounds を 0.01度単位に丸めてキャッシュキー
    cache_key = (round(south, 2), round(west, 2), round(north, 2), round(east, 2))
    now = time.time()
    if cache_key in _road_cache and now - _road_cache[cache_key]["ts"] < _ROAD_CACHE_TTL:
        return JSONResponse(content=_road_cache[cache_key]["data"])

    # 表示範囲制限（広すぎるとOverpass APIが重い）
    lat_range = north - south
    lng_range = east - west
    if lat_range > 0.05 or lng_range > 0.05:
        # 広域時は中心付近に絞る
        clat = (south + north) / 2
        clng = (west + east) / 2
        south, north = clat - 0.025, clat + 0.025
        west, east = clng - 0.025, clng + 0.025

    # Overpass API クエリ: highway タグ付きの全道路を取得
    query = f"[out:json][timeout:15];way[highway]({south},{west},{north},{east});out geom;"
    VALID_HIGHWAYS = {
        "motorway", "motorway_link", "trunk", "trunk_link",
        "primary", "primary_link", "secondary", "secondary_link",
        "tertiary", "tertiary_link", "residential", "unclassified",
        "living_street", "service",
    }

    def _classify_road(highway, tags):
        """OSMデータから建築基準法上の道路種別を推定"""
        width_str = tags.get("width", "")
        try:
            width = float(width_str.replace("m", "").strip()) if width_str else None
        except (ValueError, TypeError):
            width = None

        lanes = tags.get("lanes", "")
        access_val = tags.get("access", "")
        surface = tags.get("surface", "")

        # 高速道路: 建築基準法の対象外
        if highway in ("motorway", "motorway_link"):
            return {"legal": "対象外(自動車専用)", "name": "高速道路", "color": "#00897b", "weight": 5}

        # 国道・主要地方道 → 42条1項1号（幅員4m以上確実）
        if highway in ("trunk", "trunk_link", "primary", "primary_link"):
            return {"legal": "42条1項1号", "name": "国道/主要地方道(公道)", "color": "#e53935", "weight": 4}

        # 都道府県道 → 基本的に42条1項1号
        if highway in ("secondary", "secondary_link"):
            return {"legal": "42条1項1号", "name": "都道府県道(公道)", "color": "#fb8c00", "weight": 3}

        # 市区町村道(主要) → 幅員による判定
        if highway in ("tertiary", "tertiary_link"):
            if width and width < 4.0:
                return {"legal": "42条2項(要セットバック)", "name": "市道(4m未満)", "color": "#ff6f00", "weight": 2.5}
            return {"legal": "42条1項1号", "name": "市区町村道(公道)", "color": "#fdd835", "weight": 2.5}

        # 住宅地道路 → 幅員で1項1号 vs 2項道路を判定
        if highway in ("residential", "unclassified"):
            if width and width < 4.0:
                return {"legal": "42条2項(要セットバック)", "name": "生活道路(4m未満)", "color": "#ff6f00", "weight": 1.8}
            if width and width >= 4.0:
                return {"legal": "42条1項1号", "name": "生活道路(4m以上)", "color": "#b0bec5", "weight": 1.8}
            # 幅員不明 → 車線数で推定
            if lanes and int(lanes) >= 2:
                return {"legal": "42条1項1号(推定)", "name": "生活道路", "color": "#b0bec5", "weight": 1.8}
            return {"legal": "幅員不明(要現地確認)", "name": "生活道路", "color": "#9e9e9e", "weight": 1.5}

        # 生活道路
        if highway == "living_street":
            return {"legal": "42条2項(推定)", "name": "細街路(要セットバック)", "color": "#ff6f00", "weight": 1.2}

        # 私道・通路
        if highway == "service":
            if access_val in ("private", "no"):
                return {"legal": "43条但書/非道路", "name": "私道(建築不可の可能性)", "color": "#d32f2f", "weight": 1}
            return {"legal": "42条1項5号(推定)", "name": "私道/位置指定道路", "color": "#7e57c2", "weight": 1.2}

        return {"legal": "不明", "name": highway, "color": "#78909c", "weight": 1}

    OVERPASS_MIRRORS = [
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass-api.de/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]

    features = []
    try:
        resp = None
        for mirror in OVERPASS_MIRRORS:
            try:
                resp = req.post(mirror, data={"data": query}, timeout=15)
                if resp.status_code == 200:
                    break
            except Exception:
                continue
        if resp and resp.status_code == 200:
            data = resp.json()
            for elem in data.get("elements", []):
                if elem.get("type") != "way":
                    continue
                geom = elem.get("geometry", [])
                if len(geom) < 2:
                    continue

                tags = elem.get("tags", {})
                hw = tags.get("highway", "")
                if hw not in VALID_HIGHWAYS:
                    continue
                road_info = _classify_road(hw, tags)

                coords = [[p["lon"], p["lat"]] for p in geom]
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {
                        "road_type": road_info["name"],
                        "legal": road_info["legal"],
                        "color": road_info["color"],
                        "weight": road_info["weight"],
                        "name": tags.get("name", ""),
                        "ref": tags.get("ref", ""),
                        "width": tags.get("width", ""),
                        "lanes": tags.get("lanes", ""),
                        "surface": tags.get("surface", ""),
                        "highway": hw,
                    },
                })
    except Exception as e:
        logging.warning(f"Overpass API error: {e}")

    result = {
        "type": "FeatureCollection",
        "features": features,
        "_meta": {"count": len(features)},
    }
    _road_cache[cache_key] = {"data": result, "ts": now}
    return JSONResponse(content=result)


# ===== メッシュベース一括バッチ収集 =====

@app.post("/api/mesh/batch-collect")
async def mesh_batch_collect(
    target: str = "all",
    max_meshes: int = 500,
    prefectures: str = "13,14",
):
    """メッシュベースでデータを一括収集 → 欠損メッシュ優先

    target: rental / land_price / transactions / all
    欠損メッシュを優先的に収集し、空間補間→再集計まで自動実行。
    """
    import threading
    pref_list = [p.strip() for p in prefectures.split(",")]

    def _run():
        import math
        logging.info(f"=== メッシュバッチ収集開始: target={target}, max={max_meshes} ===")

        from data.reinfolib_client import ReinfolibClient
        api_client = ReinfolibClient()

        # 1) 欠損メッシュ抽出: 駅2km圏内で各メトリクスが未取得のメッシュ
        with db._conn() as conn:
            stations_raw = conn.execute(
                "SELECT station_name, latitude, longitude, prefecture_code, city_code "
                "FROM stations WHERE latitude IS NOT NULL AND prefecture_code IN ({})".format(
                    ",".join(f"'{p}'" for p in pref_list)
                )
            ).fetchall()
            stations_raw = [dict(x) for x in stations_raw]

            existing_meshes = {}
            for r in conn.execute("SELECT mesh_id, land_price_count, rent_count, tx_count FROM mesh_250m").fetchall():
                d = dict(r)
                existing_meshes[d["mesh_id"]] = d

        # 欠損メッシュの駅をリストアップ (メトリクス別)
        gap_stations = {"rental": [], "land_price": [], "transactions": []}
        for s in stations_raw:
            mid = _latlng_to_mesh250(s["latitude"], s["longitude"])
            em = existing_meshes.get(mid, {})
            lp_cnt = em.get("land_price_count", 0) or 0
            rent_cnt = em.get("rent_count", 0) or 0
            tx_cnt = em.get("tx_count", 0) or 0
            if rent_cnt <= 0:
                gap_stations["rental"].append(s)
            if lp_cnt <= 0:
                gap_stations["land_price"].append(s)
            if tx_cnt <= 0:
                gap_stations["transactions"].append(s)

        targets = [target] if target != "all" else ["rental", "land_price", "transactions"]
        total_collected = {}

        for t in targets:
            collected = 0
            stations_to_fill = gap_stations.get(t, [])[:max_meshes]
            logging.info(f"  {t}: {len(stations_to_fill)}駅の欠損補完開始")

            if t == "rental":
                for s in stations_to_fill:
                    try:
                        results = scraper_agent.scrape_rentals(
                            prefecture_code=s.get("prefecture_code", "13"),
                            max_pages=2,
                        )
                        if results:
                            saved = db.upsert_rental_comps(results)
                            collected += saved
                    except Exception as e:
                        logging.debug(f"  賃料収集エラー {s['station_name']}: {e}")

            elif t == "land_price" and api_client.is_configured():
                for s in stations_to_fill:
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
                                "prefecture_code": s.get("prefecture_code", ""),
                                "city_code": s.get("city_code", ""),
                            })
                        if records:
                            saved = db.upsert_api_land_prices(records)
                            collected += saved
                    except Exception as e:
                        logging.debug(f"  地価収集エラー {s['station_name']}: {e}")

            elif t == "transactions" and api_client.is_configured():
                current_year = datetime.now().year
                for s in stations_to_fill:
                    try:
                        city = s.get("city_code", "")
                        if not city:
                            continue
                        raw = api_client.get_transactions(
                            current_year - 1, 1,
                            area=s.get("prefecture_code", "13"), city=city,
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
                                    "prefecture_code": s.get("prefecture_code", ""),
                                    "city_code": city,
                                })
                            except Exception:
                                continue
                        if records:
                            saved = db.upsert_transactions(records)
                            collected += saved
                    except Exception as e:
                        logging.debug(f"  取引収集エラー {s['station_name']}: {e}")

            total_collected[t] = collected
            logging.info(f"  {t}収集完了: {collected}件")

        # 収集後に自動で再集計+空間補間
        logging.info("=== 収集後の再集計+空間補間を実行 ===")
        import asyncio
        # compute_mesh_250mの_run相当をインライン呼び出し
        # (実際にはPOST /api/mesh/compute のバックグラウンド処理と同じ)
        # ここでは同期的に実行
        import requests
        try:
            requests.post(f"http://127.0.0.1:{WEB_PORT}/api/mesh/compute", timeout=5)
        except Exception:
            pass

        logging.info(f"=== メッシュバッチ収集完了: {total_collected} ===")

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse(content={
        "status": "started",
        "target": target,
        "prefectures": pref_list,
        "max_meshes": max_meshes,
        "message": f"メッシュバッチ収集開始: {target}",
    })


@app.get("/api/mesh/coverage")
async def mesh_coverage():
    """メッシュデータのカバレッジ統計（欠損状況の可視化用）"""
    with db._conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM mesh_250m").fetchone()[0]
        has_lp = conn.execute("SELECT COUNT(*) FROM mesh_250m WHERE avg_land_price_sqm IS NOT NULL AND land_price_count > 0").fetchone()[0]
        has_rent = conn.execute("SELECT COUNT(*) FROM mesh_250m WHERE avg_rent_sqm IS NOT NULL AND rent_count > 0").fetchone()[0]
        has_tx = conn.execute("SELECT COUNT(*) FROM mesh_250m WHERE avg_tx_price_sqm IS NOT NULL AND tx_count > 0").fetchone()[0]
        est_lp = conn.execute("SELECT COUNT(*) FROM mesh_250m WHERE land_price_count = -1").fetchone()[0]
        est_rent = conn.execute("SELECT COUNT(*) FROM mesh_250m WHERE rent_count = -1").fetchone()[0]
        est_tx = conn.execute("SELECT COUNT(*) FROM mesh_250m WHERE tx_count = -1").fetchone()[0]
        has_pop = conn.execute("SELECT COUNT(*) FROM mesh_250m WHERE pop_current IS NOT NULL").fetchone()[0]
        has_zoning = conn.execute("SELECT COUNT(*) FROM mesh_250m WHERE zoning IS NOT NULL AND zoning != ''").fetchone()[0]

    fill_land = round((has_lp + est_lp) / max(total, 1) * 100, 1)
    fill_rent = round((has_rent + est_rent) / max(total, 1) * 100, 1)
    fill_tx = round((has_tx + est_tx) / max(total, 1) * 100, 1)

    return JSONResponse(content={
        "total_meshes": total,
        "coverage": {
            "land_price": {"observed": has_lp, "estimated": est_lp, "total": has_lp + est_lp},
            "rent": {"observed": has_rent, "estimated": est_rent, "total": has_rent + est_rent},
            "transactions": {"observed": has_tx, "estimated": est_tx, "total": has_tx + est_tx},
            "population": has_pop,
            "zoning": has_zoning,
        },
        "fill_rate": {
            "land_price": fill_land,
            "rent": fill_rent,
            "transactions": fill_tx,
        },
        "target_fill_rate": {
            "land_price": 100.0,
            "rent": 100.0,
            "transactions": 100.0,
        },
        "remaining_to_target": {
            "land_price": max(0, total - (has_lp + est_lp)),
            "rent": max(0, total - (has_rent + est_rent)),
            "transactions": max(0, total - (has_tx + est_tx)),
        },
    })


# ===== データ成長パイプライン =====

@app.post("/api/pipeline/grow")
async def pipeline_grow(
    prefectures: str = "13,14,11,12",
    max_rental_pages: int = 5,
    geocode_batch: int = 200,
):
    """ヒートマップ充填パイプライン: 賃料収集→ジオコード→再集計+補間"""
    import threading
    pref_list = [p.strip() for p in prefectures.split(",")]

    def _run():
        from engine.batch_processor import MeshGrowthPipeline
        pipeline = MeshGrowthPipeline()
        pipeline.run(
            prefectures=pref_list,
            max_rental_pages=max_rental_pages,
            geocode_batch=geocode_batch,
        )

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse(content={
        "status": "started",
        "message": f"データ成長パイプライン開始: {pref_list}",
        "prefectures": pref_list,
        "max_rental_pages": max_rental_pages,
    })


# スケジューラ自動起動（既定ON: ボタンなしで収益物件を定期取得してDB保存）
from engine.scheduler import scheduler as _scheduler
_scheduler.set_pipeline(ingest_pipeline)
if os.getenv("RE_SCHEDULER_AUTOSTART", "1").strip().lower() in {"1", "true", "yes", "on"}:
    _scheduler.start()
    logging.info("Scheduler autostart enabled (set RE_SCHEDULER_AUTOSTART=0 to disable)")
else:
    logging.info("Scheduler autostart disabled (set RE_SCHEDULER_AUTOSTART=1 to enable)")

if __name__ == "__main__":
    import uvicorn
    land_routes = [r for r in app.routes if hasattr(r, 'path') and 'land' in r.path]
    logging.info(f"土地API: {len(land_routes)}エンドポイント登録済")
    stats = db.get_db_stats()
    logging.info(f"DB: land_listings={stats.get('land_listings',0)}, building_plans={stats.get('building_plans',0)}")
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)
