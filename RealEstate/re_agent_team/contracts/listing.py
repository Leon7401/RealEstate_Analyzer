"""正規化物件DTO（土地/建物共通）"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional
from datetime import datetime
import uuid


@dataclass
class QualityFlags:
    """取得・座標品質フラグ"""
    parsed_ok: bool = True
    ocr_used: bool = False
    geo_quality: str = "unknown"  # exact | geocode | station | city_center | unknown
    coords_estimated: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "QualityFlags":
        d = d or {}
        return cls(
            parsed_ok=bool(d.get("parsed_ok", True)),
            ocr_used=bool(d.get("ocr_used", False)),
            geo_quality=str(d.get("geo_quality") or "unknown"),
            coords_estimated=bool(d.get("coords_estimated", False)),
        )


@dataclass
class ListingDTO:
    """
    土地/建物共通の正規化スキーマ。
    必須: 住所・価格・延床(or土地面積)・駅距離・source_url・都道府県
    """
    address: str
    asking_price: Optional[int]
    prefecture_code: str
    source_url: Optional[str]

    name: str = ""
    city_code: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    land_area: Optional[float] = None
    building_area: Optional[float] = None  # 延床
    station_distance_min: Optional[int] = None
    nearest_station: Optional[str] = None

    structure: Optional[str] = None
    floors: Optional[int] = None
    built_year: Optional[int] = None
    gross_yield: Optional[float] = None
    current_rent_annual: Optional[int] = None

    source: Optional[str] = None
    listing_kind: str = "property"  # property | land

    quality: QualityFlags = field(default_factory=QualityFlags)
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["quality"] = self.quality.to_dict()
        return d

    def to_property_dict(self) -> dict:
        """既存 Property / DB upsert 互換の平坦 dict"""
        q = self.quality
        return {
            "id": self.id,
            "name": self.name or self.address,
            "address": self.address,
            "prefecture_code": self.prefecture_code,
            "city_code": self.city_code,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "asking_price": self.asking_price,
            "land_area": self.land_area,
            "building_area": self.building_area,
            "structure": self.structure,
            "floors": self.floors,
            "built_year": self.built_year,
            "gross_yield": self.gross_yield,
            "current_rent_annual": self.current_rent_annual,
            "nearest_station": self.nearest_station,
            "station_distance_min": self.station_distance_min,
            "source": self.source,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "_coords_estimated": q.coords_estimated,
            "_geo_quality": q.geo_quality,
            "_parsed_ok": q.parsed_ok,
            "_ocr_used": q.ocr_used,
            **self.extras,
        }

    @classmethod
    def from_property_dict(cls, d: dict) -> "ListingDTO":
        quality = QualityFlags(
            parsed_ok=bool(d.get("_parsed_ok", d.get("parsed_ok", True))),
            ocr_used=bool(d.get("_ocr_used", d.get("ocr_used", False))),
            geo_quality=str(d.get("_geo_quality") or d.get("geo_quality") or "unknown"),
            coords_estimated=bool(d.get("_coords_estimated", False)),
        )
        known = {
            "address", "asking_price", "prefecture_code", "source_url", "name",
            "city_code", "latitude", "longitude", "land_area", "building_area",
            "station_distance_min", "nearest_station", "structure", "floors",
            "built_year", "gross_yield", "current_rent_annual", "source",
            "listing_kind", "id", "fetched_at",
        }
        extras = {
            k: v for k, v in d.items()
            if k not in known and not k.startswith("_") and k not in {
                "parsed_ok", "ocr_used", "geo_quality", "quality", "data_json",
            }
        }
        return cls(
            address=str(d.get("address") or ""),
            asking_price=d.get("asking_price"),
            prefecture_code=str(d.get("prefecture_code") or ""),
            source_url=d.get("source_url"),
            name=str(d.get("name") or ""),
            city_code=str(d.get("city_code") or ""),
            latitude=d.get("latitude"),
            longitude=d.get("longitude"),
            land_area=d.get("land_area"),
            building_area=d.get("building_area"),
            station_distance_min=d.get("station_distance_min"),
            nearest_station=d.get("nearest_station"),
            structure=d.get("structure"),
            floors=d.get("floors"),
            built_year=d.get("built_year"),
            gross_yield=d.get("gross_yield"),
            current_rent_annual=d.get("current_rent_annual"),
            source=d.get("source"),
            listing_kind=str(d.get("listing_kind") or "property"),
            quality=quality,
            id=str(d.get("id") or str(uuid.uuid4())[:8]),
            fetched_at=str(d.get("fetched_at") or datetime.now().isoformat()),
            extras=extras,
        )

    def required_fields_ok(self) -> bool:
        has_area = (self.building_area or 0) > 0 or (self.land_area or 0) > 0
        return bool(
            self.address
            and self.asking_price
            and self.prefecture_code
            and self.source_url
            and has_area
            and self.station_distance_min is not None
        )


QualityFlags = QualityFlags  # contracts.__init__ 互換

ListingDTO = ListingDTO
