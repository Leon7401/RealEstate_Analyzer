"""座標品質サービス — 住所ジオコード優先・都県境界検証・推定バッジ"""
from __future__ import annotations

import math
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from data.geocoder import Geocoder

logger = logging.getLogger(__name__)

PREF_BOUNDS: Dict[str, Tuple[float, float, float, float]] = {
    "13": (35.45, 35.92, 139.45, 139.95),
    "14": (35.10, 35.75, 139.10, 139.90),
    "11": (35.70, 36.35, 138.70, 139.95),
    "12": (34.90, 36.20, 139.70, 140.95),
}

WARD_CENTER: Dict[str, Tuple[float, float]] = {
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


def pref_from_address(addr: str) -> str:
    if not addr:
        return ""
    if "東京都" in addr:
        return "13"
    if "神奈川県" in addr:
        return "14"
    if "埼玉県" in addr:
        return "11"
    if "千葉県" in addr:
        return "12"
    return ""


def coord_in_pref_bounds(lat: float, lng: float, pref_code: str) -> bool:
    b = PREF_BOUNDS.get(pref_code)
    if not b:
        return True
    return b[0] <= lat <= b[1] and b[2] <= lng <= b[3]


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


class GeoQualityService:
    """表示座標の品質管理（住所ジオコード優先・都県不一致ゼロ目標）。"""

    def __init__(
        self,
        db=None,
        geocoder: Optional[Geocoder] = None,
        station_loader: Optional[Callable[[], List[dict]]] = None,
    ):
        self.db = db
        self.geocoder = geocoder or Geocoder()
        self._station_loader = station_loader
        self._station_cache: Optional[Dict[str, List[dict]]] = None
        self._geocode_cache: Dict[str, Optional[Tuple[float, float]]] = {}

    def _load_stations(self) -> Dict[str, List[dict]]:
        if self._station_cache is not None:
            return self._station_cache
        stations: List[dict] = []
        if self._station_loader:
            stations = self._station_loader() or []
        elif self.db is not None:
            stations = self.db.get_stations() or []
        station_map: Dict[str, List[dict]] = {}
        for s in stations:
            if not s.get("latitude") or not s.get("longitude"):
                continue
            key = str(s.get("station_name") or "").replace("駅", "").strip()
            if not key:
                continue
            station_map.setdefault(key, []).append({
                "lat": float(s["latitude"]),
                "lng": float(s["longitude"]),
                "pref": str(s.get("prefecture_code") or ""),
                "name": s.get("station_name"),
            })
        self._station_cache = station_map
        return station_map

    def pick_station_coord(self, name: str, pref_code: str) -> Optional[dict]:
        if not name:
            return None
        sname = name.replace("駅", "").strip()
        if not sname:
            return None
        station_coords = self._load_stations()
        cands = station_coords.get(sname, [])
        if cands:
            pref_hit = next((c for c in cands if pref_code and c.get("pref") == pref_code), None)
            if pref_hit:
                return pref_hit
            # 都県指定時は他県候補を採用しない（誤配置防止）
            return None if pref_code else cands[0]
        if len(sname) < 3:
            return None
        partial = []
        for key, vals in station_coords.items():
            if key.startswith(sname) or key.endswith(sname) or sname.startswith(key):
                for v in vals:
                    if pref_code and v.get("pref") != pref_code:
                        continue
                    partial.append(v)
        return partial[0] if partial else None

    def geocode_address(self, addr: str, budget: List[int]) -> Optional[Tuple[float, float]]:
        addr = (addr or "").strip()
        if not addr or len(addr) < 6:
            return None
        if addr in self._geocode_cache:
            return self._geocode_cache[addr]
        gc = None
        if budget[0] > 0:
            try:
                gc = self.geocoder.geocode(addr)
            except Exception as e:
                logger.debug("geocode failed for %s: %s", addr, e)
                gc = None
            budget[0] -= 1
        self._geocode_cache[addr] = gc
        return gc

    def enrich_properties(
        self,
        props: list,
        *,
        persist_updates: bool = True,
        geocode_budget: int = 120,
    ) -> dict:
        budget = [max(0, int(geocode_budget or 0))]
        updates: List[Tuple[float, float, str]] = []
        stats = {"checked": 0, "updated": 0, "estimated": 0, "corrected": 0}

        for p in props:
            stats["checked"] += 1
            pref_code = str(
                p.get("prefecture_code") or pref_from_address(p.get("address", "")) or ""
            )
            has_coords = bool(p.get("latitude") and p.get("longitude"))
            if has_coords:
                if self._coords_look_ok(p, pref_code, budget):
                    p.setdefault("_coords_estimated", False)
                    p.setdefault("_geo_quality", "exact")
                    continue
                stats["corrected"] += 1

            lat, lng, coord_source = self._resolve_coords(p, pref_code, budget)
            if lat is None:
                continue

            if coord_source == "geocode":
                p["latitude"] = round(lat, 6)
                p["longitude"] = round(lng, 6)
                p["_coords_estimated"] = False
                p["_geo_quality"] = "geocode"
            else:
                walk_min = p.get("station_distance_min") or 5
                try:
                    walk_min = float(walk_min)
                except (TypeError, ValueError):
                    walk_min = 5.0
                offset_km = walk_min * 0.08 / 111.0
                pid = hash(str(p.get("id", "") or p.get("name", "")))
                angle = (pid % 360) * math.pi / 180
                p["latitude"] = round(lat + offset_km * math.cos(angle), 6)
                p["longitude"] = round(
                    lng + offset_km * math.sin(angle) / max(0.1, math.cos(math.radians(lat))),
                    6,
                )
                p["_coords_estimated"] = True
                p["_geo_quality"] = coord_source or "station"
                stats["estimated"] += 1

            stats["updated"] += 1
            if p.get("id"):
                updates.append((p["latitude"], p["longitude"], str(p["id"])))

        if persist_updates and updates and self.db is not None:
            with self.db._conn() as conn:
                conn.executemany(
                    "UPDATE properties SET latitude=?, longitude=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    updates,
                )
        return stats

    def estimate_missing_coords(
        self,
        props: list,
        *,
        persist_updates: bool = True,
        geocode_budget: int = 120,
    ) -> dict:
        return self.enrich_properties(
            props,
            persist_updates=persist_updates,
            geocode_budget=geocode_budget,
        )

    def _coords_look_ok(self, p: dict, pref_code: str, budget: List[int]) -> bool:
        lat0 = float(p["latitude"])
        lng0 = float(p["longitude"])
        if not coord_in_pref_bounds(lat0, lng0, pref_code):
            return False

        station_name0 = str(p.get("nearest_station") or "")
        walk_min0 = p.get("station_distance_min")
        try:
            walk_min0 = float(walk_min0) if walk_min0 is not None else None
        except (TypeError, ValueError):
            walk_min0 = None
        st0 = self.pick_station_coord(station_name0, pref_code)
        if st0 and walk_min0 is not None and walk_min0 > 0:
            dkm0 = haversine_km(lat0, lng0, st0["lat"], st0["lng"])
            expected = max(0.08 * walk_min0, 0.2)
            if dkm0 <= max(1.5, expected * 4.0):
                return True
        elif st0 is None and not (p.get("address") or "").strip():
            return True

        addr0 = (p.get("address") or "").strip()
        if addr0 and len(addr0) >= 6:
            gc0 = self.geocode_address(addr0, budget)
            if gc0:
                glat0, glng0 = float(gc0[0]), float(gc0[1])
                if coord_in_pref_bounds(glat0, glng0, pref_code):
                    if haversine_km(lat0, lng0, glat0, glng0) <= 8.0:
                        return True
            return False
        return True

    def _resolve_coords(
        self, p: dict, pref_code: str, budget: List[int]
    ) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        addr = (p.get("address") or "").strip()
        if addr and len(addr) >= 6:
            gc = self.geocode_address(addr, budget)
            if gc:
                glat, glng = float(gc[0]), float(gc[1])
                if coord_in_pref_bounds(glat, glng, pref_code):
                    return glat, glng, "geocode"

        station_name = p.get("nearest_station") or ""
        for sep in ["/", "／", "線"]:
            if sep in station_name:
                station_name = station_name.split(sep)[-1]
        station_name = station_name.replace("駅", "").strip()
        st = self.pick_station_coord(station_name, pref_code)
        if st:
            return st["lat"], st["lng"], "station"

        city = str(p.get("city_code") or "")
        if city in WARD_CENTER:
            lat, lng = WARD_CENTER[city]
            return lat, lng, "city_center"

        return None, None, None

    def revalidate_all(
        self,
        *,
        limit: int = 5000,
        geocode_budget: int = 300,
        dry_run: bool = False,
        prefecture_codes: Optional[List[str]] = None,
    ) -> dict:
        if self.db is None:
            return {"error": "db not configured", "updated": 0}

        with self.db._conn() as conn:
            sql = "SELECT * FROM properties WHERE 1=1"
            params: List[Any] = []
            if prefecture_codes:
                marks = ",".join(["?"] * len(prefecture_codes))
                sql += f" AND prefecture_code IN ({marks})"
                params.extend(prefecture_codes)
            sql += " ORDER BY updated_at DESC LIMIT ?"
            params.append(int(limit))
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

        before = {
            r["id"]: (r.get("latitude"), r.get("longitude"))
            for r in rows if r.get("id")
        }
        stats = self.enrich_properties(
            rows,
            persist_updates=not dry_run,
            geocode_budget=geocode_budget,
        )
        changed = []
        pref_mismatch_fixed = 0
        for r in rows:
            pid = r.get("id")
            if not pid:
                continue
            old = before.get(pid)
            new = (r.get("latitude"), r.get("longitude"))
            if old != new and new[0] is not None:
                changed.append({
                    "id": pid,
                    "address": r.get("address"),
                    "old": {"lat": old[0], "lng": old[1]} if old else None,
                    "new": {"lat": new[0], "lng": new[1]},
                    "geo_quality": r.get("_geo_quality"),
                    "estimated": r.get("_coords_estimated"),
                })
                pref = str(
                    r.get("prefecture_code") or pref_from_address(r.get("address") or "")
                )
                if (
                    old and old[0] and old[1]
                    and not coord_in_pref_bounds(float(old[0]), float(old[1]), pref)
                ):
                    pref_mismatch_fixed += 1

        return {
            **stats,
            "scanned": len(rows),
            "changed": len(changed),
            "pref_mismatch_fixed": pref_mismatch_fixed,
            "dry_run": dry_run,
            "samples": changed[:30],
        }


# 互換エイリアス
GeoQualityService = GeoQualityService
