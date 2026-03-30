"""地価データモデル（公示地価・取引価格）"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List
import json
from datetime import datetime


@dataclass
class LandPrice:
    """公示地価・基準地価ポイント"""
    address: str
    price_per_sqm: int                      # ㎡単価（円/㎡）
    year: int = 0                           # 調査年
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # 詳細
    land_use_zone: Optional[str] = None     # 用途地域
    acreage: Optional[float] = None         # 地積（㎡）
    shape: Optional[str] = None             # 形状
    frontage: Optional[float] = None        # 間口（m）
    depth: Optional[float] = None           # 奥行（m）
    road_condition: Optional[str] = None    # 前面道路状況
    nearest_station: Optional[str] = None
    station_distance_min: Optional[int] = None

    # 変動
    price_change_rate: Optional[float] = None  # 前年比変動率

    price_type: str = "公示地価"            # 公示地価 / 基準地価
    prefecture_code: str = ""
    city_code: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TransactionRecord:
    """不動産取引価格情報（実績）"""
    address: str
    transaction_price: int                  # 取引価格（円）
    price_per_sqm: Optional[float] = None   # ㎡単価
    transaction_date: Optional[str] = None  # 取引時期 (例: "2024Q3")

    # 土地
    land_area: Optional[float] = None       # 面積（㎡）
    land_shape: Optional[str] = None
    land_use_zone: Optional[str] = None
    frontage: Optional[float] = None

    # 建物（建物付きの場合）
    building_area: Optional[float] = None
    structure: Optional[str] = None
    built_year: Optional[int] = None
    use: Optional[str] = None               # 用途（住宅、共同住宅、事務所等）

    # 位置
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    nearest_station: Optional[str] = None
    station_distance_min: Optional[int] = None

    # メタデータ
    property_type: str = "土地"             # 土地/中古マンション等/農地/林地
    prefecture_code: str = ""
    city_code: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AreaLandPriceSummary:
    """エリアの地価サマリー"""
    city_code: str
    city_name: str
    year: int

    avg_price_per_sqm: float                # 平均㎡単価
    median_price_per_sqm: float             # 中央値㎡単価
    min_price_per_sqm: float
    max_price_per_sqm: float
    sample_count: int                       # サンプル数

    avg_change_rate: Optional[float] = None # 平均変動率
    land_prices: List[LandPrice] = field(default_factory=list)
    transactions: List[TransactionRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["land_prices"] = [lp.to_dict() for lp in self.land_prices]
        d["transactions"] = [tr.to_dict() for tr in self.transactions]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
