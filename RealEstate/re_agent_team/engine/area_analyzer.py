"""
エリア歪み分析エンジン（駅単位）
- 地価が安いのに賃料が高い駅（＝投資妙味の高い歪み）を検出
- 駅単位での比較分析
- バブルマップ・ランキング用データ生成
"""
import statistics
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

from storage.database import Database
from data.city_master import CITY_NAME_MAP
from data.station_master import STATION_MAP, STATION_NAME_MAP

logger = logging.getLogger("AreaAnalyzer")


@dataclass
class StationDistortion:
    """駅単位の歪み分析結果"""
    station_id: str
    station_name: str
    line_name: str
    city_code: str
    city_name: str
    # 地価
    avg_land_price: float
    land_price_rank: int
    land_price_percentile: float
    # 賃料
    avg_rent: float
    rent_rank: int
    rent_percentile: float
    # 利回り・歪み
    implied_cap_rate: float
    cap_rate_rank: int
    distortion_score: float
    distortion_rank: int
    # 地価変動
    land_change_rate: Optional[float]
    # データ量
    data_quality: str
    sample_land: int
    sample_rent: int
    # 座標
    center_lat: Optional[float]
    center_lng: Optional[float]
    # 近隣比較
    nearby_comparison: str


# レガシー互換
AreaDistortion = StationDistortion


class AreaAnalyzer:
    """駅間の歪みを分析するエンジン"""

    def __init__(self):
        self.db = Database()

    def analyze_all_areas(
        self, prefecture_code: str = "13"
    ) -> List[StationDistortion]:
        """
        全駅の歪み分析を実行

        Returns:
            歪みスコア降順のStationDistortionリスト
        """
        logger.info(f"駅単位歪み分析開始: pref={prefecture_code}")

        metrics = self.db.get_station_metrics(prefecture_code=prefecture_code)
        if not metrics:
            logger.warning("駅メトリクスが空です。先にバッチ処理を実行してください。")
            return []

        # 有効データのみ（地価と賃料の両方がある駅）
        valid = [
            m for m in metrics
            if m["avg_land_price_sqm"] and m["avg_land_price_sqm"] > 0
            and m["avg_rent_per_sqm"] and m["avg_rent_per_sqm"] > 0
        ]

        if not valid:
            logger.warning("有効な駅データがありません")
            return []

        # 全体統計
        all_lp = [m["avg_land_price_sqm"] for m in valid]
        all_rent = [m["avg_rent_per_sqm"] for m in valid]
        all_cap = [m["implied_yield"] for m in valid if m["implied_yield"]]

        avg_cap = statistics.mean(all_cap) if all_cap else 0
        lp_mean = statistics.mean(all_lp)
        lp_std = statistics.stdev(all_lp) if len(all_lp) > 1 else 1
        rent_mean = statistics.mean(all_rent)
        rent_std = statistics.stdev(all_rent) if len(all_rent) > 1 else 1

        # 地価の安さ順にソート
        sorted_by_lp = sorted(valid, key=lambda m: m["avg_land_price_sqm"])
        lp_rank_map = {m["station_id"]: i + 1 for i, m in enumerate(sorted_by_lp)}

        # 賃料の高さ順にソート
        sorted_by_rent = sorted(valid, key=lambda m: m["avg_rent_per_sqm"], reverse=True)
        rent_rank_map = {m["station_id"]: i + 1 for i, m in enumerate(sorted_by_rent)}

        # Cap Rate順
        sorted_by_cap = sorted(valid, key=lambda m: m.get("implied_yield", 0), reverse=True)
        cap_rank_map = {m["station_id"]: i + 1 for i, m in enumerate(sorted_by_cap)}

        results = []
        for m in valid:
            sid = m["station_id"]
            lp = m["avg_land_price_sqm"]
            rent = m["avg_rent_per_sqm"]
            cap = m.get("implied_yield", 0)

            # 歪みスコア計算
            rent_z = (rent - rent_mean) / rent_std if rent_std > 0 else 0
            lp_z = (lp - lp_mean) / lp_std if lp_std > 0 else 0
            raw_distortion = rent_z - lp_z

            distortion_score = max(0, min(100, 50 + raw_distortion * 20))

            # パーセンタイル
            lp_pct = sum(1 for x in all_lp if x <= lp) / len(all_lp) * 100
            rent_pct = sum(1 for x in all_rent if x <= rent) / len(all_rent) * 100

            # データ品質
            total_samples = m.get("sample_count_land", 0) + m.get("sample_count_rent", 0)
            if total_samples >= 10:
                quality = "high"
            elif total_samples >= 3:
                quality = "medium"
            else:
                quality = "low"

            # 近隣比較
            cap_diff = cap - avg_cap if avg_cap > 0 else 0
            if cap_diff > 0.02:
                comparison = "割安"
            elif cap_diff > 0.005:
                comparison = "やや割安"
            elif cap_diff > -0.005:
                comparison = "適正"
            elif cap_diff > -0.02:
                comparison = "やや割高"
            else:
                comparison = "割高"

            results.append(StationDistortion(
                station_id=sid,
                station_name=m.get("station_name", STATION_NAME_MAP.get(sid, "")),
                line_name=m.get("line_name", ""),
                city_code=m.get("city_code", ""),
                city_name=CITY_NAME_MAP.get(m.get("city_code", ""), ""),
                avg_land_price=lp,
                land_price_rank=lp_rank_map.get(sid, 0),
                land_price_percentile=lp_pct,
                avg_rent=rent,
                rent_rank=rent_rank_map.get(sid, 0),
                rent_percentile=rent_pct,
                implied_cap_rate=cap,
                cap_rate_rank=cap_rank_map.get(sid, 0),
                distortion_score=distortion_score,
                distortion_rank=0,
                land_change_rate=m.get("land_price_change_rate"),
                data_quality=quality,
                sample_land=m.get("sample_count_land", 0),
                sample_rent=m.get("sample_count_rent", 0),
                center_lat=m.get("center_lat"),
                center_lng=m.get("center_lng"),
                nearby_comparison=comparison,
            ))

        # 歪みスコア順にランク付け
        results.sort(key=lambda r: r.distortion_score, reverse=True)
        for i, r in enumerate(results):
            r.distortion_rank = i + 1

        if results:
            logger.info(
                f"歪み分析完了: {len(results)}駅, "
                f"TOP={results[0].station_name}(score={results[0].distortion_score:.1f})"
            )
        else:
            logger.info("歪み分析完了: 0駅")

        return results

    def get_area_detail(self, station_id: str) -> Dict:
        """
        駅詳細データ（地図ポップアップ用）
        """
        land_prices = self.db.get_land_prices(station_id=station_id, limit=200)
        rental_comps = self.db.get_rental_comps(station_id=station_id, limit=200)
        transactions = self.db.get_transactions(station_id=station_id, limit=200)

        station = STATION_MAP.get(station_id, {})
        station_name = station.get("name", station_id)
        city_code = station.get("city_code", "")

        # 構造別賃料集計
        structure_stats = {}
        for r in rental_comps:
            st = r.get("structure") or "不明"
            structure_stats.setdefault(st, []).append(r["rent_per_sqm"])

        structure_summary = []
        for st, rents in structure_stats.items():
            structure_summary.append({
                "structure": st,
                "avg_rent": round(statistics.mean(rents), 0),
                "count": len(rents),
            })
        structure_summary.sort(key=lambda x: x["avg_rent"], reverse=True)

        # 地価ポイント
        lp_points = [
            {
                "lat": r["latitude"],
                "lng": r["longitude"],
                "price": r["price_per_sqm"],
                "address": r["address"],
                "station": r.get("nearest_station", ""),
                "station_dist": r.get("station_distance_min"),
                "use_zone": r.get("land_use_zone", ""),
                "change_rate": r.get("price_change_rate"),
            }
            for r in land_prices
            if r.get("latitude") and r.get("longitude")
        ]

        return {
            "station_id": station_id,
            "station_name": station_name,
            "line_name": station.get("line", ""),
            "city_code": city_code,
            "city_name": CITY_NAME_MAP.get(city_code, ""),
            "land_price_points": lp_points,
            "structure_summary": structure_summary,
            "rental_count": len(rental_comps),
            "transaction_count": len(transactions),
            "land_price_count": len(land_prices),
        }

    def build_distortion_geojson(
        self, results: List[StationDistortion]
    ) -> Dict:
        """歪み分析結果をGeoJSON化"""
        features = []
        for r in results:
            if not r.center_lat or not r.center_lng:
                continue

            # 歪みスコアに応じた色
            if r.distortion_score >= 65:
                color = "#1a9641"
            elif r.distortion_score >= 55:
                color = "#66bb6a"
            elif r.distortion_score >= 45:
                color = "#fdd835"
            elif r.distortion_score >= 35:
                color = "#ff9800"
            else:
                color = "#e53935"

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [r.center_lng, r.center_lat],
                },
                "properties": {
                    "station_id": r.station_id,
                    "station_name": r.station_name,
                    "line_name": r.line_name,
                    "city_code": r.city_code,
                    "city_name": r.city_name,
                    "distortion_score": round(r.distortion_score, 1),
                    "distortion_rank": r.distortion_rank,
                    "implied_cap_rate": round(r.implied_cap_rate * 100, 2),
                    "avg_land_price": round(r.avg_land_price),
                    "avg_rent": round(r.avg_rent),
                    "land_price_rank": r.land_price_rank,
                    "rent_rank": r.rent_rank,
                    "land_change_rate": r.land_change_rate,
                    "comparison": r.nearby_comparison,
                    "data_quality": r.data_quality,
                    "color": color,
                    "radius": max(8, min(22, r.distortion_score * 0.35)),
                },
            })

        return {"type": "FeatureCollection", "features": features}
