import math
import json
import sqlite3
from collections import defaultdict

from data.reinfolib_client import ReinfolibClient
from storage.database import Database


def _cell_key(lat: float, lng: float, step_lat: float = 0.08, step_lng: float = 0.12) -> tuple[int, int]:
    return int(math.floor(lat / step_lat)), int(math.floor(lng / step_lng))


def main():
    db = Database()
    client = ReinfolibClient()
    if not client.is_configured():
        print("API key is not configured. Set REINFOLIB_API_KEY first.")
        return

    with db._conn() as conn:
        # 人口欠損メッシュのみ対象（座標あり）
        missing = conn.execute(
            """
            SELECT mesh_id, center_lat, center_lng
            FROM mesh_250m
            WHERE pop_current IS NULL
              AND center_lat IS NOT NULL
              AND center_lng IS NOT NULL
            """
        ).fetchall()

    missing = [dict(r) for r in missing]
    print(f"missing meshes: {len(missing)}")
    if not missing:
        return

    # 粗いセルに束ねて、過大boundsによるtile切り捨て(>30)を回避
    grouped = defaultdict(list)
    for r in missing:
        grouped[_cell_key(r["center_lat"], r["center_lng"])].append(r)

    total_saved = 0
    total_features = 0
    processed_groups = 0

    # 欠損が多いセルから優先して収集
    groups = sorted(grouped.items(), key=lambda kv: len(kv[1]), reverse=True)
    for _, rows in groups:
        lats = [x["center_lat"] for x in rows]
        lngs = [x["center_lng"] for x in rows]
        south = min(lats) - 0.01
        north = max(lats) + 0.01
        west = min(lngs) - 0.015
        east = max(lngs) + 0.015

        try:
            meshes = client.get_population_mesh_bounds(south, west, north, east, zoom=13)
        except Exception as e:
            print(f"skip group error: {e}")
            continue

        if not meshes:
            processed_groups += 1
            continue

        db_records = []
        for m in meshes:
            geom = m.pop("_geometry", None)
            if not geom:
                continue
            pop_current = m.get("PTN_2020") or m.get("PTN_2025")
            pop_future = m.get("PTN_2050") or m.get("PTN_2045") or m.get("PTN_2040")
            if pop_current is None:
                continue
            change_rate = None
            if pop_future is not None and pop_current > 0:
                change_rate = round((pop_future - pop_current) / pop_current * 100, 1)
            db_records.append({
                "mesh_id": m.get("MESH_ID", ""),
                "pop_current": pop_current,
                "pop_future": pop_future,
                "change_rate": change_rate,
                "geometry_json": json.dumps(geom, ensure_ascii=False),
            })

        if db_records:
            saved = db.upsert_api_population_mesh(db_records)
            total_saved += saved
            total_features += len(db_records)
        processed_groups += 1
        print(f"group {processed_groups}/{len(groups)} -> fetched={len(meshes)} saved_total={total_saved}")

    # mesh_250mへ反映（既存集計に追従）
    with db._conn() as conn:
        conn.execute(
            """
            UPDATE mesh_250m
            SET pop_current = (
                SELECT a.pop_current FROM api_population_mesh a WHERE a.mesh_id = mesh_250m.mesh_id
            ),
                pop_future = (
                SELECT a.pop_future FROM api_population_mesh a WHERE a.mesh_id = mesh_250m.mesh_id
            ),
                pop_change_rate = (
                SELECT a.change_rate FROM api_population_mesh a WHERE a.mesh_id = mesh_250m.mesh_id
            )
            WHERE mesh_id IN (SELECT mesh_id FROM api_population_mesh)
            """
        )

        after_missing = conn.execute(
            "SELECT COUNT(*) FROM mesh_250m WHERE pop_current IS NULL"
        ).fetchone()[0]
        apm_count = conn.execute("SELECT COUNT(*) FROM api_population_mesh").fetchone()[0]

    print(f"done: groups={processed_groups}, records_seen={total_features}, upserted={total_saved}")
    print(f"remaining_missing_mesh_pop={after_missing}")
    print(f"api_population_mesh_total={apm_count}")


if __name__ == "__main__":
    main()
