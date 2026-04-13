"""投資シミュレーションモデル"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict
import json


@dataclass
class YearlyProjection:
    """年次キャッシュフロー予測"""
    year: int
    gross_rent: int                         # 総賃料収入
    vacancy_loss: int                       # 空室損
    operating_expenses: int                 # 運営費（管理費+修繕+保険+税金）
    noi: int                                # NOI (Net Operating Income)
    debt_service: int                       # 年間返済額
    cash_flow_before_tax: int               # 税引前CF
    cumulative_cf: int                      # 累積CF
    property_value: int                     # 推定物件価値
    equity: int                             # 自己資本（物件価値-残債）
    loan_balance: int                       # ローン残高


@dataclass
class SimulationResult:
    """投資シミュレーション結果"""
    property_id: str

    # 投資概要
    purchase_price: int                     # 購入価格
    initial_investment: int                 # 自己資金（頭金+諸費用）
    loan_amount: int                        # 借入額
    loan_rate: float                        # 金利
    loan_term: int                          # 返済期間

    # 初年度収支
    year1_gross_rent: int                   # 初年度総賃料
    year1_noi: int                          # 初年度NOI
    year1_cash_flow: int                    # 初年度CF
    year1_cash_on_cash: float               # 初年度CCR (Cash on Cash Return)

    # 長期指標
    irr: float                              # IRR (Internal Rate of Return)
    npv: int                                # NPV (Net Present Value)
    payback_years: Optional[int] = None     # 投資回収年数
    total_profit: int = 0                   # 総利益（シミュレーション期間合計）
    avg_annual_return: float = 0.0          # 平均年間リターン

    # 出口戦略
    exit_year: int = 10                     # 想定売却年
    exit_price: int = 0                     # 想定売却価格
    exit_profit: int = 0                    # 売却時利益

    # 年次予測
    yearly_projections: List[YearlyProjection] = field(default_factory=list)

    # リスク指標
    break_even_occupancy: Optional[float] = None  # 損益分岐稼働率
    dscr: Optional[float] = None            # DSCR (Debt Service Coverage Ratio)

    # 8年保有→売却シミュレーション
    hold_sell_exit_price_65: int = 0
    hold_sell_exit_price_70: int = 0
    hold_sell_cumulative_cf: int = 0
    hold_sell_total_return_65: int = 0
    hold_sell_total_return_70: int = 0
    hold_sell_roi_65: float = 0.0
    hold_sell_roi_70: float = 0.0
    hold_sell_exit_cap_base: Optional[float] = None
    hold_sell_exit_cap_stress: Optional[float] = None

    # 動的最適化パラメータ
    dynamic_assumptions: Dict[str, float] = field(default_factory=dict)
    optimization_score: Optional[float] = None

    # シナリオ分析
    scenarios: Dict[str, dict] = field(default_factory=dict)
    # {"optimistic": {...}, "base": {...}, "pessimistic": {...}}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["yearly_projections"] = [asdict(yp) for yp in self.yearly_projections]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
