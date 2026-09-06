"""地図レイヤ契約（geometry + grade + quality）"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

from .palette import GRADE_PALETTE, normalize_grade, grade_color


@dataclass
class MapGeometryDTO:
    """GeoJSON Point 相当"""
    latitude: float
    longitude: float
    estimated: bool = False
    geo_quality: str = "unknown"

    def to_geojson_geometry(self) -> dict:
        return {
            "type": "Point",
            "coordinates": [float(self.longitude), float(self.latitude)],
        }

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MapFeatureDTO:
    """地図マーカー用 Feature（投資グレード色は GRADE_PALETTE 固定）"""
    property_id: str
    name: str
    geometry: MapGeometryDTO
    grade: str = ""
    score: float = 0.0
    recommendation: str = ""
    asset_grade: str = ""
    listing_kind: str = "property"
    color: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.grade = normalize_grade(self.grade) or self.grade
        if not self.color:
            self.color = grade_color(self.grade)

    def to_geojson_feature(self) -> dict:
        props = {
            "id": self.property_id,
            "name": self.name,
            "grade": self.grade,
            "score": self.score,
            "recommendation": self.recommendation,
            "asset_grade": self.asset_grade,
            "listing_kind": self.listing_kind,
            "color": self.color or GRADE_PALETTE.get(self.grade, "#999999"),
            "coords_estimated": self.geometry.estimated,
            "geo_quality": self.geometry.geo_quality,
            **self.properties,
        }
        return {
            "type": "Feature",
            "geometry": self.geometry.to_geojson_geometry(),
            "properties": props,
        }

    def to_dict(self) -> dict:
        return {
            "property_id": self.property_id,
            "name": self.name,
            "geometry": self.geometry.to_dict(),
            "grade": self.grade,
            "score": self.score,
            "recommendation": self.recommendation,
            "asset_grade": self.asset_grade,
            "listing_kind": self.listing_kind,
            "color": self.color,
            "properties": self.properties,
        }

    @classmethod
    def from_listing_row(cls, row: dict) -> Optional["MapFeatureDTO"]:
        lat, lng = row.get("latitude"), row.get("longitude")
        if lat is None or lng is None:
            return None
        grade = normalize_grade(
            row.get("_analysis_grade") or row.get("grade") or row.get("judge_grade")
        )
        return cls(
            property_id=str(row.get("id") or ""),
            name=str(row.get("name") or row.get("address") or "物件"),
            geometry=MapGeometryDTO(
                latitude=float(lat),
                longitude=float(lng),
                estimated=bool(row.get("_coords_estimated")),
                geo_quality=str(row.get("_geo_quality") or "unknown"),
            ),
            grade=grade,
            score=float(row.get("_analysis_score") or row.get("score") or 0.0),
            recommendation=str(row.get("_analysis_recommendation") or row.get("recommendation") or ""),
            asset_grade=normalize_grade(row.get("asset_grade")) or str(row.get("asset_grade") or ""),
            listing_kind=str(row.get("_type") or row.get("listing_kind") or "property"),
            properties={
                "address": row.get("address"),
                "asking_price": row.get("asking_price"),
                "nearest_station": row.get("nearest_station"),
            },
        )
