"""物件データモデル"""
from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import uuid
from datetime import datetime


@dataclass
class Property:
    """売買物件情報"""
    # 基本情報
    name: str                               # 物件名
    address: str                            # 所在地
    prefecture_code: str                    # 都道府県コード
    city_code: str                          # 市区町村コード

    # 位置情報
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # 価格情報
    asking_price: Optional[int] = None      # 売出価格（円）
    price_per_sqm: Optional[float] = None   # ㎡単価（円/㎡）

    # 土地情報
    land_area: Optional[float] = None       # 土地面積（㎡）
    land_use_zone: Optional[str] = None     # 用途地域
    building_coverage: Optional[float] = None  # 建蔽率
    floor_area_ratio: Optional[float] = None   # 容積率
    road_frontage: Optional[str] = None     # 接道状況
    land_shape: Optional[str] = None        # 土地形状

    # 建物情報
    building_area: Optional[float] = None   # 建物面積（㎡）
    structure: Optional[str] = None         # 構造（RC, SRC, 木造等）
    floors: Optional[int] = None            # 階数
    units: Optional[int] = None             # 戸数（一棟の場合）
    built_year: Optional[int] = None        # 築年
    building_age: Optional[int] = None      # 築年数

    # 収益情報
    current_rent_annual: Optional[int] = None   # 現行年間賃料（円）
    gross_yield: Optional[float] = None         # 表面利回り
    occupancy_rate: Optional[float] = None      # 稼働率

    # 交通
    nearest_station: Optional[str] = None       # 最寄駅
    station_distance_min: Optional[int] = None  # 駅徒歩（分）
    station_id: Optional[str] = None            # 駅ID

    # メタデータ
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    source: Optional[str] = None            # データソース
    source_url: Optional[str] = None        # ソースURL
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "Property":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_fields})

    @property
    def total_area_label(self) -> str:
        parts = []
        if self.land_area:
            parts.append(f"土地{self.land_area:.1f}㎡")
        if self.building_area:
            parts.append(f"建物{self.building_area:.1f}㎡")
        return " / ".join(parts) if parts else "面積不明"
