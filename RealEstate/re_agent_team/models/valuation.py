"""物件評価（バリュエーション）モデル"""
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict
import json


@dataclass
class ValuationResult:
    """物件バリュエーション結果"""
    property_id: str

    # 土地評価
    estimated_land_value: int               # 推定土地価格（円）
    land_value_per_sqm: float               # 推定土地㎡単価
    official_land_price_per_sqm: float      # 公示地価㎡単価（参考）
    land_price_ratio: float                 # 公示地価比率（推定/公示）

    # 建物評価
    estimated_building_value: int           # 推定建物価格（円）
    building_replacement_cost: Optional[int] = None  # 建物再調達原価
    depreciation_rate: Optional[float] = None        # 減価率

    # 土地値比率
    land_value_ratio_in_price: float = 0.0  # 物件価格に対する土地値比率
    # = estimated_land_value / asking_price

    # 賃料評価
    estimated_market_rent_monthly: Optional[int] = None     # 推定相場賃料（月額）
    estimated_market_rent_annual: Optional[int] = None      # 推定相場賃料（年額）
    current_rent_vs_market: Optional[float] = None          # 現行賃料/相場賃料
    # > 1.0 = 割高（下落リスク）, < 1.0 = 割安（上昇余地）

    # 利回り評価
    gross_yield: Optional[float] = None                     # 表面利回り
    net_yield: Optional[float] = None                       # 実質利回り
    cap_rate_area_avg: Optional[float] = None               # エリア平均Cap Rate

    # 価格妥当性
    price_assessment: str = "適正"          # 割安 / やや割安 / 適正 / やや割高 / 割高
    price_deviation_pct: float = 0.0        # 相場からの乖離率（%）

    # 詳細スコア (0-100)
    scores: Dict[str, float] = field(default_factory=dict)
    # {"location": 80, "land_value": 75, "yield": 60, "building": 50, "rent_stability": 70}

    overall_score: float = 0.0              # 総合スコア (0-100)
    comments: list = field(default_factory=list)

    # 経費率
    expense_rate: Optional[float] = None            # 動的経費率
    expense_breakdown: Optional[dict] = None        # 経費内訳

    # データ品質
    sample_count: int = 0                           # 地価推定に使用した取引事例数

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
