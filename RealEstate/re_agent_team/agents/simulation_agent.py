"""投資シミュレーションエージェント"""
from typing import Optional, Dict, List

from .base_agent import BaseAgent
from models.property import Property
from models.valuation import ValuationResult
from models.simulation import SimulationResult, YearlyProjection
from config.settings import (
    DEFAULT_LOAN_RATE,
    DEFAULT_LOAN_TERM,
    DEFAULT_LTV,
    SIMULATION_YEARS,
    VACANCY_RATE,
    MANAGEMENT_FEE_RATE,
    REPAIR_RESERVE_RATE,
    INSURANCE_RATE,
    PROPERTY_TAX_RATE,
    CITY_PLANNING_TAX_RATE,
    LAND_APPRECIATION_RATE,
    BUILDING_DEPRECIATION_RATE,
    RENT_DECLINE_RATE,
    DISCOUNT_RATE,
)


class SimulationAgent(BaseAgent):
    """
    不動産投資のキャッシュフローシミュレーションを実行するエージェント
    - 年次CF予測
    - IRR/NPV計算
    - シナリオ分析（楽観/基準/悲観）
    - 出口戦略シミュレーション
    """

    # 購入諸費用率
    ACQUISITION_COST_RATE = 0.07  # 仲介手数料+登記+取得税等 約7%
    # 売却諸費用
    SELLING_BROKER_FEE_RATE = 0.03   # 売却時仲介手数料 3%
    CAPITAL_GAINS_TAX_RATE = 0.20    # 長期譲渡所得税率 約20%（所得税15%+住民税5%）

    def __init__(self):
        super().__init__("SimulationAgent")

    def run(
        self,
        property: Property,
        valuation: ValuationResult,
        loan_rate: float = None,
        loan_term: int = None,
        ltv: float = None,
    ) -> SimulationResult:
        """投資シミュレーションを実行"""
        self.logger.info(f"シミュレーション開始: {property.name}")

        loan_rate = loan_rate or DEFAULT_LOAN_RATE
        loan_term = loan_term or DEFAULT_LOAN_TERM
        ltv = ltv or DEFAULT_LTV

        # asking_price は新築の場合 land_price + construction_cost を含む
        # （LandListing.to_property() で合算済み）
        purchase_price = property.asking_price or 0
        loan_amount = int(purchase_price * ltv)
        down_payment = purchase_price - loan_amount
        acquisition_costs = int(purchase_price * self.ACQUISITION_COST_RATE)
        initial_investment = down_payment + acquisition_costs

        # 年間賃料
        annual_rent = (
            property.current_rent_annual
            or valuation.estimated_market_rent_annual
            or 0
        )

        # 出口価格計算用の土地値・建物値
        # 推定土地値が購入価格を超える場合は購入価格ベースで按分
        est_land = valuation.estimated_land_value or 0
        est_bldg = valuation.estimated_building_value or 0
        est_total = est_land + est_bldg
        if est_total > 0 and est_total > purchase_price:
            # 購入価格を土地/建物比率で按分
            land_for_sim = int(purchase_price * est_land / est_total)
            bldg_for_sim = purchase_price - land_for_sim
            self.logger.info(
                f"出口価格補正: 推定合計{est_total:,} > 購入{purchase_price:,} "
                f"→ 土地{land_for_sim:,}/建物{bldg_for_sim:,}に按分"
            )
        else:
            land_for_sim = est_land
            bldg_for_sim = est_bldg

        # 年次予測
        projections = self._project_cashflows(
            purchase_price=purchase_price,
            annual_rent=annual_rent,
            loan_amount=loan_amount,
            loan_rate=loan_rate,
            loan_term=loan_term,
            land_value=land_for_sim,
            building_value=bldg_for_sim,
        )

        # 初年度指標
        y1 = projections[0] if projections else None
        year1_cf = y1.cash_flow_before_tax if y1 else 0
        year1_ccr = year1_cf / initial_investment if initial_investment > 0 else 0

        # 出口戦略（10年後売却想定）
        exit_year = min(10, len(projections))

        # IRR計算
        irr = self._calculate_irr(initial_investment, projections, exit_year=exit_year, purchase_price=purchase_price)

        # NPV計算（IRRと同じexit_yearで整合）
        npv = self._calculate_npv(initial_investment, projections, DISCOUNT_RATE, exit_year=exit_year, purchase_price=purchase_price)

        # 投資回収年数
        payback = self._calculate_payback(initial_investment, projections)
        exit_projection = projections[exit_year - 1] if exit_year > 0 else None
        exit_price = exit_projection.property_value if exit_projection else 0
        exit_loan_balance = exit_projection.loan_balance if exit_projection else 0
        net_exit = self._net_exit_price(exit_price, purchase_price)
        exit_profit = net_exit - exit_loan_balance - initial_investment

        # DSCR
        monthly_payment = self._monthly_payment(loan_amount, loan_rate, loan_term)
        annual_debt_service = monthly_payment * 12
        y1_noi = y1.noi if y1 else 0
        dscr = y1_noi / annual_debt_service if annual_debt_service > 0 else 0

        # 損益分岐稼働率
        total_expenses = (
            annual_rent * (
                MANAGEMENT_FEE_RATE + REPAIR_RESERVE_RATE
                + INSURANCE_RATE + PROPERTY_TAX_RATE + CITY_PLANNING_TAX_RATE
            )
        )
        break_even_occ = (
            (annual_debt_service + total_expenses) / annual_rent
            if annual_rent > 0 else 1.0
        )

        # 総利益
        total_profit = sum(p.cash_flow_before_tax for p in projections)

        # シナリオ分析（補正済み土地値/建物値を使用）
        scenarios = self._run_scenarios(
            purchase_price, annual_rent, loan_amount,
            loan_rate, loan_term, initial_investment,
            land_for_sim, bldg_for_sim,
        )

        # 8年保有→売却シミュレーション（事業モデル準拠、売却諸費用控除後）
        hold_years = 8
        if len(projections) >= hold_years:
            p8 = projections[hold_years - 1]
            exit_price_65 = self._calculate_exit_by_yield(p8.gross_rent, 0.065)
            exit_price_70 = self._calculate_exit_by_yield(p8.gross_rent, 0.070)
            net_exit_65 = self._net_exit_price(exit_price_65, purchase_price)
            net_exit_70 = self._net_exit_price(exit_price_70, purchase_price)
            cumulative_cf_8y = sum(pr.cash_flow_before_tax for pr in projections[:hold_years])
            total_return_65 = cumulative_cf_8y + net_exit_65 - p8.loan_balance - initial_investment
            total_return_70 = cumulative_cf_8y + net_exit_70 - p8.loan_balance - initial_investment
            roi_65 = total_return_65 / initial_investment if initial_investment > 0 else 0
            roi_70 = total_return_70 / initial_investment if initial_investment > 0 else 0
        else:
            exit_price_65 = exit_price_70 = cumulative_cf_8y = 0
            total_return_65 = total_return_70 = 0
            roi_65 = roi_70 = 0

        result = SimulationResult(
            property_id=property.id,
            purchase_price=purchase_price,
            initial_investment=initial_investment,
            loan_amount=loan_amount,
            loan_rate=loan_rate,
            loan_term=loan_term,
            year1_gross_rent=annual_rent,
            year1_noi=y1_noi,
            year1_cash_flow=year1_cf,
            year1_cash_on_cash=year1_ccr,
            irr=irr,
            npv=npv,
            payback_years=payback,
            total_profit=total_profit,
            avg_annual_return=total_profit / SIMULATION_YEARS if SIMULATION_YEARS > 0 else 0,
            exit_year=exit_year,
            exit_price=exit_price,
            exit_profit=exit_profit,
            yearly_projections=projections,
            break_even_occupancy=break_even_occ,
            dscr=dscr,
            scenarios=scenarios,
            hold_sell_exit_price_65=exit_price_65,
            hold_sell_exit_price_70=exit_price_70,
            hold_sell_cumulative_cf=cumulative_cf_8y,
            hold_sell_total_return_65=total_return_65,
            hold_sell_total_return_70=total_return_70,
            hold_sell_roi_65=roi_65,
            hold_sell_roi_70=roi_70,
        )

        self.logger.info(
            f"シミュレーション完了: IRR={irr:.1%}, NPV={npv:,}円, "
            f"CCR={year1_ccr:.1%}, DSCR={dscr:.2f}"
        )
        return result

    # ===== 内部メソッド =====

    def _project_cashflows(
        self,
        purchase_price: int,
        annual_rent: int,
        loan_amount: int,
        loan_rate: float,
        loan_term: int,
        land_value: int,
        building_value: int,
        rent_decline: float = None,
        land_growth: float = None,
        building_deprec: float = None,
        vacancy: float = None,
    ) -> List[YearlyProjection]:
        rent_decline = rent_decline if rent_decline is not None else RENT_DECLINE_RATE
        land_growth = land_growth if land_growth is not None else LAND_APPRECIATION_RATE
        building_deprec = building_deprec if building_deprec is not None else BUILDING_DEPRECIATION_RATE
        vacancy = vacancy if vacancy is not None else VACANCY_RATE

        monthly_payment = self._monthly_payment(loan_amount, loan_rate, loan_term)
        annual_debt = int(monthly_payment * 12)

        expense_rate = (
            MANAGEMENT_FEE_RATE + REPAIR_RESERVE_RATE
            + INSURANCE_RATE + PROPERTY_TAX_RATE + CITY_PLANNING_TAX_RATE
        )

        projections = []
        cumulative_cf = 0
        remaining_loan = loan_amount

        for year in range(1, SIMULATION_YEARS + 1):
            # 賃料は毎年逓減
            year_rent = int(annual_rent * ((1 - rent_decline) ** (year - 1)))

            vacancy_loss = int(year_rent * vacancy)
            effective_rent = year_rent - vacancy_loss
            expenses = int(year_rent * expense_rate)
            noi = effective_rent - expenses

            # ローン返済（元利均等）
            interest_payment = remaining_loan * loan_rate
            principal_payment = annual_debt - interest_payment
            remaining_loan = max(0, remaining_loan - principal_payment)

            cf = noi - annual_debt
            cumulative_cf += cf

            # 物件価値推定
            year_land = int(land_value * ((1 + land_growth) ** year))
            year_building = int(building_value * max(0.10, 1 - building_deprec * year))
            property_value = year_land + year_building

            equity = property_value - int(remaining_loan)

            projections.append(YearlyProjection(
                year=year,
                gross_rent=year_rent,
                vacancy_loss=vacancy_loss,
                operating_expenses=expenses,
                noi=noi,
                debt_service=annual_debt,
                cash_flow_before_tax=cf,
                cumulative_cf=cumulative_cf,
                property_value=property_value,
                equity=equity,
                loan_balance=int(remaining_loan),
            ))

        return projections

    def _monthly_payment(
        self, principal: int, annual_rate: float, term_years: int
    ) -> float:
        """元利均等返済の月額"""
        if principal <= 0 or annual_rate <= 0 or term_years <= 0:
            return 0
        r = annual_rate / 12
        n = term_years * 12
        return principal * r * (1 + r) ** n / ((1 + r) ** n - 1)

    def _net_exit_price(self, exit_price: int, book_value: int) -> int:
        """売却手取額（仲介手数料+譲渡所得税控除後）
        net = exit_price * (1 - broker_fee) - max(0, gain) * tax_rate
        """
        after_broker = int(exit_price * (1 - self.SELLING_BROKER_FEE_RATE))
        gain = exit_price - book_value
        tax = int(max(0, gain) * self.CAPITAL_GAINS_TAX_RATE)
        return after_broker - tax

    def _calculate_irr(
        self, initial_investment: int, projections: List[YearlyProjection],
        exit_year: int = 10, purchase_price: int = 0,
    ) -> float:
        """IRR（内部収益率）をニュートン法で計算"""
        if not projections or initial_investment <= 0:
            return 0.0

        exit_idx = min(exit_year, len(projections)) - 1
        cashflows = [-initial_investment]
        for i, p in enumerate(projections[:exit_idx]):
            cashflows.append(p.cash_flow_before_tax)
        # 最終年に売却益を加算（売却諸費用・譲渡税控除後）
        if exit_idx < len(projections):
            final = projections[exit_idx]
            net_exit = self._net_exit_price(final.property_value, purchase_price)
            cashflows.append(
                final.cash_flow_before_tax + net_exit - final.loan_balance
            )

        # ニュートン法
        rate = 0.05
        for _ in range(200):
            npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))
            dnpv = sum(
                -t * cf / (1 + rate) ** (t + 1)
                for t, cf in enumerate(cashflows)
            )
            if abs(dnpv) < 1e-10:
                break
            new_rate = rate - npv / dnpv
            if abs(new_rate - rate) < 1e-8:
                rate = new_rate
                break
            rate = new_rate

        # IRRが25%超は不動産投資では非現実的 → データ品質の問題を示唆
        clamped = max(-1.0, min(rate, 1.0))
        if clamped > 0.25:
            self.logger.warning(
                f"IRR={clamped:.1%}は非現実的（通常5-15%）。"
                "地価推定値やCFに異常がある可能性あり"
            )
        return clamped

    def _calculate_npv(
        self, initial_investment: int,
        projections: List[YearlyProjection],
        discount_rate: float,
        exit_year: int = 10,
        purchase_price: int = 0,
    ) -> int:
        """NPV計算（出口売却益を含む、IRRと整合）"""
        if not projections:
            return -initial_investment
        exit_idx = min(exit_year, len(projections)) - 1
        pv = -initial_investment
        for i, p in enumerate(projections[:exit_idx]):
            pv += p.cash_flow_before_tax / (1 + discount_rate) ** p.year
        # 最終年に売却益を加算（売却諸費用・譲渡税控除後、IRR計算と同じロジック）
        if exit_idx < len(projections):
            final = projections[exit_idx]
            net_exit = self._net_exit_price(final.property_value, purchase_price)
            terminal_cf = (
                final.cash_flow_before_tax
                + net_exit
                - final.loan_balance
            )
            pv += terminal_cf / (1 + discount_rate) ** final.year
        return int(pv)

    def _calculate_payback(
        self, initial_investment: int, projections: List[YearlyProjection]
    ) -> Optional[int]:
        cumulative = 0
        for p in projections:
            cumulative += p.cash_flow_before_tax
            if cumulative >= initial_investment:
                return p.year
        return None

    def _run_scenarios(
        self,
        purchase_price: int,
        annual_rent: int,
        loan_amount: int,
        loan_rate: float,
        loan_term: int,
        initial_investment: int,
        land_value: int,
        building_value: int,
    ) -> Dict[str, dict]:
        scenarios = {}

        configs = {
            "optimistic": {
                "rent_decline": 0.002,
                "land_growth": 0.01,
                "vacancy": 0.03,
            },
            "base": {
                "rent_decline": RENT_DECLINE_RATE,
                "land_growth": LAND_APPRECIATION_RATE,
                "vacancy": VACANCY_RATE,
            },
            "pessimistic": {
                "rent_decline": 0.01,
                "land_growth": -0.005,
                "vacancy": 0.10,
            },
        }

        for name, cfg in configs.items():
            projs = self._project_cashflows(
                purchase_price, annual_rent, loan_amount,
                loan_rate, loan_term, land_value, building_value,
                rent_decline=cfg["rent_decline"],
                land_growth=cfg["land_growth"],
                vacancy=cfg["vacancy"],
            )
            irr = self._calculate_irr(initial_investment, projs, purchase_price=purchase_price)
            npv = self._calculate_npv(initial_investment, projs, DISCOUNT_RATE, purchase_price=purchase_price)
            total_cf = sum(p.cash_flow_before_tax for p in projs)

            scenarios[name] = {
                "irr": irr,
                "npv": npv,
                "total_cashflow": total_cf,
                "year10_equity": projs[9].equity if len(projs) >= 10 else 0,
                "assumptions": cfg,
            }

        return scenarios

    def _calculate_exit_by_yield(self, annual_rent_at_exit: int, exit_yield: float = 0.065) -> int:
        """出口利回りから逆算した売却価格"""
        if annual_rent_at_exit <= 0 or exit_yield <= 0:
            return 0
        return int(annual_rent_at_exit / exit_yield)
