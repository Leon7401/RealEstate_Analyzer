"""賃料データモデル"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List
import json


@dataclass
class RentalComp:
    """賃貸比較事例"""
    address: str
    rent_monthly: int                       # 月額賃料（円）
    area_sqm: float                         # 専有面積（㎡）
    rent_per_sqm: float                     # ㎡賃料（円/㎡）

    # 物件詳細
    layout: Optional[str] = None            # 間取り（1K, 2LDK等）
    structure: Optional[str] = None         # 構造
    built_year: Optional[int] = None        # 築年
    floor: Optional[int] = None             # 階数
    floors_total: Optional[int] = None      # 総階数

    # 費用
    management_fee: Optional[int] = None    # 管理費（円/月）
    deposit_months: Optional[float] = None  # 敷金（ヶ月）
    key_money_months: Optional[float] = None  # 礼金（ヶ月）

    # 位置
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    nearest_station: Optional[str] = None
    station_distance_min: Optional[int] = None

    # エリア
    city_code: Optional[str] = None

    source: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AreaRentalSummary:
    """エリア賃料サマリー"""
    city_code: str
    city_name: str

    avg_rent_per_sqm: float                 # 平均㎡賃料
    median_rent_per_sqm: float
    min_rent_per_sqm: float
    max_rent_per_sqm: float
    sample_count: int

    # 構造別平均
    rent_by_structure: dict = field(default_factory=dict)    # {"RC": 3500, "木造": 2800}
    rent_by_age_range: dict = field(default_factory=dict)    # {"0-10年": 3200, "11-20年": 2800}

    comps: List[RentalComp] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["comps"] = [c.to_dict() for c in self.comps]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
