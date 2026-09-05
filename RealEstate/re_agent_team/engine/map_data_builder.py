"""地図表示用データビルダー"""
import json
from typing import List, Dict, Optional

from models.land_price import LandPrice, TransactionRecord, AreaLandPriceSummary
from models.property import Property
from models.judgment import JudgmentResult
from contracts import GRADE_PALETTE, grade_color


class MapDataBuilder:
    """Leaflet地図に表示するGeoJSONデータを構築"""

    # 投資グレード色は contracts.GRADE_PALETTE が単一ソース
    GRADE_COLORS = GRADE_PALETTE

    PRICE_HEATMAP_COLORS = [
        (100000, "#313695"),     # ≤10万/㎡ 青
        (300000, "#4575b4"),
        (500000, "#74add1"),
        (800000, "#abd9e9"),
        (1000000, "#fee090"),    # 100万/㎡ 黄
        (1500000, "#fdae61"),
        (2000000, "#f46d43"),
        (3000000, "#d73027"),
        (5000000, "#a50026"),    # ≥500万/㎡ 赤
    ]

    def build_land_prices_geojson(
        self, land_prices: List[LandPrice]
    ) -> dict:
        """公示地価ポイントのGeoJSON"""
        features = []
        for lp in land_prices:
            if not lp.latitude or not lp.longitude:
                continue
            color = self._price_to_color(lp.price_per_sqm)
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lp.longitude, lp.latitude],
                },
                "properties": {
                    "address": lp.address,
                    "price_per_sqm": lp.price_per_sqm,
                    "price_label": f"¥{lp.price_per_sqm:,}/㎡",
                    "year": lp.year,
                    "type": lp.price_type,
                    "use_zone": lp.land_use_zone or "",
                    "station": lp.nearest_station or "",
                    "change_rate": lp.price_change_rate,
                    "color": color,
                    "layer": "land_price",
                },
            })

        return {"type": "FeatureCollection", "features": features}

    def build_transactions_geojson(
        self, transactions: List[TransactionRecord]
    ) -> dict:
        """取引事例のGeoJSON"""
        features = []
        for tx in transactions:
            if not tx.latitude or not tx.longitude:
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [tx.longitude, tx.latitude],
                },
                "properties": {
                    "address": tx.address,
                    "price": tx.transaction_price,
                    "price_label": f"¥{tx.transaction_price:,}",
                    "price_per_sqm": tx.price_per_sqm,
                    "date": tx.transaction_date,
                    "type": tx.property_type,
                    "use": tx.use or "",
                    "area": tx.land_area,
                    "layer": "transaction",
                },
            })

        return {"type": "FeatureCollection", "features": features}

    def build_property_judgment_geojson(
        self,
        properties: List[Property],
        judgments: List[JudgmentResult],
    ) -> dict:
        """物件＋判定結果のGeoJSON"""
        judgment_map = {j.property_id: j for j in judgments}
        features = []

        for prop in properties:
            if not prop.latitude or not prop.longitude:
                continue
            j = judgment_map.get(prop.id)
            grade = j.grade if j else "?"
            color = grade_color(grade, fallback="#999999")

            properties_dict = {
                "name": prop.name,
                "address": prop.address,
                "price": prop.asking_price,
                "price_label": f"¥{prop.asking_price:,}" if prop.asking_price else "",
                "grade": grade,
                "recommendation": j.recommendation if j else "",
                "score": j.overall_score if j else 0,
                "yield": j.key_metrics.get("表面利回り", "") if j else "",
                "land_ratio": j.key_metrics.get("土地値比率", "") if j else "",
                "color": color,
                "layer": "property",
            }

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [prop.longitude, prop.latitude],
                },
                "properties": properties_dict,
            })

        return {"type": "FeatureCollection", "features": features}

    def _price_to_color(self, price_per_sqm: int) -> str:
        for threshold, color in self.PRICE_HEATMAP_COLORS:
            if price_per_sqm <= threshold:
                return color
        return self.PRICE_HEATMAP_COLORS[-1][1]
