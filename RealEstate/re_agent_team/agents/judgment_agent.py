"""投資判定エージェント - 最終的な投資判定を下す"""
from typing import Dict, List

from .base_agent import BaseAgent
from models.property import Property
from models.valuation import ValuationResult
from models.simulation import SimulationResult
from models.judgment import JudgmentResult
from config.settings import (
    GRADE_THRESHOLDS,
    MIN_GROSS_YIELD,
    TARGET_GROSS_YIELD,
    MIN_NET_YIELD,
    MIN_LAND_VALUE_RATIO,
    IDEAL_LAND_VALUE_RATIO,
)


class JudgmentAgent(BaseAgent):
    """
    バリュエーションとシミュレーション結果を総合し、
    投資判定（買うべきか否か）を下すエージェント
    """

    def __init__(self):
        super().__init__("JudgmentAgent")

    def run(
        self,
        property: Property,
        valuation: ValuationResult,
        simulation: SimulationResult,
        asset_score_grade: str = None,
    ) -> JudgmentResult:
        """投資判定を実行

        Args:
            asset_score_grade: AssetScoreAgent のグレード (S/A/B/C/D/F)。
                               F（擁壁等）なら判定を最大Dに制限、Sなら+5加点。
        """
        self.logger.info(f"投資判定開始: {property.name}")

        # 1. スコア算出
        scores = self._calculate_scores(property, valuation, simulation)
        overall = sum(scores.values()) / len(scores) if scores else 0

        # 資産性スコアグレードによる補正
        if asset_score_grade == "S":
            overall = min(100, overall + 5)
            self.logger.info("資産性スコアS → 総合スコア+5加点")
        elif asset_score_grade == "F":
            self.logger.warning("資産性スコアF（擁壁等） → 判定グレードを最大Dに制限")

        # 2. グレード判定
        grade = self._determine_grade(valuation, simulation)

        # 資産性F（擁壁・地盤リスク等）はグレードを最大Dに制限
        if asset_score_grade == "F" and grade in ("S", "A", "B", "C"):
            self.logger.warning(
                f"資産性スコアF: グレード{grade}→Dに降格"
            )
            grade = "D"

        # 3. 推奨判定
        recommendation = self._determine_recommendation(grade, overall, simulation)

        # 4. 確信度
        confidence = self._calculate_confidence(valuation, simulation)

        # 5. SWOT分析
        strengths = self._identify_strengths(property, valuation, simulation)
        weaknesses = self._identify_weaknesses(property, valuation, simulation)
        risks = self._identify_risks(property, valuation, simulation)
        opportunities = self._identify_opportunities(property, valuation, simulation)

        # 6. 主要指標サマリー
        key_metrics = self._build_key_metrics(property, valuation, simulation)

        result = JudgmentResult(
            property_id=property.id,
            property_name=property.name,
            grade=grade,
            recommendation=recommendation,
            confidence=confidence,
            overall_score=overall,
            score_breakdown=scores,
            strengths=strengths,
            weaknesses=weaknesses,
            risks=risks,
            opportunities=opportunities,
            key_metrics=key_metrics,
        )

        self.logger.info(
            f"判定完了: {grade} - {recommendation} (スコア: {overall:.1f})"
        )
        return result

    # ===== スコアリング =====

    def _calculate_scores(
        self,
        prop: Property,
        val: ValuationResult,
        sim: SimulationResult,
    ) -> Dict[str, float]:
        scores = {}

        # 立地 (0-100)
        dist = prop.station_distance_min or 10
        scores["location"] = max(20, min(100, 100 - (dist - 1) * 8))

        # 土地値 (0-100)
        lr = val.land_value_ratio_in_price
        if lr >= 0.70:
            scores["land_value"] = 95
        elif lr >= 0.60:
            scores["land_value"] = 80
        elif lr >= 0.50:
            scores["land_value"] = 65
        elif lr >= 0.40:
            scores["land_value"] = 50
        elif lr >= 0.30:
            scores["land_value"] = 35
        else:
            scores["land_value"] = max(10, lr * 100)

        # 利回り (0-100)
        ny = val.net_yield or 0
        if ny >= 0.06:
            scores["yield"] = 95
        elif ny >= 0.05:
            scores["yield"] = 80
        elif ny >= 0.04:
            scores["yield"] = 65
        elif ny >= 0.03:
            scores["yield"] = 50
        else:
            scores["yield"] = max(10, ny * 1500)

        # キャッシュフロー (0-100)
        ccr = sim.year1_cash_on_cash
        if ccr >= 0.08:
            scores["cash_flow"] = 95
        elif ccr >= 0.05:
            scores["cash_flow"] = 75
        elif ccr >= 0.03:
            scores["cash_flow"] = 55
        elif ccr >= 0.01:
            scores["cash_flow"] = 40
        elif ccr >= 0:
            scores["cash_flow"] = 25
        else:
            scores["cash_flow"] = 10

        # 成長性 (0-100) - IRRベース
        irr = sim.irr
        if irr >= 0.10:
            scores["growth"] = 95
        elif irr >= 0.07:
            scores["growth"] = 80
        elif irr >= 0.05:
            scores["growth"] = 65
        elif irr >= 0.03:
            scores["growth"] = 50
        else:
            scores["growth"] = max(10, irr * 1000)

        # リスク (0-100) - 低リスク=高スコア
        risk_score = 70  # ベース
        if sim.dscr and sim.dscr >= 1.3:
            risk_score += 15
        elif sim.dscr and sim.dscr < 1.0:
            risk_score -= 30
        if sim.break_even_occupancy and sim.break_even_occupancy < 0.70:
            risk_score += 10
        elif sim.break_even_occupancy and sim.break_even_occupancy > 0.90:
            risk_score -= 20
        if lr >= 0.50:
            risk_score += 10  # 土地値が高い=出口リスク低い
        age = prop.building_age or 0
        if age > 30:
            risk_score -= 15
        scores["risk"] = max(10, min(100, risk_score))

        # 売却益 (0-100) - 8年後売却ROI
        if hasattr(sim, 'hold_sell_roi_65') and sim.hold_sell_roi_65 != 0:
            roi = sim.hold_sell_roi_65
            if roi >= 1.0:
                scores["exit_profit"] = 95
            elif roi >= 0.5:
                scores["exit_profit"] = 80
            elif roi >= 0.3:
                scores["exit_profit"] = 65
            elif roi >= 0.1:
                scores["exit_profit"] = 50
            elif roi >= 0:
                scores["exit_profit"] = 35
            else:
                scores["exit_profit"] = 10

        # 資産性 (0-100)
        asset_score = 50
        if lr >= 0.70:
            asset_score += 25
        elif lr >= 0.50:
            asset_score += 15
        if dist and dist <= 5:
            asset_score += 15
        elif dist and dist <= 10:
            asset_score += 5
        if ny and ny >= 0.05:
            asset_score += 10
        scores["asset_value"] = min(100, asset_score)

        return scores

    def _determine_grade(
        self, val: ValuationResult, sim: SimulationResult
    ) -> str:
        ny = val.net_yield or 0
        lr = val.land_value_ratio_in_price
        irr = sim.irr

        for grade in ["S", "A", "B", "C", "D"]:
            thresh = GRADE_THRESHOLDS[grade]
            if (ny >= thresh["net_yield"]
                    and lr >= thresh["land_ratio"]
                    and irr >= thresh["irr"]):
                return grade
        return "F"

    def _determine_recommendation(
        self, grade: str, overall: float, sim: SimulationResult
    ) -> str:
        if grade == "S" and overall >= 80:
            return "強く推奨"
        elif grade in ("S", "A") and overall >= 65:
            return "推奨"
        elif grade in ("A", "B") and overall >= 50:
            return "条件付推奨"
        elif grade in ("B", "C") and overall >= 40:
            return "慎重検討"
        elif grade in ("C", "D"):
            return "見送り"
        else:
            return "強く見送り"

    def _calculate_confidence(
        self, val: ValuationResult, sim: SimulationResult
    ) -> float:
        """判定の確信度（データ充実度ベース）"""
        conf = 0.5  # ベース

        # バリュエーションデータの充実度
        if val.estimated_land_value > 0:
            conf += 0.1
        if val.estimated_market_rent_annual and val.estimated_market_rent_annual > 0:
            conf += 0.1
        if val.current_rent_vs_market is not None:
            conf += 0.1

        # シミュレーションの安定性
        scenarios = sim.scenarios
        if scenarios:
            opt_irr = scenarios.get("optimistic", {}).get("irr", 0)
            pes_irr = scenarios.get("pessimistic", {}).get("irr", 0)
            spread = abs(opt_irr - pes_irr)
            if spread < 0.03:
                conf += 0.15  # シナリオ間のブレが小さい
            elif spread < 0.06:
                conf += 0.05

        # データ品質ペナルティ
        # 土地値比率が異常に高い → 地価データの信頼性が低い
        if val.land_value_ratio_in_price > 2.0:
            conf -= 0.2
        # IRRが非現実的に高い → 計算の前提に問題あり
        if sim.irr > 0.20:
            conf -= 0.15
        # オフライン参考テーブルのみ使用
        if val.land_price_ratio == 1.0 and val.official_land_price_per_sqm > 0:
            conf -= 0.1  # APIデータなしの可能性

        return max(0.1, min(1.0, conf))

    # ===== SWOT分析 =====

    def _identify_strengths(
        self, prop: Property, val: ValuationResult, sim: SimulationResult
    ) -> List[str]:
        strengths = []
        if val.land_value_ratio_in_price >= IDEAL_LAND_VALUE_RATIO:
            strengths.append(
                f"土地値比率が高い ({val.land_value_ratio_in_price:.0%}) → 資産保全性が高い"
            )
        if val.net_yield and val.net_yield >= MIN_NET_YIELD * 1.5:
            strengths.append(f"実質利回りが良好 ({val.net_yield:.1%})")
        if prop.station_distance_min and prop.station_distance_min <= 5:
            strengths.append(f"駅近 (徒歩{prop.station_distance_min}分) → 賃貸需要が安定")
        if sim.dscr and sim.dscr >= 1.3:
            strengths.append(f"DSCR {sim.dscr:.2f} → 返済余力あり")
        if val.price_assessment in ("割安", "やや割安"):
            strengths.append(f"相場比 {val.price_assessment} ({val.price_deviation_pct:+.1f}%)")
        if sim.irr >= 0.06:
            strengths.append(f"IRR {sim.irr:.1%} → 高い投資効率")
        if hasattr(sim, 'hold_sell_roi_65') and sim.hold_sell_roi_65 >= 0.3:
            strengths.append(f"8年保有→売却ROI {sim.hold_sell_roi_65:.0%} → 高いトータルリターン")
        return strengths

    def _identify_weaknesses(
        self, prop: Property, val: ValuationResult, sim: SimulationResult
    ) -> List[str]:
        weaknesses = []
        if val.land_value_ratio_in_price < MIN_LAND_VALUE_RATIO:
            weaknesses.append(
                f"土地値比率が低い ({val.land_value_ratio_in_price:.0%}) → 建物依存リスク"
            )
        if val.net_yield and val.net_yield < MIN_NET_YIELD:
            weaknesses.append(f"実質利回りが低い ({val.net_yield:.1%})")
        if prop.building_age and prop.building_age > 25:
            weaknesses.append(f"築{prop.building_age}年 → 大規模修繕・建替リスク")
        if sim.year1_cash_flow < 0:
            weaknesses.append(f"初年度CF赤字 ({sim.year1_cash_flow:,}円)")
        if val.price_assessment in ("割高", "やや割高"):
            weaknesses.append(f"相場比 {val.price_assessment}")
        return weaknesses

    def _identify_risks(
        self, prop: Property, val: ValuationResult, sim: SimulationResult
    ) -> List[str]:
        risks = []
        if sim.break_even_occupancy and sim.break_even_occupancy > 0.85:
            risks.append(
                f"損益分岐稼働率 {sim.break_even_occupancy:.0%} → 空室耐性が低い"
            )
        if sim.dscr and sim.dscr < 1.1:
            risks.append(f"DSCR {sim.dscr:.2f} → 金利上昇時に返済困難リスク")

        # 悲観シナリオ
        pes = sim.scenarios.get("pessimistic", {})
        if pes.get("irr", 0) < 0:
            risks.append("悲観シナリオでIRRがマイナス")

        if val.current_rent_vs_market and val.current_rent_vs_market > 1.1:
            risks.append(
                f"現行賃料が相場比 {val.current_rent_vs_market:.0%} → 賃料下落リスク"
            )

        # データ品質リスク
        if val.land_value_ratio_in_price > 2.0:
            risks.append(
                f"⚠ 土地値比率{val.land_value_ratio_in_price:.0%}は異常値 "
                "→ 地価推定がオフラインテーブルに依存している可能性。"
                "実際の公示地価を確認すること"
            )
        if sim.irr > 0.20:
            risks.append(
                f"⚠ IRR{sim.irr:.1%}は不動産投資では非現実的（通常5-15%）。"
                "土地値上昇の前提を再検証すること"
            )
        return risks

    def _identify_opportunities(
        self, prop: Property, val: ValuationResult, sim: SimulationResult
    ) -> List[str]:
        opps = []
        if val.current_rent_vs_market and val.current_rent_vs_market < 0.9:
            opps.append(
                f"現行賃料が相場以下 ({val.current_rent_vs_market:.0%}) → 賃上げ余地"
            )
        if val.price_assessment in ("割安", "やや割安"):
            opps.append("値付けが割安 → 価格交渉で更に有利に")
        if prop.building_coverage and prop.floor_area_ratio:
            # 容積率未消化チェック（簡易）
            if prop.land_area and prop.building_area:
                used_ratio = prop.building_area / prop.land_area
                if used_ratio < prop.floor_area_ratio * 0.7:
                    opps.append("容積率に余裕 → 増築・建替で収益向上可能性")
        opt = sim.scenarios.get("optimistic", {})
        if opt.get("irr", 0) >= 0.10:
            opps.append(f"楽観シナリオIRR {opt['irr']:.1%} → 上振れポテンシャル")
        if hasattr(sim, 'hold_sell_total_return_65') and sim.hold_sell_total_return_65 > 0:
            opps.append(f"8年保有+売却(6.5%)でトータル{sim.hold_sell_total_return_65/10000:,.0f}万円のリターン見込")
        return opps

    def _build_key_metrics(
        self, prop: Property, val: ValuationResult, sim: SimulationResult
    ) -> Dict[str, str]:
        metrics = {}
        if prop.asking_price:
            metrics["売出価格"] = f"{prop.asking_price:,}円"
        if val.gross_yield:
            metrics["表面利回り"] = f"{val.gross_yield:.1%}"
        if val.net_yield:
            metrics["実質利回り"] = f"{val.net_yield:.1%}"
        metrics["土地値比率"] = f"{val.land_value_ratio_in_price:.0%}"
        metrics["推定土地価格"] = f"{val.estimated_land_value:,}円"
        metrics["IRR"] = f"{sim.irr:.1%}"
        metrics["NPV"] = f"{sim.npv:,}円"
        if sim.dscr:
            metrics["DSCR"] = f"{sim.dscr:.2f}"
        metrics["初年度CCR"] = f"{sim.year1_cash_on_cash:.1%}"
        if sim.payback_years:
            metrics["投資回収"] = f"{sim.payback_years}年"
        metrics["価格妥当性"] = val.price_assessment
        if hasattr(sim, 'hold_sell_exit_price_65') and sim.hold_sell_exit_price_65 > 0:
            metrics["8年後売却(6.5%)"] = f"{sim.hold_sell_exit_price_65/10000:,.0f}万円"
            metrics["8年後売却(7.0%)"] = f"{sim.hold_sell_exit_price_70/10000:,.0f}万円"
            metrics["8年累積CF"] = f"{sim.hold_sell_cumulative_cf/10000:,.0f}万円"
            metrics["トータルリターン(6.5%)"] = f"{sim.hold_sell_total_return_65/10000:,.0f}万円"
            metrics["ROI(6.5%売却)"] = f"{sim.hold_sell_roi_65:.0%}"
        return metrics
