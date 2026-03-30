"""
不動産情報ライブラリ API クライアント
https://www.reinfolib.mlit.go.jp/help/apiManual/

主要エンドポイント:
  XIT001: 不動産取引価格情報取得 (year+quarter+area/city/station)
  XIT002: 市区町村一覧取得
  XPT001: 取引価格ポイント情報 (タイル座標ベース z/x/y)
  XKT001: 都市計画情報 (座標ベース)
  XKT014: 防火地域
  XKT016: 洪水浸水想定区域
  XKT022: 土砂災害警戒区域
"""
import os
import json
import math
import time
import hashlib
import requests
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime

from config.settings import (
    REINFOLIB_API_BASE,
    REINFOLIB_API_KEY,
    CACHE_DIR,
    TRANSACTION_YEARS_BACK,
)


class ReinfolibClient:
    """不動産情報ライブラリ REST APIクライアント"""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get("REINFOLIB_API_KEY", REINFOLIB_API_KEY)
        self.base_url = REINFOLIB_API_BASE
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["Ocp-Apim-Subscription-Key"] = self.api_key
        self.cache_dir = CACHE_DIR / "reinfolib"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._rate_limit_interval = 1.0
        self._last_request_time = 0.0

    def _cache_key(self, endpoint: str, params: dict) -> str:
        raw = f"{endpoint}_{json.dumps(params, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _get_cache(self, cache_key: str, max_age_hours: int = 24) -> Optional[dict]:
        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < max_age_hours * 3600:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        return None

    def _set_cache(self, cache_key: str, data: dict):
        cache_file = self.cache_dir / f"{cache_key}.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._rate_limit_interval:
            time.sleep(self._rate_limit_interval - elapsed)
        self._last_request_time = time.time()

    def _request(self, endpoint: str, params: dict = None,
                 cache_hours: int = 24) -> Dict[str, Any]:
        """APIリクエスト実行（キャッシュ付き）"""
        params = params or {}
        cache_key = self._cache_key(endpoint, params)

        cached = self._get_cache(cache_key, cache_hours)
        if cached is not None:
            return cached

        self._rate_limit()
        url = f"{self.base_url}/{endpoint}"
        resp = self.session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        self._set_cache(cache_key, data)
        return data

    # ===== 市区町村一覧 =====

    def get_cities(self, prefecture_code: str) -> List[Dict]:
        """市区町村一覧取得 (XIT002)"""
        data = self._request("XIT002", {"area": prefecture_code})
        return data.get("data", [])

    # ===== 取引価格情報 (XIT001) =====

    def get_transactions(
        self,
        year: int,
        quarter: int,
        area: str = "",
        city: str = "",
        price_classification: str = "",
    ) -> List[Dict]:
        """
        不動産取引価格情報取得 (XIT001)

        Args:
            year: 西暦年 (2005以降)
            quarter: 四半期 (1-4)
            area: 都道府県コード (例: "13")
            city: 市区町村コード (例: "13101")
            price_classification: "01"=取引価格のみ, "02"=成約価格のみ, ""=両方
        """
        params = {"year": str(year), "quarter": str(quarter)}
        if area:
            params["area"] = area
        if city:
            params["city"] = city
        if price_classification:
            params["priceClassification"] = price_classification

        data = self._request("XIT001", params, cache_hours=168)
        return data.get("data", [])

    def get_transactions_multi(
        self,
        area: str = "",
        city: str = "",
        years_back: int = None,
    ) -> List[Dict]:
        """複数四半期分の取引データをまとめて取得"""
        years_back = years_back or TRANSACTION_YEARS_BACK
        current_year = datetime.now().year
        all_records = []

        for y in range(current_year - years_back, current_year + 1):
            for q in range(1, 5):
                # 未来の四半期はスキップ
                if y == current_year and q > (datetime.now().month - 1) // 3 + 1:
                    break
                try:
                    records = self.get_transactions(y, q, area=area, city=city)
                    all_records.extend(records)
                except Exception:
                    continue

        return all_records

    # ===== 地価公示・地価調査ポイント (XPT001) =====
    # タイル座標ベースのAPI

    @staticmethod
    def _lat_lng_to_tile(lat: float, lng: float, zoom: int):
        """緯度経度→タイル座標変換"""
        n = 2 ** zoom
        x = int((lng + 180.0) / 360.0 * n)
        lat_rad = math.radians(lat)
        y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
        return x, y

    @staticmethod
    def _bounds_to_tiles(south: float, west: float, north: float, east: float, zoom: int):
        """boundsを覆うタイル座標リストを返す"""
        n = 2 ** zoom

        def to_tile(lat, lng):
            tx = int((lng + 180.0) / 360.0 * n)
            lat_rad = math.radians(lat)
            ty = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
            return tx, ty

        x_min, y_min = to_tile(north, west)  # 北西 = y小
        x_max, y_max = to_tile(south, east)  # 南東 = y大

        tiles = []
        for tx in range(x_min, x_max + 1):
            for ty in range(y_min, y_max + 1):
                tiles.append((tx, ty))
        return tiles

    def _multi_tile_request(self, endpoint: str, tiles: list,
                            extra_params: dict = None, cache_hours: int = 720) -> list:
        """複数タイルを取得してfeaturesを結合"""
        all_features = []
        seen_ids = set()
        extra_params = extra_params or {}

        for tx, ty in tiles:
            zoom = extra_params.get("z", "13")
            params = {
                "response_format": "geojson",
                "z": str(zoom),
                "x": str(tx),
                "y": str(ty),
            }
            params.update(extra_params)
            # z/x/y は上書き
            params["x"] = str(tx)
            params["y"] = str(ty)

            try:
                data = self._request(endpoint, params, cache_hours=cache_hours)
                for f in data.get("features", []):
                    # 重複排除（_id or point_id）
                    fid = f.get("properties", {}).get("_id") or f.get("properties", {}).get("point_id")
                    if fid and fid in seen_ids:
                        continue
                    if fid:
                        seen_ids.add(fid)
                    all_features.append(f)
            except Exception:
                continue

        return all_features

    def get_land_price_points(
        self,
        lat: float,
        lng: float,
        zoom: int = 13,
        from_period: str = "",
        to_period: str = "",
    ) -> List[Dict]:
        """
        取引価格ポイント情報取得 (XPT001) - GeoJSON

        Args:
            lat, lng: 中心座標
            zoom: ズームレベル (11-15)
            from_period: 開始期間 "YYYYQ" (例: "20231")
            to_period: 終了期間 "YYYYQ" (例: "20254")
        """
        current_year = datetime.now().year
        if not from_period:
            from_period = f"{current_year - 3}1"
        if not to_period:
            to_period = f"{current_year}4"

        x, y = self._lat_lng_to_tile(lat, lng, zoom)

        params = {
            "response_format": "geojson",
            "z": str(zoom),
            "x": str(x),
            "y": str(y),
            "from": from_period,
            "to": to_period,
        }

        data = self._request("XPT001", params, cache_hours=720)
        # GeoJSONのfeaturesを返す
        features = data.get("features", [])
        return [f.get("properties", {}) for f in features]

    def get_land_prices_for_area(
        self,
        prefecture_code: str,
        city_code: str = "",
        years_back: int = 3,
    ) -> List[Dict]:
        """
        市区町村の公示地価ポイントを取得（XIT001の土地取引データから推定）
        city_code の中心座標を使ってXPT001タイルを取得
        """
        from data.city_master import CITY_MASTER

        results = []
        cities = CITY_MASTER.get(prefecture_code, [])

        target_cities = cities
        if city_code:
            target_cities = [c for c in cities if c["code"] == city_code]

        # 各市区町村の中心座標でタイル取得（概算）
        # 東京都の市区町村中心座標テーブル
        city_centers = self._get_city_centers(prefecture_code)

        current_year = datetime.now().year
        from_period = f"{current_year - years_back}1"
        to_period = f"{current_year}4"

        for city in target_cities:
            code = city.get("code", "")
            if not code:
                continue
            center = city_centers.get(code)
            if not center:
                continue
            try:
                points = self.get_land_price_points(
                    center[0], center[1], zoom=13,
                    from_period=from_period, to_period=to_period,
                )
                for p in points:
                    p["city_code"] = code
                    p["prefecture_code"] = prefecture_code
                results.extend(points)
            except Exception:
                continue

        return results

    def _get_city_centers(self, pref: str) -> Dict[str, tuple]:
        """市区町村の概算中心座標を返す（一都三県全域）"""
        centers = {
            # ===== 東京都23区 =====
            "13101": (35.694, 139.754), "13102": (35.672, 139.774),
            "13103": (35.658, 139.752), "13104": (35.694, 139.703),
            "13105": (35.708, 139.752), "13106": (35.713, 139.781),
            "13107": (35.711, 139.802), "13108": (35.673, 139.817),
            "13109": (35.609, 139.730), "13110": (35.641, 139.698),
            "13111": (35.561, 139.716), "13112": (35.646, 139.653),
            "13113": (35.664, 139.698), "13114": (35.708, 139.664),
            "13115": (35.700, 139.637), "13116": (35.726, 139.716),
            "13117": (35.753, 139.737), "13118": (35.736, 139.783),
            "13119": (35.751, 139.709), "13120": (35.735, 139.652),
            "13121": (35.776, 139.805), "13122": (35.742, 139.847),
            "13123": (35.707, 139.868),
            # ===== 東京都市部 =====
            "13201": (35.683, 139.559), "13202": (35.714, 139.560),
            "13203": (35.682, 139.529), "13204": (35.706, 139.481),
            "13205": (35.672, 139.559), "13206": (35.728, 139.520),
            "13207": (35.699, 139.414), "13208": (35.671, 139.604),
            "13209": (35.756, 139.541), "13210": (35.731, 139.481),
            "13211": (35.748, 139.531), "13212": (35.728, 139.559),
            "13213": (35.753, 139.457), "13214": (35.713, 139.424),
            "13215": (35.664, 139.443), "13218": (35.639, 139.492),
            "13219": (35.672, 139.470), "13220": (35.779, 139.497),
            "13221": (35.786, 139.524), "13222": (35.773, 139.562),
            "13223": (35.734, 139.409), "13224": (35.762, 139.447),
            "13225": (35.688, 139.371), "13227": (35.626, 139.446),
            "13228": (35.638, 139.528), "13229": (35.751, 139.342),
            # ===== 神奈川県 =====
            "14101": (35.466, 139.642), "14102": (35.475, 139.626),
            "14103": (35.456, 139.622), "14104": (35.441, 139.651),
            "14105": (35.444, 139.600), "14106": (35.459, 139.592),
            "14107": (35.474, 139.586), "14108": (35.483, 139.603),
            "14109": (35.507, 139.600), "14110": (35.491, 139.552),
            "14111": (35.479, 139.560), "14112": (35.437, 139.559),
            "14113": (35.420, 139.585), "14114": (35.401, 139.597),
            "14115": (35.362, 139.595), "14116": (35.347, 139.575),
            "14117": (35.408, 139.651), "14118": (35.438, 139.689),
            "14131": (35.531, 139.703), "14132": (35.573, 139.658),
            "14133": (35.556, 139.717), "14134": (35.590, 139.694),
            "14135": (35.561, 139.633), "14136": (35.558, 139.580),
            "14137": (35.539, 139.542), "14150": (35.334, 139.550),
            "14201": (35.560, 139.483), "14203": (35.504, 139.445),
            "14204": (35.489, 139.406), "14205": (35.438, 139.493),
            "14206": (35.405, 139.487), "14207": (35.345, 139.463),
            "14208": (35.368, 139.398), "14209": (35.327, 139.371),
            "14210": (35.443, 139.340), "14211": (35.346, 139.319),
            "14212": (35.290, 139.482), "14213": (35.260, 139.159),
            "14214": (35.325, 139.275), "14215": (35.538, 139.362),
            "14216": (35.406, 139.388), "14217": (35.394, 139.453),
            "14218": (35.485, 139.502), "14219": (35.499, 139.470),
            # ===== 埼玉県 =====
            "11101": (35.862, 139.586), "11102": (35.892, 139.650),
            "11103": (35.884, 139.633), "11104": (35.854, 139.609),
            "11105": (35.861, 139.610), "11106": (35.865, 139.654),
            "11107": (35.881, 139.570), "11108": (35.886, 139.600),
            "11109": (35.912, 139.630), "11110": (35.861, 139.561),
            "11201": (35.825, 139.723), "11202": (35.867, 139.749),
            "11203": (35.861, 139.481), "11204": (35.923, 139.494),
            "11205": (35.806, 139.537), "11206": (35.938, 139.658),
            "11207": (35.779, 139.673), "11208": (35.821, 139.660),
            "11209": (35.956, 139.589), "11210": (35.907, 139.530),
            "11211": (35.802, 139.606), "11212": (35.852, 139.693),
            "11214": (35.770, 139.575), "11215": (35.979, 139.469),
            "11216": (35.884, 139.462), "11217": (35.834, 139.582),
            "11218": (35.989, 139.546), "11219": (35.962, 139.396),
            "11221": (35.795, 139.487), "11222": (35.843, 139.386),
            "11223": (36.023, 139.540), "11224": (35.921, 139.455),
            "11225": (35.889, 139.398), "11227": (35.823, 139.526),
            "11228": (35.836, 139.477), "11230": (35.948, 139.540),
            "11231": (35.932, 139.704), "11232": (35.824, 139.445),
            "11233": (35.760, 139.690), "11234": (35.839, 139.405),
            "11235": (36.018, 139.610), "11237": (35.910, 139.724),
            "11238": (35.843, 139.520), "11239": (35.760, 139.616),
            "11240": (35.811, 139.564), "11241": (35.855, 139.541),
            "11243": (35.794, 139.548),
            # ===== 千葉県 =====
            "12101": (35.607, 140.106), "12102": (35.746, 140.003),
            "12103": (35.685, 139.978), "12104": (35.836, 140.023),
            "12106": (35.668, 140.038),
            "12203": (35.728, 139.911), "12204": (35.781, 139.890),
            "12205": (35.855, 139.888), "12206": (35.731, 139.860),
            "12207": (35.705, 139.993), "12208": (35.832, 139.970),
            "12210": (35.837, 139.928), "12211": (35.831, 139.870),
            "12212": (35.834, 140.102), "12213": (35.674, 139.901),
            "12215": (35.767, 139.968), "12216": (35.764, 139.937),
            "12217": (35.718, 139.939), "12218": (35.772, 140.031),
            "12219": (35.774, 140.073), "12220": (35.707, 140.063),
            "12221": (35.671, 140.048), "12222": (35.614, 139.909),
            "12224": (35.860, 139.987), "12225": (35.708, 140.142),
            "12226": (35.670, 140.105), "12227": (35.763, 140.108),
            "12228": (35.697, 140.173), "12230": (35.777, 140.163),
            "12231": (35.741, 139.987), "12232": (35.765, 139.862),
            "12233": (35.871, 139.846), "12234": (35.701, 139.881),
            "12236": (35.676, 139.870), "12237": (35.827, 139.848),
        }
        return centers

    # ===== 公示地価・基準地価 (XPT002) =====

    def get_official_land_prices(
        self,
        lat: float,
        lng: float,
        year: int = None,
        price_classification: str = "",
        zoom: int = 13,
    ) -> List[Dict]:
        """
        公示地価・基準地価ポイント取得 (XPT002)

        Args:
            lat, lng: 中心座標
            year: 対象年（デフォルト: 直近年）
            price_classification: "0"=公示地価, "1"=基準地価, ""=両方
            zoom: ズームレベル (13-15)

        Returns:
            地価ポイント情報リスト（properties抽出済み）
        """
        if not year:
            year = datetime.now().year - 1

        x, y = self._lat_lng_to_tile(lat, lng, zoom)
        params = {
            "response_format": "geojson",
            "z": str(zoom),
            "x": str(x),
            "y": str(y),
            "year": str(year),
        }
        if price_classification:
            params["priceClassification"] = price_classification

        data = self._request("XPT002", params, cache_hours=720)
        features = data.get("features", [])
        results = []
        for f in features:
            props = f.get("properties", {})
            geom = f.get("geometry", {})
            coords = geom.get("coordinates", [])
            if coords and len(coords) >= 2:
                props["_lng"] = coords[0]
                props["_lat"] = coords[1]
            results.append(props)
        return results

    def get_official_land_prices_bounds(
        self,
        south: float, west: float, north: float, east: float,
        year: int = None,
        zoom: int = 13,
    ) -> List[Dict]:
        """表示範囲(bounds)内の公示地価を複数タイルで取得"""
        if not year:
            year = datetime.now().year - 1

        tiles = self._bounds_to_tiles(south, west, north, east, zoom)
        # タイル数制限（API負荷防止）
        if len(tiles) > 30:
            tiles = tiles[:30]

        extra = {"z": str(zoom), "year": str(year)}
        features = self._multi_tile_request("XPT002", tiles, extra_params=extra)

        results = []
        for f in features:
            props = f.get("properties", {})
            geom = f.get("geometry", {})
            coords = geom.get("coordinates", [])
            if coords and len(coords) >= 2:
                props["_lng"] = coords[0]
                props["_lat"] = coords[1]
            results.append(props)
        return results

    def get_population_mesh_bounds(
        self,
        south: float, west: float, north: float, east: float,
        zoom: int = 13,
    ) -> List[Dict]:
        """表示範囲内の人口メッシュを複数タイルで取得"""
        tiles = self._bounds_to_tiles(south, west, north, east, zoom)
        if len(tiles) > 30:
            tiles = tiles[:30]

        extra = {"z": str(zoom)}
        features = self._multi_tile_request("XKT013", tiles, extra_params=extra)

        results = []
        for f in features:
            props = f.get("properties", {})
            props["_geometry"] = f.get("geometry")
            results.append(props)
        return results

    def get_facilities_bounds(
        self,
        south: float, west: float, north: float, east: float,
        endpoints: List[str] = None,
        zoom: int = 14,
    ) -> Dict[str, list]:
        """表示範囲内の施設を複数タイル・複数種別で取得"""
        endpoints = endpoints or ["XKT006", "XKT010", "XKT007"]
        tiles = self._bounds_to_tiles(south, west, north, east, zoom)
        if len(tiles) > 20:
            tiles = tiles[:20]

        results = {}
        for ep in endpoints:
            extra = {"z": str(zoom)}
            features = self._multi_tile_request(ep, tiles, extra_params=extra)
            items = []
            for f in features:
                props = f.get("properties", {})
                geom = f.get("geometry", {})
                coords = geom.get("coordinates", [])
                if coords:
                    flat = coords
                    while flat and isinstance(flat[0], list):
                        flat = flat[0]
                    if len(flat) >= 2:
                        props["_lng"] = flat[0]
                        props["_lat"] = flat[1]
                items.append(props)
            results[ep] = items
        return results

    def get_official_land_prices_for_area(
        self,
        prefecture_code: str,
        city_code: str = "",
        year: int = None,
    ) -> List[Dict]:
        """複数市区町村の公示地価を一括取得"""
        city_centers = self._get_city_centers(prefecture_code)
        if not year:
            year = datetime.now().year - 1

        results = []
        targets = {city_code: city_centers[city_code]} if city_code and city_code in city_centers else city_centers

        for code, center in targets.items():
            try:
                points = self.get_official_land_prices(center[0], center[1], year=year, zoom=13)
                for p in points:
                    p["city_code"] = code
                    p["prefecture_code"] = prefecture_code
                results.extend(points)
            except Exception:
                continue
        return results

    # ===== 人口データ =====

    def get_population_mesh(self, lat: float, lng: float, zoom: int = 13) -> List[Dict]:
        """
        将来推計人口 250mメッシュ (XKT013)

        Returns:
            メッシュ単位の人口データリスト（properties + geometry）
        """
        x, y = self._lat_lng_to_tile(lat, lng, zoom)
        data = self._request("XKT013", {
            "response_format": "geojson",
            "z": str(zoom),
            "x": str(x),
            "y": str(y),
        }, cache_hours=720)

        results = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            props["_geometry"] = f.get("geometry")
            results.append(props)
        return results

    def get_did_area(self, lat: float, lng: float, zoom: int = 13) -> List[Dict]:
        """
        人口集中地区 DID (XKT031)

        Returns:
            DID区域データリスト（人口・世帯数・面積含む）
        """
        x, y = self._lat_lng_to_tile(lat, lng, zoom)
        data = self._request("XKT031", {
            "response_format": "geojson",
            "z": str(zoom),
            "x": str(x),
            "y": str(y),
        }, cache_hours=720)

        results = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            props["_geometry"] = f.get("geometry")
            results.append(props)
        return results

    # ===== 周辺施設 =====

    def get_schools(self, lat: float, lng: float, zoom: int = 14) -> List[Dict]:
        """学校 (XKT006)"""
        return self._facility_request("XKT006", lat, lng, zoom)

    def get_childcare(self, lat: float, lng: float, zoom: int = 14) -> List[Dict]:
        """保育園・幼稚園 (XKT007)"""
        return self._facility_request("XKT007", lat, lng, zoom)

    def get_medical_facilities(self, lat: float, lng: float, zoom: int = 14) -> List[Dict]:
        """医療機関 (XKT010)"""
        return self._facility_request("XKT010", lat, lng, zoom)

    def get_station_ridership(self, lat: float, lng: float, zoom: int = 13) -> List[Dict]:
        """駅別乗降客数 (XKT015)"""
        return self._facility_request("XKT015", lat, lng, zoom)

    def get_evacuation_shelters(self, lat: float, lng: float, zoom: int = 14) -> List[Dict]:
        """避難施設 (XGT001)"""
        return self._facility_request("XGT001", lat, lng, zoom)

    def _facility_request(self, endpoint: str, lat: float, lng: float, zoom: int) -> List[Dict]:
        """施設系API共通メソッド - GeoJSON featuresからproperties+座標を抽出"""
        x, y = self._lat_lng_to_tile(lat, lng, zoom)
        data = self._request(endpoint, {
            "response_format": "geojson",
            "z": str(zoom),
            "x": str(x),
            "y": str(y),
        }, cache_hours=720)

        results = []
        for f in data.get("features", []):
            props = f.get("properties", {})
            geom = f.get("geometry", {})
            coords = geom.get("coordinates", [])
            if coords:
                flat = coords
                while flat and isinstance(flat[0], list):
                    flat = flat[0]
                if len(flat) >= 2:
                    props["_lng"] = flat[0]
                    props["_lat"] = flat[1]
            results.append(props)
        return results

    # ===== 都市計画・災害リスク =====

    # ===== タイル座標ベースの都市計画・災害API =====

    def _tile_request(self, endpoint: str, lat: float, lng: float, zoom: int = 15) -> Dict:
        """タイル座標ベースのAPI呼び出し共通メソッド"""
        x, y = self._lat_lng_to_tile(lat, lng, zoom)
        return self._request(endpoint, {
            "response_format": "geojson",
            "z": str(zoom),
            "x": str(x),
            "y": str(y),
        }, cache_hours=720)

    def get_zoning_info(self, lat: float, lng: float) -> Dict:
        """都市計画情報取得 (XKT001) - 用途地域等"""
        return self._tile_request("XKT001", lat, lng)

    def get_land_use_district(self, lat: float, lng: float) -> Dict:
        """用途地域詳細 (XKT002)"""
        return self._tile_request("XKT002", lat, lng)

    def get_fire_prevention_area(self, lat: float, lng: float) -> Dict:
        """防火地域・準防火地域 (XKT014)"""
        return self._tile_request("XKT014", lat, lng)

    def get_flood_risk(self, lat: float, lng: float) -> Dict:
        """洪水浸水想定区域 (XKT016)"""
        return self._tile_request("XKT016", lat, lng)

    def get_landslide_risk(self, lat: float, lng: float) -> Dict:
        """土砂災害警戒区域 (XKT022)"""
        return self._tile_request("XKT022", lat, lng)

    @staticmethod
    def _find_nearest_feature(features: list, lat: float, lng: float) -> Optional[Dict]:
        """GeoJSON featuresから座標に最も近いfeatureのpropertiesを返す"""
        if not features:
            return None
        # 単純に最初のfeatureを返す（タイル内の代表値）
        # 複数ある場合はポイントとの距離で選別
        best = None
        best_dist = float("inf")
        for f in features:
            props = f.get("properties", {})
            geom = f.get("geometry", {})
            coords = geom.get("coordinates", [])
            if not coords:
                if best is None:
                    best = props
                continue
            # Polygon/MultiPolygonの場合、centroidを概算
            flat = coords
            while flat and isinstance(flat[0], list):
                flat = flat[0]
            if len(flat) >= 2:
                clng, clat = flat[0], flat[1]
                dist = (clat - lat) ** 2 + (clng - lng) ** 2
                if dist < best_dist:
                    best_dist = dist
                    best = props
        return best if best else (features[0].get("properties", {}) if features else None)

    def enrich_land_listing(self, lat: float, lng: float) -> Dict:
        """座標から用途地域・防火・災害リスクを一括取得（GeoJSONタイルベース）"""
        result = {}

        # 用途地域 (XKT002)
        try:
            data = self.get_land_use_district(lat, lng)
            features = data.get("features", [])
            props = self._find_nearest_feature(features, lat, lng)
            if props:
                # XKT002: use_area_ja が用途地域名
                zoning_name = (props.get("use_area_ja")
                               or props.get("用途地域名")
                               or props.get("land_use_ja", ""))
                result["zoning"] = zoning_name

                # 建蔽率: u_building_coverage_ratio_ja (例: "60%")
                # 容積率: u_floor_area_ratio_ja (例: "200%")
                for key, val in props.items():
                    kl = key.lower()
                    if ("coverage" in kl or "建蔽" in key or "kenpei" in kl) and val:
                        result["building_coverage_ratio"] = val
                    if ("floor_area" in kl or "容積" in key or "youseki" in kl) and val:
                        result["floor_area_ratio"] = val
        except Exception:
            pass

        # 防火地域 (XKT014)
        try:
            data = self.get_fire_prevention_area(lat, lng)
            features = data.get("features", [])
            props = self._find_nearest_feature(features, lat, lng)
            if props:
                # fire_prevention_jaキーを探索
                zone_name = ""
                for key, val in props.items():
                    if "fire_prevention" in key.lower() and "_ja" in key.lower():
                        zone_name = str(val)
                        break
                    if "防火" in key:
                        zone_name = str(val)
                        break
                if not zone_name:
                    zone_name = str(props)
                result["fire_prevention"] = zone_name
                result["quasi_fireproof"] = "準防火" in zone_name
                result["fire_zone"] = "防火" in zone_name and "準" not in zone_name
        except Exception:
            pass

        # 洪水浸水想定区域
        try:
            data = self.get_flood_risk(lat, lng)
            features = data.get("features", [])
            if features:
                result["flood_risk"] = True
                props = self._find_nearest_feature(features, lat, lng)
                result["flood_depth"] = str(props) if props else ""
            else:
                result["flood_risk"] = False
        except Exception:
            pass

        return result

    # ===== ユーティリティ =====

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def test_connection(self) -> bool:
        try:
            cities = self.get_cities("13")
            return len(cities) > 0
        except Exception as e:
            print(f"API接続エラー: {e}")
            return False
