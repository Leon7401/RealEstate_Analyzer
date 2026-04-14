import math
import sqlite3
import sys
from typing import Optional, Tuple

sys.path.insert(0, r"C:\Users\leons\OneDrive\Project\RealEstate\re_agent_team")

from data.geocoder import Geocoder
from data.station_master import STATION_MAP, find_nearest_station, resolve_station_id


def pref_from_address(address: str) -> str:
    s = str(address or "")
    if s.startswith("東京都"):
        return "13"
    if s.startswith("神奈川県"):
        return "14"
    if s.startswith("埼玉県"):
        return "11"
    if s.startswith("千葉県"):
        return "12"
    return ""


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def geocode_with_cache(geocoder: Geocoder, cache: dict, address: str) -> Optional[Tuple[float, float]]:
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
    db = r"C:\Users\leons\OneDrive\Project\RealEstate\re_agent_team\output\realestate.db"
    conn = sqlite3.connect(db, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")

    rows = conn.execute(
        """
        SELECT id, name, address, prefecture_code, nearest_station, station_id, latitude, longitude
        FROM properties
        WHERE (address LIKE '東京都%' OR address LIKE '神奈川県%' OR address LIKE '埼玉県%' OR address LIKE '千葉県%')
          AND prefecture_code <> CASE
              WHEN address LIKE '東京都%' THEN '13'
              WHEN address LIKE '神奈川県%' THEN '14'
              WHEN address LIKE '埼玉県%' THEN '11'
              WHEN address LIKE '千葉県%' THEN '12'
              ELSE prefecture_code
          END
        """
    ).fetchall()

    geocoder = Geocoder()
    geo_cache = {}
    updated = 0

    for row in rows:
        r = dict(row)
        pref = pref_from_address(r.get("address", ""))
        if not pref:
            continue

        sid = resolve_station_id(
            nearest_station_text=r.get("nearest_station"),
            lat=r.get("latitude"),
            lon=r.get("longitude"),
            pref_code=pref,
        )
        if not sid:
            sid = resolve_station_id(
                nearest_station_text=r.get("name"),
                lat=r.get("latitude"),
                lon=r.get("longitude"),
                pref_code=pref,
            )

        gc = geocode_with_cache(geocoder, geo_cache, r.get("address", ""))
        lat = r.get("latitude")
        lon = r.get("longitude")
        if gc:
            glat, glon = gc
            near = find_nearest_station(glat, glon, max_distance_km=3.0, pref_code=pref)
            if near:
                near_sid = near.get("station_id")
                if sid and sid in STATION_MAP:
                    s = STATION_MAP[sid]
                    if haversine_km(glat, glon, float(s["lat"]), float(s["lon"])) > 15.0:
                        sid = near_sid
                else:
                    sid = near_sid
            lat = glat
            lon = glon

        sname = STATION_MAP.get(sid, {}).get("name") if sid else None
        conn.execute(
            """
            UPDATE properties
            SET prefecture_code=?, station_id=?, nearest_station=?, latitude=?, longitude=?
            WHERE id=?
            """,
            (pref, sid, sname, lat, lon, r["id"]),
        )
        updated += 1

    conn.commit()
    print({"target_rows": len(rows), "updated_rows": updated})


if __name__ == "__main__":
    run()
