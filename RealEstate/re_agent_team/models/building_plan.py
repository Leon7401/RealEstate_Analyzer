"""建築プランデータモデル - 土地に対する建築プランの算出結果"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List
import json


@dataclass
class BuildingPlan:
    """建築プラン（構造×階数×間取りサイズの組み合わせ）"""
    land_listing_id: int                        # 対応する土地物件ID
    structure_type: str                         # "木造" / "重量鉄骨"
    floors: int                                 # 階数 (2-5)
    unit_size_sqm: float                        # 1戸あたり面積 (20/25/30/35㎡)

    # 面積計算結果
    max_footprint_sqm: float = 0.0              # 最大建築面積
    max_total_floor_area_sqm: float = 0.0       # 最大延床面積
    actual_total_floor_area_sqm: float = 0.0    # 実延床面積
    common_area_ratio: float = 0.0              # 共用部率
    effective_floor_area_sqm: float = 0.0       # 有効面積（賃貸可能面積）

    # プラン結果
    max_units: int = 0                          # 最大戸数

    # 収益試算
    estimated_rent_per_sqm: float = 0.0         # 推定㎡賃料（月額）
    equipment_grade: str = "premium"            # 設備グレード（standard/premium/premium_loft）
    equipment_premium_factor: float = 1.05      # 設備プレミアム係数
    estimated_monthly_rent_per_unit: int = 0    # 推定月額賃料/戸（設備プレミアム込み）
    estimated_annual_income: int = 0            # 推定年間収入
    estimated_construction_cost: int = 0        # 推定建築費（本体のみ）
    land_acquisition_cost: int = 0              # 土地取得諸費用
    construction_overhead: int = 0              # 建築付帯費用
    setback_cost_premium: int = 0               # セットバック施工費増分
    total_investment: int = 0                   # 総投資額（土地+諸費用+建築費+付帯費用）
    estimated_yield: float = 0.0                # 推定表面利回り（総投資額ベース）

    # 建築規制チェック結果
    volume_reduction_ratio: float = 0.0         # 斜線制限等によるボリューム減率
    ward_ordinance_compliant: bool = True        # ワンルーム条例適合
    ward_ordinance_note: str = ""                # 条例備考

    # メタデータ
    id: Optional[int] = None                    # DB auto-increment

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> "BuildingPlan":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in d.items() if k in valid_fields})

    @property
    def plan_label(self) -> str:
        return f"{self.structure_type}{self.floors}階 / {self.unit_size_sqm:.0f}㎡×{self.max_units}戸"

    @property
    def yield_label(self) -> str:
        return f"{self.estimated_yield * 100:.2f}%"


@dataclass
class LandPlanSummary:
    """土地1件に対する全プランのサマリー"""
    land_listing_id: int
    address: str = ""
    land_price: int = 0
    land_area_sqm: float = 0.0
    plans: List[BuildingPlan] = field(default_factory=list)

    @property
    def best_plan(self) -> Optional[BuildingPlan]:
        """最高利回りプラン"""
        if not self.plans:
            return None
        return max(self.plans, key=lambda p: p.estimated_yield)

    @property
    def best_yield(self) -> float:
        bp = self.best_plan
        return bp.estimated_yield if bp else 0.0

    def plans_by_structure(self) -> dict:
        """構造別にプランをグループ化"""
        result = {}
        for p in self.plans:
            key = f"{p.structure_type}_{p.floors}F"
            result.setdefault(key, []).append(p)
        return result

    def to_dict(self) -> dict:
        return {
            "land_listing_id": self.land_listing_id,
            "address": self.address,
            "land_price": self.land_price,
            "land_area_sqm": self.land_area_sqm,
            "best_yield": self.best_yield,
            "best_plan": self.best_plan.to_dict() if self.best_plan else None,
            "plans": [p.to_dict() for p in self.plans],
            "plan_count": len(self.plans),
        }
