"""バリュエーション（物件評価）エージェント"""
from typing import Optional

from .base_agent import BaseAgent
from .land_price_agent import LandPriceAgent
from .rental_agent import RentalAgent
from models.property import Property
from models.valuation import ValuationResult
from config.settings import (
    VACANCY_RATE,
    MANAGEMENT_FEE_RATE,
    REPAIR_RESERVE_RATE,
    INSURANCE_RATE,
    PROPERTY_TAX_RATE,
    CITY_PLANNING_TAX_RATE,
)


class ValuationAgent(BaseAgent):
    """
    物件の総合的な価値評価を行うエージェント
    - 土地値推定
    - 建物価値推定
    - 土地値比率算出
    - 賃料妥当性判定
    - 利回り計算
    """

    # 構造別の再調達原価（円/㎡）
    REPLACEMENT_COST_PER_SQM = {
        "RC": 280000,
        "SRC": 320000,
        "鉄骨": 220000,
        "軽量鉄骨": 200000,
        "木造": 170000,
    }

    # 構造別の法定耐用年数
    USEFUL_LIFE = {
        "RC": 47,
        "SRC": 47,
        "鉄骨": 34,
        "軽量鉄骨": 27,
        "木造": 22,
    }

    def __init__(self):
        super().__init__("ValuationAgent")
        self.land_agent = LandPriceAgent()
        self.rental_agent = RentalAgent()

    def run(self, property: Property) -> ValuationResult:
        """物件のバリュエーションを実行"""
        self.logger.info(f"バリュエーション開始: {property.name}")

        # 1. 土地価値推定
        land_est = self._estimate_land_value(property)

        # 2. 建物価値推定
        building_est = self._estimate_building_value(property)

        # 3. 賃料推定
        rent_est = self._estimate_rent(property)

        # 4. 利回り計算
        yields = self._calculate_yields(property, rent_est)

        # 5. 土地値比率（サニティチェック付き）
        land_ratio = (
            land_est["estimated_price"] / property.asking_price
            if property.asking_price and property.asking_price > 0
            else 0
        )
        # 土地値比率が200%超は地価推定の信頼性が低い
        if land_ratio > 2.0:
            self.logger.warning(
                f"土地値比率{land_ratio:.0%}は異常（推定地価{land_est['estimated_price']:,}円 "
                f"vs 売出価格{property.asking_price:,}円）。"
                f"地価推定方法: {land_est.get('method', '不明')}, "
                f"サンプル数: {land_est.get('sample_count', 0)}"
            )
        # 土地値比率が300%超の場合は信頼性なしとして上限クリップ
        if land_ratio > 3.0:
            self.logger.warning("土地値比率を上限3.0にクリップ")
            land_ratio = 3.0

        # 6. 価格妥当性判定
        total_estimated = land_est["estimated_price"] + building_est["estimated_value"]
        deviation = (
            (property.asking_price - total_estimated) / total_estimated
            if total_estimated > 0 and property.asking_price
            else 0
        )
        assessment = self._assess_price(deviation)

        # 7. スコアリング
        scores = self._calculate_scores(
            property, land_ratio, yields, rent_est, deviation
        )

        result = ValuationResult(
            property_id=property.id,
            estimated_land_value=land_est["estimated_price"],
            land_value_per_sqm=land_est["price_per_sqm"],
            official_land_price_per_sqm=land_est["official_price_per_sqm"],
            land_price_ratio=land_est["ratio_to_official"],
            estimated_building_value=building_est["estimated_value"],
            building_replacement_cost=building_est.get("replacement_cost"),
            depreciation_rate=building_est.get("depreciation_rate"),
            land_value_ratio_in_price=land_ratio,
            estimated_market_rent_monthly=rent_est.get("monthly_rent"),
            estimated_market_rent_annual=rent_est.get("annual_rent"),
            current_rent_vs_market=(
                property.current_rent_annual / rent_est["annual_rent"]
                if property.current_rent_annual and rent_est.get("annual_rent")
                else None
            ),
            gross_yield=yields.get("gross_yield"),
            net_yield=yields.get("net_yield"),
            price_assessment=assessment,
            price_deviation_pct=deviation * 100,
            scores=scores,
            overall_score=sum(scores.values()) / len(scores) if scores else 0,
            expense_rate=yields.get("expense_rate"),
            sample_count=land_est.get("sample_count", 0),
        )

        self.logger.info(
            f"バリュエーション完了: 土地値比率={land_ratio:.1%}, "
            f"表面利回り={yields.get('gross_yield', 0):.1%}, "
            f"判定={assessment}"
        )
        return result

    # ===== 内部メソッド =====

    def _estimate_land_value(self, prop: Property) -> dict:
        if not prop.land_area or prop.land_area <= 0:
            return {
                "estimated_price": 0,
                "price_per_sqm": 0,
                "official_price_per_sqm": 0,
                "ratio_to_official": 0,
            }

        # 物件入力に㎡単価が与えられていれば最優先（ヒートマップ連携値を含む）
        if prop.price_per_sqm and prop.price_per_sqm > 0:
            est_price = int(prop.price_per_sqm * prop.land_area)
            return {
                "estimated_price": est_price,
                "price_per_sqm": prop.price_per_sqm,
                "official_price_per_sqm": prop.price_per_sqm,
                "ratio_to_official": 1.0,
                "sample_count": 1,
                "method": "input_price_per_sqm",
            }

        return self.land_agent.estimate_land_value(
            address=prop.address,
            land_area=prop.land_area,
            prefecture_code=prop.prefecture_code,
            city_code=prop.city_code,
        )

    def _estimate_building_value(self, prop: Property) -> dict:
        if not prop.building_area or not prop.structure:
            return {"estimated_value": 0}

        # 再調達原価
        cost_per_sqm = self.REPLACEMENT_COST_PER_SQM.get(
            prop.structure, 250000
        )
        replacement_cost = int(cost_per_sqm * prop.building_area)

        # 減価償却
        useful_life = self.USEFUL_LIFE.get(prop.structure, 47)
        age = prop.building_age or 0
        remaining_ratio = max(0, (useful_life - age) / useful_life)

        # 経済的残存価値（最低でも再調達の10%）
        depreciation_rate = 1 - remaining_ratio
        estimated_value = int(replacement_cost * max(remaining_ratio, 0.10))

        return {
            "estimated_value": estimated_value,
            "replacement_cost": replacement_cost,
            "depreciation_rate": depreciation_rate,
            "useful_life": useful_life,
            "remaining_years": max(0, useful_life - age),
        }

    def _estimate_rent(self, prop: Property) -> dict:
        if not prop.building_area:
            return {"monthly_rent": 0, "annual_rent": 0, "confidence": "low"}

        return self.rental_agent.estimate_rent(
            city_code=prop.city_code,
            structure=prop.structure or "RC",
            building_age=prop.building_age or 10,
            area_sqm=prop.building_area,
            station_distance_min=prop.station_distance_min or 5,
        )

    def _calculate_yields(self, prop: Property, rent_est: dict) -> dict:
        if not prop.asking_price or prop.asking_price <= 0:
            return {}

        # 表面利回り
        annual_rent = (
            prop.current_rent_annual
            if prop.current_rent_annual
            else rent_est.get("annual_rent", 0)
        )
        gross_yield = annual_rent / prop.asking_price if annual_rent else 0

        # 実質利回り（経費控除後 - 構造・築年で動的調整）
        expense_rate = self._dynamic_expense_rate(prop)
        net_income = annual_rent * (1 - expense_rate)
        net_yield = net_income / prop.asking_price if annual_rent else 0

        return {
            "gross_yield": gross_yield,
            "net_yield": net_yield,
            "annual_rent": annual_rent,
            "annual_expenses": int(annual_rent * expense_rate),
            "noi": int(net_income),
            "expense_rate": expense_rate,
        }

    def _dynamic_expense_rate(self, prop: Property) -> float:
        """構造・築年に応じた動的経費率"""
        age = prop.building_age or 15
        structure = prop.structure or "RC"

        # ベース経費率
        base = (
            MANAGEMENT_FEE_RATE      # 5%
            + INSURANCE_RATE         # 0.3%
            + PROPERTY_TAX_RATE      # 1.4%
            + CITY_PLANNING_TAX_RATE # 0.3%
        )  # = 7%

        # 空室率: 築年・構造で調整
        if age <= 5:
            vacancy = 0.03
        elif age <= 15:
            vacancy = 0.05
        elif age <= 25:
            vacancy = 0.07
        else:
            vacancy = 0.10  # 築古は空室リスク高い

        # 木造は空室率を少し加算（競争力低下が早い）
        if structure == "木造" and age > 15:
            vacancy += 0.02

        # 修繕積立: 築年で大幅に変動
        if age <= 10:
            repair = 0.03
        elif age <= 20:
            repair = 0.05
        elif age <= 30:
            repair = 0.08
        else:
            repair = 0.12  # 築30年超は大規模修繕期

        # 木造は修繕費が高い傾向
        if structure == "木造" and age > 20:
            repair += 0.03

        total = base + vacancy + repair
        self.logger.debug(
            f"動的経費率: {total:.1%} "
            f"(基本{base:.1%}+空室{vacancy:.1%}+修繕{repair:.1%}) "
            f"[{structure}築{age}年]"
        )
        return total

    def _assess_price(self, deviation: float) -> str:
        if deviation < -0.15:
            return "割安"
        elif deviation < -0.05:
            return "やや割安"
        elif deviation < 0.05:
            return "適正"
        elif deviation < 0.15:
            return "やや割高"
        else:
            return "割高"

    def _calculate_scores(
        self,
        prop: Property,
        land_ratio: float,
        yields: dict,
        rent_est: dict,
        deviation: float,
    ) -> dict:
        scores = {}

        # 立地スコア（駅距離ベース）
        dist = prop.station_distance_min or 10
        if dist <= 3:
            scores["location"] = 90
        elif dist <= 5:
            scores["location"] = 80
        elif dist <= 7:
            scores["location"] = 70
        elif dist <= 10:
            scores["location"] = 60
        else:
            scores["location"] = max(30, 80 - dist * 3)

        # 土地値スコア
        scores["land_value"] = min(100, max(0, land_ratio * 120))

        # 利回りスコア
        gy = yields.get("gross_yield", 0)
        if gy >= 0.08:
            scores["yield"] = 95
        elif gy >= 0.06:
            scores["yield"] = 80
        elif gy >= 0.05:
            scores["yield"] = 65
        elif gy >= 0.04:
            scores["yield"] = 50
        else:
            scores["yield"] = max(10, gy * 1000)

        # 建物スコア（築年数ベース）
        age = prop.building_age or 15
        scores["building"] = max(10, 100 - age * 2.5)

        # 価格妥当性スコア
        scores["price_fairness"] = min(100, max(0, 70 - deviation * 200))

        return scores
