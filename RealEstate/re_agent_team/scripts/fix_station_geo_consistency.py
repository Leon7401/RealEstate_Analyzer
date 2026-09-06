import math
import sqlite3
import sys
from typing import Optional, Tuple

sys.path.insert(0, r"C:\Users\leons\OneDrive\Project\RealEstate\re_agent_team")

from data.geocoder import Geocoder
from data.station_master import STATION_MAP, find_nearest_station, resolve_station_id


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def pref_from_address(address: str) -> str:
    s = str(address or "")
    if "東京都" in s:
        return "13"
    if "神奈川県" in s:
        return "14"
    if "埼玉県" in s:
        return "11"
    if "千葉県" in s:
        return "12"
    return ""


def geocode_cached(geocoder: Geocoder, cache: dict, address: str) -> Optional[Tuple[float, float]]:
    if not address:
        return None
    if address in cache:
        return cache[address]
    try:
        cache[address] = geocoder.geocode(address)
    except Exception:
        cache[address] = None
    return cache[address]


def run() -> None:
    db_path = r"C:\Users\leons\OneDrive\Project\RealEstate\re_agent_team\output\realestate.db"
    conn = sqlite3.connect(db_path, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")

    rows = conn.execute(
        """
        SELECT id, name, address, prefecture_code, nearest_station, station_id, station_distance_min, latitude, longitude
        FROM properties
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
        ORDER BY updated_at DESC
        """
    ).fetchall()

    geocoder = Geocoder()
    geo_cache = {}
    changed = 0

    for row in rows:
        r = dict(row)
        rid = r["id"]
        pref = str(r.get("prefecture_code") or "") or pref_from_address(r.get("address", ""))
        lat = float(r["latitude"])
        lon = float(r["longitude"])
        st_name = str(r.get("nearest_station") or "")
        sid = resolve_station_id(st_name, lat=lat, lon=lon, pref_code=pref or None)

        suspicious = False
        if sid and sid in STATION_MAP:
            s = STATION_MAP[sid]
            if pref and str(s.get("pref") or "") and str(s.get("pref")) != pref:
                suspicious = True
            d_station = haversine_km(lat, lon, float(s["lat"]), float(s["lon"]))
            walk = r.get("station_distance_min")
            try:
                walk = float(walk) if walk is not None else None
            except Exception:
                walk = None
            if walk and walk > 0:
                expected = max(0.08 * walk, 0.2)
                if d_station > max(2.0, expected * 4.0):
                    suspicious = True
            elif d_station > 8.0:
                suspicious = True
        else:
            suspicious = bool(st_name)

        gc = geocode_cached(geocoder, geo_cache, r.get("address", ""))
        if gc:
            glat, glon = float(gc[0]), float(gc[1])
            if haversine_km(lat, lon, glat, glon) > 8.0:
                lat, lon = glat, glon
                suspicious = True

        if not suspicious:
            continue

        near = find_nearest_station(lat, lon, max_distance_km=8.0, pref_code=pref or None)
        if (not near) or float(near.get("distance_km") or 999.0) > 20.0:
            near_any = find_nearest_station(lat, lon, max_distance_km=8.0, pref_code=None)
            if near_any:
                near = near_any
        if not near:
            continue

        sid2 = near.get("station_id")
        sname2 = near.get("name")
        dkm2 = float(near.get("distance_km") or 0.0)
        walk2 = max(1, min(120, int(round(dkm2 * 12.5)))) if dkm2 > 0 else r.get("station_distance_min")

        conn.execute(
            """
            UPDATE properties
            SET station_id=?, nearest_station=?, station_distance_min=?, latitude=?, longitude=?, updated_at=datetime('now','localtime')
            WHERE id=?
            """,
            (sid2, sname2, walk2, lat, lon, rid),
        )
        changed += 1

    conn.commit()
    print({"checked": len(rows), "updated": changed})


if __name__ == "__main__":
    run()
