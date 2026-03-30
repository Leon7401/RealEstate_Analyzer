"""土地物件データモデル - SUUMO/楽待から取得する土地情報"""
from dataclasses import dataclass, field, asdict
from typing import Optional
import json
from datetime import datetime


@dataclass
class LandListing:
    """土地物件情報（新築用地）"""
    # 基本情報
    address: str                                # 住所
    railway_line: Optional[str] = None          # 路線
    station: Optional[str] = None               # 最寄駅
    walk_minutes: Optional[int] = None          # 徒歩分数

    # 価格・面積
    land_price: Optional[int] = None            # 土地価格（円）
    land_area_sqm: Optional[float] = None       # 土地面積（㎡）

    # 法規制
    building_coverage_ratio: Optional[float] = None  # 建蔽率（小数: 0.60）
    floor_area_ratio: Optional[float] = None         # 容積率（小数: 2.00）
    zoning: Optional[str] = None                     # 用途地域
    height_limit_m: Optional[float] = None           # 絶対高さ制限(m)
    height_district: Optional[str] = None            # 高度地区（1種/2種/3種）
    quasi_fireproof: bool = False                    # 準防火地域
    two_way_road: bool = False                       # 2方向道路
    north_road: bool = False                         # 北道路

    # 接道・地形
    road_width_m: Optional[float] = None             # 前面道路幅員(m)
    road_legal_type: Optional[str] = None            # 42条1項1号/42条2項/42条1項5号/私道
    frontage_m: Optional[float] = None               # 間口(m)
    depth_m: Optional[float] = None                  # 奥行(m)
    has_retaining_wall: bool = False                  # 擁壁あり（検討対象外）
    has_step_retaining_wall: bool = False             # 階段擁壁（完全NG）
    land_shape: Optional[str] = None                  # 整形地/不整形地/旗竿地
    setback_required: bool = False                    # セットバック必要
    setback_area_sqm: Optional[float] = None          # セットバック面積(㎡)
    corner_lot: bool = False                          # 角地

    # ソース情報
    source: Optional[str] = None                # "SUUMO" / "楽待" / "CSV"
    source_url: Optional[str] = None            # ソースURL
    maisoku_pdf_path: Optional[str] = None      # マイソクPDFパス
    analysis_status: str = "pending"            # pending / ok / error
    memo: Optional[str] = None

    # 位置情報
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # メタデータ
    id: Optional[int] = None                    # DB auto-increment
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "LandListing":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_fields})

    @property
    def price_label(self) -> str:
        if not self.land_price:
            return "価格不明"
        if self.land_price >= 100_000_000:
            oku = self.land_price / 100_000_000
            return f"{oku:.1f}億円"
        return f"{self.land_price // 10_000:,}万円"

    @property
    def effective_land_area(self) -> Optional[float]:
        """有効敷地面積（セットバック面積を差し引いた面積）"""
        if not self.land_area_sqm:
            return None
        return self.land_area_sqm - (self.setback_area_sqm or 0)

    @property
    def effective_coverage(self) -> Optional[float]:
        """有効建蔽率（角地緩和込み）"""
        if not self.building_coverage_ratio:
            return None
        bonus = 0.10 if self.corner_lot else 0
        return min(self.building_coverage_ratio + bonus, 0.80)

    @property
    def max_building_footprint(self) -> Optional[float]:
        """最大建築面積（㎡）— セットバック・角地緩和考慮"""
        area = self.effective_land_area
        coverage = self.effective_coverage
        if area and coverage:
            return area * coverage
        return None

    @property
    def max_total_floor_area(self) -> Optional[float]:
        """最大延床面積（㎡）— セットバック考慮"""
        area = self.effective_land_area
        if area and self.floor_area_ratio:
            return area * self.floor_area_ratio
        return None

    @property
    def is_disqualified(self) -> bool:
        """検討対象外の物件かどうか"""
        if self.has_retaining_wall or self.has_step_retaining_wall:
            return True
        if self.zoning and self.zoning in ("工業専用地域",):
            return True
        return False

    def estimate_setback(self):
        """前面道路幅員からセットバック面積を推定"""
        if not self.road_width_m or self.road_width_m >= 4.0:
            self.setback_required = False
            self.setback_area_sqm = 0
            return
        self.setback_required = True
        setback_distance = (4.0 - self.road_width_m) / 2
        frontage = self.frontage_m or (self.land_area_sqm ** 0.5 if self.land_area_sqm else 8.0)
        self.setback_area_sqm = round(setback_distance * frontage, 2)

    def to_property(self, building_plan=None) -> "Property":
        """LandListing + BuildingPlanからPropertyオブジェクトを生成（新築投資用）"""
        from models.property import Property
        from datetime import datetime as dt

        total_price = self.land_price or 0
        building_area = None
        structure = None
        units = None
        annual_rent = None
        gross_yield = None

        if building_plan:
            total_price += building_plan.estimated_construction_cost
            building_area = building_plan.actual_total_floor_area_sqm
            structure = building_plan.structure_type
            units = building_plan.max_units
            annual_rent = building_plan.estimated_annual_income
            gross_yield = building_plan.estimated_yield

        # city_code推定
        city_code = self._guess_city_code()
        pref_code = self._guess_pref_code()

        return Property(
            name=f"新築プラン: {self.address}",
            address=self.address.replace(" / ", "").replace("／", ""),
            prefecture_code=pref_code,
            city_code=city_code,
            latitude=self.latitude,
            longitude=self.longitude,
            asking_price=total_price,
            land_area=self.land_area_sqm,
            building_area=building_area,
            structure=structure,
            floors=building_plan.floors if building_plan else None,
            units=units,
            built_year=dt.now().year,
            building_age=0,
            current_rent_annual=annual_rent,
            gross_yield=gross_yield,
            nearest_station=self.station,
            station_distance_min=self.walk_minutes,
            land_use_zone=self.zoning,
            building_coverage=self.building_coverage_ratio,
            floor_area_ratio=self.floor_area_ratio,
            source=self.source,
            source_url=self.source_url,
        )

    def _guess_pref_code(self) -> str:
        addr = self.address or ""
        if "東京" in addr: return "13"
        if "神奈川" in addr: return "14"
        if "埼玉" in addr: return "11"
        if "千葉" in addr: return "12"
        return "13"

    def _guess_city_code(self) -> str:
        addr = self.address or ""
        try:
            from data.city_master import CITY_NAME_MAP
            # 長い名前順にマッチ（「小金井市」が「金井」に誤マッチしないように）
            sorted_cities = sorted(CITY_NAME_MAP.items(), key=lambda x: len(x[1]), reverse=True)
            for code, name in sorted_cities:
                if name in addr:
                    return code
        except Exception:
            pass
        # フォールバック：23区ハードコード
        wards = {
            "千代田区": "13101", "中央区": "13102", "港区": "13103",
            "新宿区": "13104", "文京区": "13105", "台東区": "13106",
            "墨田区": "13107", "江東区": "13108", "品川区": "13109",
            "目黒区": "13110", "大田区": "13111", "世田谷区": "13112",
            "渋谷区": "13113", "中野区": "13114", "杉並区": "13115",
            "豊島区": "13116", "北区": "13117", "荒川区": "13118",
            "板橋区": "13119", "練馬区": "13120", "足立区": "13121",
            "葛飾区": "13122", "江戸川区": "13123",
        }
        for ward, code in wards.items():
            if ward in addr:
                return code
        pref = self._guess_pref_code()
        return pref + "101"
