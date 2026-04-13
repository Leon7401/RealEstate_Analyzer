"""
バッチ処理エンジン - データの自動収集・更新・集計

バッチ種別:
  land_prices       : 公示地価データ取得（APIから）
  transactions      : 取引価格データ取得（APIから）
  rental_comps      : 賃料データ取得（CSV / スクレイピング）
  station_metrics   : 駅メトリクス再計算
  land_listings     : 土地物件スクレイピング（SUUMO/楽待）
  building_plans    : 建築プラン一括生成
  full_update       : 上記全てを順次実行
"""
import logging
import csv
import re
import statistics
from datetime import datetime
from typing import List, Optional

from storage.database import Database
from data.reinfolib_client import ReinfolibClient
from data.city_master import CITY_MASTER, CITY_NAME_MAP
from data.station_master import (
    STATIONS, STATION_MAP, STATION_NAME_MAP,
    get_stations_by_prefecture, resolve_station_id,
    get_reference_land_price, get_reference_rent,
    REFERENCE_LAND_PRICES_STATION, REFERENCE_RENT_STATION,
)
from config.settings import (
    DEFAULT_CITY_CODES,
    BATCH_TARGET_PREFECTURES,
    TRANSACTION_YEARS_BACK,
)

logger = logging.getLogger("BatchProcessor")


class BatchProcessor:
    """データの一括取得・更新・集計を行うバッチ処理エンジン"""

    def __init__(self):
        self.db = Database()
        self.api = ReinfolibClient()

    # ===== フルバッチ =====

    def run_full_update(self, prefectures: List[str] = None):
        """全データの一括更新（駅単位）"""
        prefectures = prefectures or BATCH_TARGET_PREFECTURES
        logger.info(f"=== フルバッチ更新開始: {prefectures} ===")

        # 1. 駅マスタ投入
        self._ensure_station_master()

        for pref in prefectures:
            cities = CITY_MASTER.get(pref, [])
            city_codes = [c["code"] for c in cities if c["code"]]

            # 2. 地価データ
            if self.api.is_configured():
                self.batch_land_prices(pref, city_codes)
                self.batch_transactions(pref, city_codes)
            else:
                logger.warning("APIキー未設定: 参考データ投入モード")
                self._seed_reference_data(pref)

            # 3. 賃料データ（CSVから）
            self.batch_rental_from_csv()

            # 4. station_id割り当て
            self._assign_station_ids()

            # 5. 駅メトリクス再計算
            self.compute_station_metrics(pref)

        logger.info("=== フルバッチ更新完了 ===")

    def _ensure_station_master(self):
        """駅マスタをDBに投入"""
        stats = self.db.get_db_stats()
        if stats.get("stations", 0) < len(STATIONS):
            count = self.db.populate_stations(STATIONS)
            logger.info(f"駅マスタ投入: {count}駅")

    def _assign_station_ids(self):
        """未割り当てレコードにstation_idを付与"""
        updated = self.db.assign_station_ids(resolve_station_id)
        if updated > 0:
            logger.info(f"station_id割り当て: {updated}件")

    # ===== 公示地価バッチ =====

    def batch_land_prices(
        self, pref: str, city_codes: List[str] = None
    ):
        """公示地価/取引データからの地価バッチ取得

        XIT001で土地取引のみを取得し、㎡単価を公示地価として集計。
        XPT001（タイル座標ベース）も主要市区町村の中心座標で取得。
        """
        if not self.api.is_configured():
            logger.warning("APIキー未設定: 地価バッチスキップ")
            return

        city_codes = city_codes or DEFAULT_CITY_CODES
        batch_id = self.db.start_batch("land_prices", pref)
        total_fetched = 0
        total_inserted = 0

        current_year = datetime.now().year

        try:
            for city in city_codes:
                logger.info(f"  地価取得: {CITY_NAME_MAP.get(city, city)}")

                # XIT001で直近の土地取引を取得
                raw = []
                for q in range(1, 5):
                    try:
                        items = self.api.get_transactions(
                            year=current_year - 1, quarter=q,
                            area=pref, city=city,
                        )
                        # 土地取引のみフィルタ
                        land_items = [
                            i for i in items
                            if i.get("Type", "") in ("宅地(土地)", "宅地(土地と建物)", "林地")
                            or "土地" in i.get("Type", "")
                        ]
                        raw.extend(land_items)
                    except Exception:
                        continue

                total_fetched += len(raw)

                records = []
                for item in raw:
                    try:
                        price = int(item.get("TradePrice", 0))
                        area = _safe_float(item.get("Area"))
                        if not area or area <= 0 or price <= 0:
                            continue

                        nearest = item.get("NearestStation", "")
                        sid = resolve_station_id(nearest, None, None, pref)

                        records.append({
                            "address": (
                                item.get("Municipality", "")
                                + item.get("DistrictName", "")
                            ),
                            "price_per_sqm": int(price / area),
                            "year": current_year - 1,
                            "latitude": None,
                            "longitude": None,
                            "land_use_zone": item.get("CityPlanning", ""),
                            "acreage": area,
                            "nearest_station": nearest,
                            "station_distance_min": _safe_int(
                                item.get("TimeToNearestStation")
                            ),
                            "station_id": sid,
                            "price_change_rate": None,
                            "price_type": "取引実績",
                            "prefecture_code": pref,
                            "city_code": city,
                        })
                    except (ValueError, TypeError):
                        continue

                inserted = self.db.upsert_land_prices(records)
                total_inserted += inserted

            self.db.finish_batch(batch_id, "completed", total_fetched, total_inserted)
            logger.info(f"  地価バッチ完了: {total_fetched}件取得, {total_inserted}件保存")

        except Exception as e:
            self.db.finish_batch(batch_id, "error", total_fetched, total_inserted, str(e))
            logger.error(f"  地価バッチエラー: {e}")

    # ===== 取引データバッチ =====

    def batch_transactions(
        self, pref: str, city_codes: List[str] = None
    ):
        """取引価格データのバッチ取得（XIT001: year+quarter+city）"""
        if not self.api.is_configured():
            return

        city_codes = city_codes or DEFAULT_CITY_CODES
        batch_id = self.db.start_batch("transactions", pref)
        total_fetched = 0
        total_inserted = 0

        try:
            for city in city_codes:
                logger.info(f"  取引取得: {CITY_NAME_MAP.get(city, city)}")
                raw = self.api.get_transactions_multi(area=pref, city=city, years_back=TRANSACTION_YEARS_BACK)
                total_fetched += len(raw)

                records = []
                for item in raw:
                    try:
                        price = int(item.get("TradePrice", 0))
                        area = _safe_float(item.get("Area"))
                        nearest = item.get("NearestStation", "")
                        sid = resolve_station_id(nearest, None, None, pref)

                        records.append({
                            "address": (
                                item.get("Municipality", "")
                                + item.get("DistrictName", "")
                            ),
                            "transaction_price": price,
                            "price_per_sqm": price / area if area and area > 0 else None,
                            "transaction_date": item.get("Period", ""),
                            "land_area": area,
                            "building_area": _safe_float(item.get("FloorArea")),
                            "land_shape": item.get("LandShape", ""),
                            "land_use_zone": item.get("CityPlanning", ""),
                            "structure": item.get("Structure", ""),
                            "built_year": _parse_built_year(item.get("BuildingYear", "")),
                            "use": item.get("Use", ""),
                            "property_type": item.get("Type", "土地"),
                            "nearest_station": nearest,
                            "station_distance_min": _safe_int(
                                item.get("TimeToNearestStation")
                            ),
                            "station_id": sid,
                            "prefecture_code": pref,
                            "city_code": city,
                        })
                    except (ValueError, TypeError):
                        continue

                inserted = self.db.upsert_transactions(records)
                total_inserted += inserted

            self.db.finish_batch(batch_id, "completed", total_fetched, total_inserted)
            logger.info(f"  取引バッチ完了: {total_fetched}件取得, {total_inserted}件保存")

        except Exception as e:
            self.db.finish_batch(batch_id, "error", total_fetched, total_inserted, str(e))
            logger.error(f"  取引バッチエラー: {e}")

    # ===== 賃料データバッチ =====

    def batch_rental_from_csv(self, csv_path: str = None):
        """CSVから賃料データをDBに取込"""
        from config.settings import DATA_DIR
        csv_path = csv_path or str(DATA_DIR / "rental_comps_tokyo.csv")

        batch_id = self.db.start_batch("rental_comps", "", "")
        fetched = 0
        inserted = 0

        try:
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                records = []
                for row in reader:
                    fetched += 1
                    rent = _safe_int(row.get("rent_monthly"))
                    area = _safe_float(row.get("area_sqm"))
                    if rent and area and rent > 0 and area > 0:
                        addr = row.get("address", "")
                        nearest = row.get("nearest_station", "")
                        pref = _guess_prefecture(addr)
                        sid = resolve_station_id(nearest, None, None, pref)

                        records.append({
                            "address": addr,
                            "rent_monthly": rent,
                            "area_sqm": area,
                            "rent_per_sqm": rent / area,
                            "layout": row.get("layout"),
                            "structure": row.get("structure"),
                            "built_year": _safe_int(row.get("built_year")),
                            "floor": _safe_int(row.get("floor")),
                            "nearest_station": nearest,
                            "station_distance_min": _safe_int(
                                row.get("station_distance_min")
                            ),
                            "station_id": sid,
                            "city_code": _guess_city_code(addr),
                            "source": "CSV",
                        })

                inserted = self.db.upsert_rental_comps(records)

            self.db.finish_batch(batch_id, "completed", fetched, inserted)
            logger.info(f"  賃料CSV取込完了: {fetched}行読込, {inserted}件保存")

        except Exception as e:
            self.db.finish_batch(batch_id, "error", fetched, inserted, str(e))
            logger.error(f"  賃料CSV取込エラー: {e}")

    # ===== 駅メトリクス計算 =====

    def compute_station_metrics(self, pref: str):
        """駅単位のメトリクス再計算"""
        stations = get_stations_by_prefecture(pref)
        year = datetime.now().year

        logger.info(f"  駅メトリクス計算: {len(stations)}駅 (pref={pref})")
        computed = 0

        for s in stations:
            sid = s["station_id"]
            # 地価データ
            lps = self.db.get_land_prices(station_id=sid)
            lp_prices = [r["price_per_sqm"] for r in lps if r["price_per_sqm"] and r["price_per_sqm"] > 0]

            # 賃料データ
            rcs = self.db.get_rental_comps(station_id=sid)
            rc_rents = [r["rent_per_sqm"] for r in rcs if r["rent_per_sqm"] and r["rent_per_sqm"] > 0]

            # 取引データ
            txs = self.db.get_transactions(station_id=sid)

            # 地価・賃料どちらもなければスキップ（参考値がある場合は含める）
            if not lp_prices and not rc_rents:
                continue

            avg_lp = statistics.mean(lp_prices) if lp_prices else 0
            med_lp = statistics.median(lp_prices) if lp_prices else 0
            avg_rent = statistics.mean(rc_rents) if rc_rents else 0
            med_rent = statistics.median(rc_rents) if rc_rents else 0

            changes = [r["price_change_rate"] for r in lps if r.get("price_change_rate") is not None]
            avg_change = statistics.mean(changes) if changes else None

            implied_yield = (avg_rent * 12) / avg_lp if avg_lp > 0 and avg_rent > 0 else 0

            self.db.upsert_station_metrics({
                "station_id": sid,
                "station_name": s["name"],
                "line_name": s.get("line", ""),
                "prefecture_code": pref,
                "city_code": s.get("city_code", ""),
                "year": year,
                "avg_land_price_sqm": avg_lp,
                "median_land_price_sqm": med_lp,
                "land_price_change_rate": avg_change,
                "avg_rent_per_sqm": avg_rent,
                "median_rent_per_sqm": med_rent,
                "implied_yield": implied_yield,
                "yield_gap": implied_yield,
                "distortion_score": implied_yield * 100,
                "sample_count_land": len(lp_prices),
                "sample_count_rent": len(rc_rents),
                "sample_count_tx": len(txs),
                "center_lat": s["lat"],
                "center_lng": s["lon"],
            })
            computed += 1

        logger.info(f"  駅メトリクス計算完了: {computed}駅")

    # ===== 参考データ投入（オフライン） =====

    def _seed_reference_data(self, pref: str):
        """APIなし時に駅別参考データをDBに投入"""
        year = datetime.now().year
        stations = get_stations_by_prefecture(pref)
        lp_records = []
        rc_records = []

        for s in stations:
            sid = s["station_id"]
            price = get_reference_land_price(sid)

            # 地価参考データ
            lp_records.append({
                "address": f"{s['name']}駅 周辺 参考値",
                "price_per_sqm": price,
                "year": year,
                "latitude": s["lat"],
                "longitude": s["lon"],
                "nearest_station": s["name"],
                "station_id": sid,
                "price_type": "参考値",
                "prefecture_code": pref,
                "city_code": s.get("city_code", ""),
            })

            # 賃料参考データ（RC構造の参考値で1件投入）
            rent = get_reference_rent(sid, "RC")
            rc_records.append({
                "address": f"{s['name']}駅 周辺 参考値",
                "rent_monthly": int(rent * 30),  # 30m2想定
                "area_sqm": 30.0,
                "rent_per_sqm": rent,
                "structure": "RC",
                "nearest_station": s["name"],
                "station_id": sid,
                "city_code": s.get("city_code", ""),
                "source": "参考値",
            })

        lp_count = self.db.upsert_land_prices(lp_records)
        rc_count = self.db.upsert_rental_comps(rc_records)
        logger.info(f"  参考データ投入 (pref={pref}): 地価{lp_count}件, 賃料{rc_count}件")

    # ===== API補完（用途地域・防火地域） =====

    def batch_enrich_from_api(self, limit: int = 200) -> int:
        """APIから用途地域・防火・災害リスクを補完"""
        if not self.api.is_configured():
            logger.warning("APIキー未設定: enrich スキップ")
            return 0

        # 座標ありで用途地域未設定の物件を取得
        with self.db._conn() as conn:
            rows = conn.execute("""
                SELECT id, latitude, longitude, zoning, building_coverage_ratio, floor_area_ratio
                FROM land_listings
                WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                AND (zoning IS NULL OR zoning = '' OR building_coverage_ratio IS NULL)
                LIMIT ?
            """, (limit,)).fetchall()

        if not rows:
            logger.info("  API補完対象なし")
            return 0

        logger.info(f"  API補完開始: {len(rows)}件")
        enriched = 0

        for row in [dict(r) for r in rows]:
            try:
                data = self.api.enrich_land_listing(row["latitude"], row["longitude"])
                if not data:
                    continue

                updates = {}
                if data.get("zoning") and not row.get("zoning"):
                    updates["zoning"] = data["zoning"]
                if data.get("building_coverage_ratio") and not row.get("building_coverage_ratio"):
                    try:
                        raw = str(data["building_coverage_ratio"]).replace("%", "").strip()
                        val = float(raw)
                        updates["building_coverage_ratio"] = val / 100 if val > 1 else val
                    except (ValueError, TypeError):
                        pass
                if data.get("floor_area_ratio") and not row.get("floor_area_ratio"):
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
                    params = list(updates.values()) + [row["id"]]
                    with self.db._conn() as conn:
                        conn.execute(
                            f"UPDATE land_listings SET {set_clause}, updated_at=datetime('now','localtime') WHERE id=?",
                            params,
                        )
                    enriched += 1
                    # 建蔽率/容積率が補完されたらpendingにリセット（プラン再生成用）
                    if "building_coverage_ratio" in updates or "floor_area_ratio" in updates:
                        self.db.update_land_listing_status(row["id"], "pending")
            except Exception as e:
                logger.debug(f"  API補完エラー (ID={row['id']}): {e}")

        logger.info(f"  API補完完了: {enriched}/{len(rows)}件")
        return enriched

    def estimate_missing_prices(self) -> int:
        """土地価格がNULL/0の物件に周辺相場から推定価格を付与"""
        with self.db._conn() as conn:
            # 価格なしの物件を取得
            rows = conn.execute("""
                SELECT id, address, station, land_area_sqm, latitude, longitude,
                       zoning, building_coverage_ratio
                FROM land_listings
                WHERE (land_price IS NULL OR land_price = 0)
                AND duplicate_of_id IS NULL
            """).fetchall()

        if not rows:
            logger.info("  価格推定対象なし")
            return 0

        logger.info(f"  価格推定開始: {len(rows)}件")
        estimated = 0

        for row in [dict(r) for r in rows]:
            try:
                price = self._estimate_land_price(row)
                if price and price > 0:
                    with self.db._conn() as conn:
                        conn.execute("""
                            UPDATE land_listings SET land_price=?, memo=COALESCE(memo,'') || ' [推定価格]',
                                updated_at=datetime('now','localtime')
                            WHERE id=?
                        """, (price, row["id"]))
                    estimated += 1
            except Exception as e:
                logger.debug(f"  価格推定エラー (ID={row['id']}): {e}")

        logger.info(f"  価格推定完了: {estimated}/{len(rows)}件")
        return estimated

    def _estimate_land_price(self, listing: dict) -> int:
        """周辺の公示地価・取引事例から土地価格を推定"""
        area = listing.get("land_area_sqm")
        if not area or area <= 0:
            return 0

        price_per_sqm = None

        # 1. 同じ駅の公示地価から推定
        station = listing.get("station", "")
        if station:
            lps = self.db.get_land_prices(station_id="", city_code="", prefecture_code="", limit=5000)
            station_lps = [r for r in lps if r.get("nearest_station") and station in r["nearest_station"]
                          and r.get("price_per_sqm") and r["price_per_sqm"] > 0]
            if station_lps:
                import statistics
                price_per_sqm = statistics.median([r["price_per_sqm"] for r in station_lps])

        # 2. 座標近傍の公示地価
        if not price_per_sqm and listing.get("latitude") and listing.get("longitude"):
            lat, lng = listing["latitude"], listing["longitude"]
            all_lps = self.db.get_land_prices(limit=5000)
            nearby = []
            for r in all_lps:
                if r.get("latitude") and r.get("longitude") and r.get("price_per_sqm"):
                    dlat = abs(r["latitude"] - lat)
                    dlng = abs(r["longitude"] - lng)
                    if dlat < 0.02 and dlng < 0.02:  # ~2km圏内
                        nearby.append(r["price_per_sqm"])
            if nearby:
                import statistics
                price_per_sqm = statistics.median(nearby)

        # 3. 同じ用途地域の取引事例
        if not price_per_sqm:
            zoning = listing.get("zoning", "")
            if zoning:
                txs = self.db.get_transactions(limit=5000)
                matched = [r["price_per_sqm"] for r in txs
                          if r.get("land_use_zone") == zoning and r.get("price_per_sqm") and r["price_per_sqm"] > 0]
                if matched:
                    import statistics
                    price_per_sqm = statistics.median(matched)

        # 4. 全体のデフォルト（最終フォールバック）
        if not price_per_sqm:
            price_per_sqm = 200000  # 20万円/㎡（郊外デフォルト）

        return int(price_per_sqm * area)

    # ===== 実取引データ大量取得 =====

    def ingest_real_transactions(self, prefectures: list = None) -> int:
        """reinfolib APIから実取引データを大量取得"""
        if not self.api.is_configured():
            logger.warning("APIキー未設定: 取引データ取得スキップ")
            return 0

        prefectures = prefectures or ["13", "14", "11", "12"]
        from data.city_master import CITY_MASTER
        from data.station_master import resolve_station_id

        total = 0
        current_year = datetime.now().year

        for pref in prefectures:
            cities = CITY_MASTER.get(pref, [])
            city_codes = [c["code"] for c in cities if c["code"]]

            batch_id = self.db.start_batch("real_transactions", pref)
            pref_total = 0

            try:
                for city in city_codes:
                    logger.info(f"  取引データ取得: pref={pref}, city={city}")
                    raw = self.api.get_transactions(pref, city)

                    records = []
                    for item in raw:
                        try:
                            price = int(item.get("TradePrice", 0))
                            if price <= 0:
                                continue

                            area = None
                            try:
                                area = float(item.get("Area", 0))
                            except (ValueError, TypeError):
                                pass

                            nearest = item.get("NearestStation", "")
                            sid = resolve_station_id(nearest, None, None, pref)

                            records.append({
                                "address": (item.get("Municipality", "") + item.get("DistrictName", "")),
                                "transaction_price": price,
                                "price_per_sqm": price / area if area and area > 0 else None,
                                "transaction_date": item.get("Period", ""),
                                "land_area": area,
                                "building_area": _safe_float(item.get("FloorArea")),
                                "land_shape": item.get("LandShape", ""),
                                "land_use_zone": item.get("CityPlanning", ""),
                                "structure": item.get("Structure", ""),
                                "built_year": _parse_built_year(item.get("BuildingYear", "")),
                                "use": item.get("Use", ""),
                                "property_type": item.get("Type", ""),
                                "nearest_station": nearest,
                                "station_distance_min": _safe_int(item.get("TimeToNearestStation")),
                                "station_id": sid,
                                "prefecture_code": pref,
                                "city_code": city,
                            })
                        except Exception:
                            continue

                    inserted = self.db.upsert_transactions(records)
                    pref_total += inserted

                self.db.finish_batch(batch_id, "completed", pref_total, pref_total)
                total += pref_total
                logger.info(f"  取引データ完了: pref={pref}, {pref_total}件")
            except Exception as e:
                self.db.finish_batch(batch_id, "error", 0, pref_total, str(e))
                logger.error(f"  取引データエラー: {e}")

        return total

    def ingest_real_land_prices(self, prefectures: list = None) -> int:
        """reinfolib APIから公示地価データを大量取得"""
        if not self.api.is_configured():
            logger.warning("APIキー未設定: 地価データ取得スキップ")
            return 0

        prefectures = prefectures or ["13", "14", "11", "12"]
        from data.city_master import CITY_MASTER
        from data.station_master import resolve_station_id

        total = 0
        year = datetime.now().year

        for pref in prefectures:
            cities = CITY_MASTER.get(pref, [])
            city_codes = [c["code"] for c in cities if c["code"]]

            batch_id = self.db.start_batch("real_land_prices", pref)
            pref_total = 0

            try:
                for city in city_codes:
                    raw = self.api.get_land_prices(pref, city, year)

                    records = []
                    for item in raw:
                        try:
                            lat = _safe_float(item.get("latitude"))
                            lon = _safe_float(item.get("longitude"))
                            nearest = item.get("nearestStation", "")
                            sid = resolve_station_id(nearest, lat, lon, pref)

                            records.append({
                                "address": item.get("address", ""),
                                "price_per_sqm": int(item.get("currencyUnitPrice", 0)),
                                "year": int(item.get("year", year)),
                                "latitude": lat,
                                "longitude": lon,
                                "land_use_zone": item.get("useCategory", ""),
                                "acreage": _safe_float(item.get("acreage")),
                                "nearest_station": nearest,
                                "station_distance_min": _safe_int(item.get("stationDistance")),
                                "station_id": sid,
                                "price_change_rate": _safe_float(item.get("priceChangeRate")),
                                "price_type": item.get("priceType", "公示地価"),
                                "prefecture_code": pref,
                                "city_code": city,
                            })
                        except Exception:
                            continue

                    inserted = self.db.upsert_land_prices(records)
                    pref_total += inserted

                self.db.finish_batch(batch_id, "completed", pref_total, pref_total)
                total += pref_total
                logger.info(f"  地価データ完了: pref={pref}, {pref_total}件")
            except Exception as e:
                self.db.finish_batch(batch_id, "error", 0, pref_total, str(e))

        return total

    # ===== XPT002 公示地価・基準地価の一括取得 =====

    def ingest_official_land_prices(self, prefectures: list = None, year: int = None) -> int:
        """reinfolib XPT002 APIから公示地価・基準地価を取得しDBに保存"""
        if not self.api.is_configured():
            logger.warning("APIキー未設定: XPT002地価取得スキップ")
            return 0

        prefectures = prefectures or ["13", "14", "11", "12"]
        from data.station_master import resolve_station_id

        total = 0
        if not year:
            year = datetime.now().year - 1

        for pref in prefectures:
            batch_id = self.db.start_batch("official_land_prices", pref)
            pref_total = 0

            try:
                points = self.api.get_official_land_prices_for_area(
                    pref, year=year,
                )
                logger.info(f"  XPT002地価取得: pref={pref}, {len(points)}件")

                records = []
                for p in points:
                    try:
                        # 休止地点スキップ
                        if p.get("pause_flag") == 1 and not p.get("last_years_price"):
                            continue
                        # 価格: last_years_price(数値) を優先
                        price_per_sqm = 0
                        lyp = p.get("last_years_price")
                        if lyp and isinstance(lyp, (int, float)) and lyp > 0:
                            price_per_sqm = int(lyp)
                        else:
                            price_str = p.get("u_current_years_price_ja", "0")
                            price_per_sqm = int(str(price_str).replace(",", "").replace("円", "").strip() or 0)
                        if price_per_sqm <= 0:
                            continue

                        lat = p.get("_lat")
                        lng = p.get("_lng")
                        nearest = p.get("nearest_station_name_ja", "")
                        sid = resolve_station_id(nearest, lat, lng, pref)

                        change_rate = None
                        try:
                            cr = p.get("year_on_year_change_rate")
                            if cr is not None:
                                change_rate = float(cr)
                        except (ValueError, TypeError):
                            pass

                        records.append({
                            "address": p.get("place_name_ja", ""),
                            "price_per_sqm": price_per_sqm,
                            "year": year,
                            "latitude": lat,
                            "longitude": lng,
                            "land_use_zone": p.get("regulations_use_category_name_ja", ""),
                            "acreage": None,
                            "nearest_station": nearest,
                            "station_distance_min": None,
                            "station_id": sid,
                            "price_change_rate": change_rate,
                            "price_type": "公示地価",
                            "prefecture_code": pref,
                            "city_code": p.get("city_code", ""),
                        })
                    except Exception:
                        continue

                inserted = self.db.upsert_land_prices(records)
                pref_total += inserted
                self.db.finish_batch(batch_id, "completed", len(points), pref_total)
                total += pref_total
                logger.info(f"  XPT002地価完了: pref={pref}, {pref_total}件保存")
            except Exception as e:
                self.db.finish_batch(batch_id, "error", 0, pref_total, str(e))
                logger.error(f"  XPT002地価エラー (pref={pref}): {e}")

        return total

    # ===== 土地物件スクレイピングバッチ =====

    def batch_land_listings(
        self,
        source: str = "suumo",
        pref: str = "13",
        price_min: int = None,
        price_max: int = None,
        area_min: float = None,
        walk_max: int = None,
        max_pages: int = 3,
    ) -> int:
        """土地物件のバッチスクレイピング"""
        from agents.land_scraper_agent import LandScraperAgent

        scraper = LandScraperAgent()
        batch_id = self.db.start_batch("land_listings", pref)
        total_fetched = 0
        total_inserted = 0

        try:
            listings = scraper.run(
                source=source,
                prefecture_code=pref,
                price_min=price_min,
                price_max=price_max,
                area_min=area_min,
                walk_max=walk_max,
                max_pages=max_pages,
            )
            total_fetched = len(listings)

            records = [l.to_dict() for l in listings]
            total_inserted = self.db.upsert_land_listings(records)

            self.db.finish_batch(batch_id, "completed", total_fetched, total_inserted)
            logger.info(
                f"  土地スクレイピングバッチ完了: "
                f"{total_fetched}件取得, {total_inserted}件保存"
            )
        except Exception as e:
            self.db.finish_batch(batch_id, "error", total_fetched, total_inserted, str(e))
            logger.error(f"  土地スクレイピングバッチエラー: {e}")

        return total_inserted

    def batch_land_listings_from_csv(self, csv_path: str) -> int:
        """CSVから土地物件を一括取込"""
        from agents.land_scraper_agent import LandScraperAgent

        scraper = LandScraperAgent()
        batch_id = self.db.start_batch("land_listings_csv", "")

        try:
            listings = scraper.import_from_csv(csv_path)
            records = [l.to_dict() for l in listings]
            inserted = self.db.upsert_land_listings(records)

            self.db.finish_batch(batch_id, "completed", len(listings), inserted)
            logger.info(f"  CSV土地取込完了: {len(listings)}件読込, {inserted}件保存")
            # ジオコーディング
            self.batch_geocode()
            self.batch_enrich_from_api()
            self.estimate_missing_prices()
            return inserted
        except Exception as e:
            self.db.finish_batch(batch_id, "error", 0, 0, str(e))
            logger.error(f"  CSV土地取込エラー: {e}")
            return 0

    # ===== 建築プラン一括生成 =====

    def batch_building_plans(
        self,
        listing_ids: list = None,
        rent_per_sqm: float = None,
    ) -> int:
        """土地物件に対して建築プランを一括生成"""
        from agents.plan_agent import PlanAgent
        from models.land_listing import LandListing

        plan_agent = PlanAgent()
        batch_id = self.db.start_batch("building_plans", "")
        total_plans = 0

        try:
            if listing_ids:
                listings_data = [
                    self.db.get_land_listing_by_id(lid)
                    for lid in listing_ids
                ]
                listings_data = [l for l in listings_data if l]
            else:
                # 全物件を再計算（pending以外も含む）
                listings_data = self.db.get_land_listings(limit=2000)

            for ld in listings_data:
                try:
                    listing = LandListing.from_dict(ld)
                    listing.id = ld["id"]

                    summary = plan_agent.run(
                        listing, rent_per_sqm=rent_per_sqm
                    )

                    if summary.plans:
                        plan_dicts = [p.to_dict() for p in summary.plans]
                        for pd in plan_dicts:
                            pd["land_listing_id"] = ld["id"]
                        inserted = self.db.upsert_building_plans(plan_dicts)
                        total_plans += inserted

                    self.db.update_land_listing_status(ld["id"], "ok")
                except Exception as e:
                    logger.warning(f"  プラン生成エラー (ID={ld.get('id')}): {e}")
                    self.db.update_land_listing_status(ld["id"], "error")

            self.db.finish_batch(
                batch_id, "completed", len(listings_data), total_plans
            )
            logger.info(
                f"  プラン生成バッチ完了: "
                f"{len(listings_data)}物件, {total_plans}プラン生成"
            )
        except Exception as e:
            self.db.finish_batch(batch_id, "error", 0, total_plans, str(e))
            logger.error(f"  プラン生成バッチエラー: {e}")

        return total_plans

    def batch_land_judgments(self, listing_ids: list = None) -> int:
        """土地物件のフル投資判定を一括実行"""
        from agents.orchestrator_agent import OrchestratorAgent
        from models.land_listing import LandListing
        from models.building_plan import BuildingPlan

        orchestrator = OrchestratorAgent()
        batch_id = self.db.start_batch("land_judgments", "")
        total = 0

        try:
            if listing_ids:
                listings_data = [self.db.get_land_listing_by_id(lid) for lid in listing_ids]
                listings_data = [l for l in listings_data if l]
            else:
                # Get listings with plans but no judgment
                listings_data = self.db.get_land_listings(status="ok", limit=500)

            for ld in listings_data:
                # Skip if already judged
                existing = self.db.get_land_judgment(ld["id"])
                if existing:
                    continue

                try:
                    listing = LandListing.from_dict(ld)
                    listing.id = ld["id"]

                    # Get best plan
                    plans = self.db.get_building_plans(ld["id"])
                    if not plans:
                        continue

                    best_plan = BuildingPlan.from_dict(plans[0])

                    # Convert to Property and run judgment
                    prop = listing.to_property(best_plan)
                    # 資産性グレードをDBから取得して判定に反映
                    as_data = self.db.get_asset_score(ld["id"])
                    as_grade = as_data.get("grade") if as_data else None
                    judgment = orchestrator.run(prop, asset_score_grade=as_grade)

                    # Save
                    self.db.save_land_judgment({
                        "land_listing_id": ld["id"],
                        "building_plan_id": plans[0].get("id"),
                        "grade": judgment.grade,
                        "recommendation": judgment.recommendation,
                        "overall_score": judgment.overall_score,
                        "confidence": judgment.confidence,
                        "key_metrics": judgment.key_metrics,
                        "property_id": prop.id,
                        "property_name": prop.name,
                        "score_breakdown": judgment.score_breakdown,
                        "strengths": judgment.strengths,
                        "weaknesses": judgment.weaknesses,
                        "risks": judgment.risks,
                        "opportunities": judgment.opportunities,
                    })
                    total += 1
                    logger.info(f"  判定完了: {ld['address']} => {judgment.grade}")
                except Exception as e:
                    logger.warning(f"  判定エラー (ID={ld.get('id')}): {e}")

            self.db.finish_batch(batch_id, "completed", len(listings_data), total)
            logger.info(f"  土地判定バッチ完了: {total}件")
        except Exception as e:
            self.db.finish_batch(batch_id, "error", 0, total, str(e))
            logger.error(f"  土地判定バッチエラー: {e}")

        return total

    def run_land_pipeline(
        self,
        source: str = "suumo",
        pref: str = "13",
        price_min: int = None,
        price_max: int = None,
        area_min: float = None,
        walk_max: int = None,
        max_pages: int = 3,
        rent_per_sqm: float = None,
        run_judgment: bool = False,
    ) -> dict:
        """土地物件パイプライン: スクレイピング → プラン生成 → 判定"""
        logger.info(f"=== 土地パイプライン開始: source={source}, pref={pref} ===")

        # Step 1: スクレイピング
        scraped = self.batch_land_listings(
            source=source, pref=pref,
            price_min=price_min, price_max=price_max,
            area_min=area_min, walk_max=walk_max,
            max_pages=max_pages,
        )

        # Step 2: ジオコーディング
        self.batch_geocode()

        # Step 3: API補完（用途地域・防火地域）
        self.batch_enrich_from_api()

        # Step 4: 価格推定（NULL/0の物件に相場価格を付与）
        self.estimate_missing_prices()

        # Step 5: プラン生成
        plans = self.batch_building_plans(rent_per_sqm=rent_per_sqm)

        # Step 6: 重複検出
        self.db.detect_duplicates()

        result = {"listings_saved": scraped, "plans_generated": plans}

        # Step 7: 資産性スコアリング（接道・ハザード・整形・標高・人口）
        scored = self.batch_asset_scores()
        result["asset_scores_generated"] = scored

        # Step 8: 投資判定（オプション）
        if run_judgment:
            judgments = self.batch_land_judgments()
            result["judgments_generated"] = judgments

        logger.info(f"=== 土地パイプライン完了: {scraped}物件, {plans}プラン, {scored}スコア ===")
        return result

    # ===== 資産性スコアリング =====

    def batch_asset_scores(self, listing_ids: list = None, limit: int = 200) -> int:
        """土地物件の資産性（接道・ハザード・整形・標高・人口）を一括スコアリング"""
        from agents.asset_score_agent import AssetScoreAgent

        agent = AssetScoreAgent()
        batch_id = self.db.start_batch("asset_scores", "")
        total = 0

        try:
            if listing_ids:
                listings_data = [self.db.get_land_listing_by_id(lid) for lid in listing_ids]
                listings_data = [l for l in listings_data if l and l.get("latitude") and l.get("longitude")]
            else:
                listings_data = self.db.get_unscored_listings(limit)

            logger.info(f"  資産性スコアリング対象: {len(listings_data)}物件")

            for ld in listings_data:
                try:
                    result = agent.run(
                        lat=ld["latitude"],
                        lng=ld["longitude"],
                        land_area_sqm=ld.get("land_area_sqm"),
                        station_distance_min=ld.get("walk_minutes"),
                        station_name=ld.get("station"),
                        city_code=ld.get("city_code"),
                        prefecture_code=ld.get("prefecture_code"),
                        has_retaining_wall=bool(ld.get("has_retaining_wall")),
                    )

                    self.db.upsert_asset_score({
                        "land_listing_id": ld["id"],
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
                    total += 1
                    logger.info(f"  スコアリング完了: {ld['address']} => {result.grade} ({result.overall_score:.1f})")
                except Exception as e:
                    logger.warning(f"  スコアリングエラー (ID={ld.get('id')}): {e}")

            self.db.finish_batch(batch_id, "completed", len(listings_data), total)
            logger.info(f"  資産性スコアリングバッチ完了: {total}件")
        except Exception as e:
            self.db.finish_batch(batch_id, "error", 0, total, str(e))
            logger.error(f"  資産性スコアリングバッチエラー: {e}")

        return total

    # ===== ジオコーディング =====

    def batch_geocode(self, limit: int = 200) -> int:
        """未ジオコーディングの土地物件に座標を付与"""
        from data.geocoder import Geocoder

        geocoder = Geocoder()
        ungeocoded = self.db.get_ungeocoded_listings(limit)

        if not ungeocoded:
            logger.info("  ジオコーディング対象なし")
            return 0

        logger.info(f"  ジオコーディング開始: {len(ungeocoded)}件")
        geocoded = 0

        for item in ungeocoded:
            address = item["address"]
            # Normalize: "埼玉県 / 狭山市" → "埼玉県狭山市"
            import re as _re
            clean_addr = address.replace(" / ", "").replace("／", "").replace("\u3000", "").replace("　", "").strip()
            # Remove lot numbers that confuse geocoder
            clean_addr = _re.sub(r'[\d-]+番[\d-]*号?$', '', clean_addr)
            clean_addr = _re.sub(r'\(.*?\)', '', clean_addr)

            coords = geocoder.geocode(clean_addr)
            if not coords:
                # Try progressively shorter address
                # "東京都練馬区石神井町7丁目" → "東京都練馬区石神井町" → "東京都練馬区"
                for trim in [r'\d+丁目.*$', r'[町村]\d.*$', r'[市区町村郡].*$']:
                    shorter = _re.sub(trim, lambda m: m.group(0)[0] if m.group(0) else '', clean_addr)
                    if shorter != clean_addr:
                        coords = geocoder.geocode(shorter)
                        if coords:
                            break
                        clean_addr = shorter

            if coords:
                lat, lng = coords
                self.db.update_land_listing_coords(item["id"], lat, lng)
                geocoded += 1

        logger.info(f"  ジオコーディング完了: {geocoded}/{len(ungeocoded)}件")
        return geocoded

    # ===== マイソク再解析バッチ =====

    def batch_enrich_maisoku(self, limit: int = 200) -> int:
        """マイソクPDF付き物件を再解析して不足フィールドを補完

        maisoku_pdf_path が設定されている物件のうち、
        用途地域・建蔽率・容積率・道路幅員などが未設定のものを再解析する。
        """
        from agents.maisoku_agent import MaisokuAgent

        with self.db._conn() as conn:
            rows = conn.execute("""
                SELECT id, maisoku_pdf_path, latitude, longitude,
                       address, zoning, building_coverage_ratio, floor_area_ratio,
                       road_width_m, frontage_m, depth_m, station, walk_minutes
                FROM land_listings
                WHERE maisoku_pdf_path IS NOT NULL AND maisoku_pdf_path != ''
                AND (
                    zoning IS NULL OR zoning = ''
                    OR building_coverage_ratio IS NULL
                    OR floor_area_ratio IS NULL
                    OR road_width_m IS NULL
                    OR frontage_m IS NULL
                )
                LIMIT ?
            """, (limit,)).fetchall()

        if not rows:
            logger.info("  マイソク再解析対象なし")
            return 0

        logger.info(f"  マイソク再解析開始: {len(rows)}件")
        agent = MaisokuAgent()
        enriched = 0

        for row in [dict(r) for r in rows]:
            pdf_path = row["maisoku_pdf_path"]
            try:
                from pathlib import Path
                if not Path(pdf_path).exists():
                    logger.debug(f"  ファイル不在スキップ: {pdf_path}")
                    continue

                parsed = agent.run(
                    file_path=pdf_path,
                    lat=row.get("latitude"),
                    lng=row.get("longitude"),
                    enrich_from_api=True,
                )

                if parsed.get("error") or parsed.get("_rejected"):
                    continue

                # 既存値が空のフィールドのみ補完
                updates = {}
                field_map = {
                    "address": "address",
                    "zoning": "zoning",
                    "building_coverage_ratio": "building_coverage_ratio",
                    "floor_area_ratio": "floor_area_ratio",
                    "road_width_m": "road_width_m",
                    "road_legal_type": "road_legal_type",
                    "frontage_m": "frontage_m",
                    "depth_m": "depth_m",
                    "land_price": "land_price",
                    "station": "station",
                    "walk_minutes": "walk_minutes",
                    "railway_line": "railway_line",
                    "land_shape": "land_shape",
                    "land_area_sqm": "land_area_sqm",
                }
                for parsed_key, db_key in field_map.items():
                    new_val = parsed.get(parsed_key)
                    old_val = row.get(db_key)
                    if new_val is not None and new_val != "" and (old_val is None or old_val == ""):
                        updates[db_key] = new_val

                # bool fields
                if parsed.get("corner_lot"):
                    updates["corner_lot"] = 1
                if parsed.get("setback_required"):
                    updates["setback_required"] = 1
                if parsed.get("quasi_fireproof"):
                    updates["quasi_fireproof"] = 1

                if updates:
                    set_clause = ", ".join(f"{k}=?" for k in updates)
                    params = list(updates.values()) + [row["id"]]
                    with self.db._conn() as conn:
                        conn.execute(
                            f"UPDATE land_listings SET {set_clause}, updated_at=datetime('now','localtime') WHERE id=?",
                            params,
                        )
                    enriched += 1

                    # 建蔽率/容積率が補完されたらpendingにリセット（プラン再生成用）
                    if "building_coverage_ratio" in updates or "floor_area_ratio" in updates:
                        self.db.update_land_listing_status(row["id"], "pending")

            except Exception as e:
                logger.debug(f"  マイソク再解析エラー (ID={row['id']}): {e}")

        logger.info(f"  マイソク再解析完了: {enriched}/{len(rows)}件補完")
        return enriched

    # ===== レガシー: エリアメトリクス計算 =====

    def compute_all_area_metrics(
        self, pref: str, city_codes: List[str] = None
    ):
        """全エリアのメトリクス再計算（レガシー互換）"""
        city_codes = city_codes or DEFAULT_CITY_CODES
        year = datetime.now().year
        for city in city_codes:
            self._compute_area_metric(pref, city, year)

    def _compute_area_metric(self, pref: str, city: str, year: int):
        lps = self.db.get_land_prices(city_code=city)
        lp_prices = [r["price_per_sqm"] for r in lps if r["price_per_sqm"] > 0]
        rcs = self.db.get_rental_comps(city_code=city)
        rc_rents = [r["rent_per_sqm"] for r in rcs if r["rent_per_sqm"] > 0]
        txs = self.db.get_transactions(city_code=city)

        avg_lp = statistics.mean(lp_prices) if lp_prices else 0
        med_lp = statistics.median(lp_prices) if lp_prices else 0
        avg_rent = statistics.mean(rc_rents) if rc_rents else 0
        med_rent = statistics.median(rc_rents) if rc_rents else 0

        changes = [r["price_change_rate"] for r in lps if r.get("price_change_rate") is not None]
        avg_change = statistics.mean(changes) if changes else None
        implied_yield = (avg_rent * 12) / avg_lp if avg_lp > 0 and avg_rent > 0 else 0

        lats = [r["latitude"] for r in lps if r.get("latitude")]
        lngs = [r["longitude"] for r in lps if r.get("longitude")]
        center_lat = statistics.mean(lats) if lats else None
        center_lng = statistics.mean(lngs) if lngs else None

        self.db.upsert_area_metrics({
            "city_code": city,
            "city_name": CITY_NAME_MAP.get(city, ""),
            "prefecture_code": pref,
            "year": year,
            "avg_land_price_sqm": avg_lp,
            "median_land_price_sqm": med_lp,
            "land_price_change_rate": avg_change,
            "avg_rent_per_sqm": avg_rent,
            "median_rent_per_sqm": med_rent,
            "implied_yield": implied_yield,
            "yield_gap": implied_yield,
            "distortion_score": implied_yield * 100,
            "sample_count_land": len(lp_prices),
            "sample_count_rent": len(rc_rents),
            "sample_count_tx": len(txs),
            "center_lat": center_lat,
            "center_lng": center_lng,
        })


# ===== ユーティリティ =====

def _safe_int(val) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None

def _safe_float(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None

def _parse_built_year(s: str) -> Optional[int]:
    if not s:
        return None
    import re
    m = re.search(r"(令和|平成|昭和)(\d+)年", s)
    if m:
        era, y = m.group(1), int(m.group(2))
        if era == "令和": return 2018 + y
        elif era == "平成": return 1988 + y
        elif era == "昭和": return 1925 + y
    m2 = re.search(r"(\d{4})年?", s)
    return int(m2.group(1)) if m2 else None

def _guess_city_code(address: str) -> str:
    WARDS = {
        "千代田区": "13101", "中央区": "13102", "港区": "13103",
        "新宿区": "13104", "文京区": "13105", "台東区": "13106",
        "墨田区": "13107", "江東区": "13108", "品川区": "13109",
        "目黒区": "13110", "大田区": "13111", "世田谷区": "13112",
        "渋谷区": "13113", "中野区": "13114", "杉並区": "13115",
        "豊島区": "13116", "北区": "13117", "荒川区": "13118",
        "板橋区": "13119", "練馬区": "13120", "足立区": "13121",
        "葛飾区": "13122", "江戸川区": "13123",
    }
    for ward, code in WARDS.items():
        if ward in address:
            return code
    return ""

def _guess_prefecture(address: str) -> str:
    """住所から都道府県コードを推定"""
    if "東京" in address: return "13"
    if "神奈川" in address: return "14"
    if "埼玉" in address: return "11"
    if "千葉" in address: return "12"
    return "13"  # デフォルト


class MeshGrowthPipeline:
    """メッシュヒートマップのデータ充填率を継続的に向上させるパイプライン

    1. 賃料スクレイピング（SUUMO: 全県全エリア巡回）
    2. 座標なしレコードのジオコード
    3. API地価・人口取込
    4. メッシュ再集計+空間補間
    """

    def __init__(self):
        self.db = Database()

    def run(self, prefectures=None, max_rental_pages=5, geocode_batch=200):
        """フルパイプライン実行"""
        prefectures = prefectures or BATCH_TARGET_PREFECTURES
        logger.info(f"=== MeshGrowthPipeline開始: {prefectures} ===")

        # Step 1: 賃料スクレイピング（最大のボトルネック）
        self._scrape_rentals(prefectures, max_rental_pages)

        # Step 2: 座標なしレコードのジオコード
        self._geocode_missing(geocode_batch)

        # Step 3: メッシュ再集計 + 空間補間（HTTP経由）
        self._trigger_mesh_recompute()

        # Step 4: カバレッジログ
        self._log_coverage()

        logger.info("=== MeshGrowthPipeline完了 ===")

    def _scrape_rentals(self, prefectures, max_pages):
        """全県の賃料データをSUUMOからスクレイピング"""
        from agents.scraper_agent import ScraperAgent
        scraper = ScraperAgent()

        for pref in prefectures:
            logger.info(f"  賃料スクレイピング: pref={pref}")
            try:
                results = scraper.scrape_rentals(
                    prefecture_code=pref,
                    max_pages=max_pages,
                )
                if results:
                    saved = self.db.upsert_rental_comps(results)
                    logger.info(f"    → {len(results)}件取得, {saved}件保存")
                else:
                    logger.info(f"    → 0件")
            except Exception as e:
                logger.error(f"    賃料スクレイピングエラー (pref={pref}): {e}")

    def _geocode_missing(self, batch_size):
        """座標なし賃料レコードをジオコード"""
        import time
        with self.db._conn() as conn:
            rows = conn.execute("""
                SELECT id, address, nearest_station FROM rental_comps
                WHERE (latitude IS NULL OR latitude = 0) AND address != ''
                LIMIT ?
            """, (batch_size,)).fetchall()

        if not rows:
            logger.info("  ジオコード対象なし")
            return

        logger.info(f"  ジオコード開始: {len(rows)}件")
        geocoded = 0

        for r in [dict(x) for x in rows]:
            try:
                lat, lng = self._geocode_address(r["address"])
                if lat and lng:
                    with self.db._conn() as conn:
                        conn.execute(
                            "UPDATE rental_comps SET latitude=?, longitude=? WHERE id=?",
                            (lat, lng, r["id"]),
                        )
                    geocoded += 1
                time.sleep(0.5)  # API rate limit
            except Exception:
                continue

        logger.info(f"  ジオコード完了: {geocoded}/{len(rows)}件")

    def _geocode_address(self, address):
        """国土地理院ジオコーディングAPI"""
        import requests
        try:
            resp = requests.get(
                "https://msearch.gsi.go.jp/address-search/AddressSearch",
                params={"q": address},
                timeout=5,
            )
            data = resp.json()
            if data and len(data) > 0:
                coords = data[0].get("geometry", {}).get("coordinates", [])
                if len(coords) >= 2:
                    return coords[1], coords[0]  # lat, lng
        except Exception:
            pass
        return None, None

    def _trigger_mesh_recompute(self):
        """メッシュ再集計をトリガー（HTTP経由: サーバープロセス内で非同期実行）"""
        import requests as req
        try:
            from config.settings import WEB_PORT
            resp = req.post(f"http://127.0.0.1:{WEB_PORT}/api/mesh/compute", timeout=5)
            logger.info(f"  メッシュ再集計トリガー: {resp.status_code}")
        except Exception:
            logger.info("  メッシュ再集計: サーバー未起動のためスキップ")

    def _log_coverage(self):
        """現在のカバレッジをログ出力"""
        with self.db._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM mesh_250m").fetchone()[0]
            for metric, col, cnt_col in [
                ("rent", "avg_rent_sqm", "rent_count"),
                ("land_price", "avg_land_price_sqm", "land_price_count"),
                ("transactions", "avg_tx_price_sqm", "tx_count"),
            ]:
                obs = conn.execute(f"SELECT COUNT(*) FROM mesh_250m WHERE {col} IS NOT NULL AND {cnt_col} > 0").fetchone()[0]
                est = conn.execute(f"SELECT COUNT(*) FROM mesh_250m WHERE {cnt_col} = -1").fetchone()[0]
                pct = (obs + est) / max(total, 1) * 100
                logger.info(f"  coverage {metric}: obs={obs} est={est} ({pct:.1f}%)")
