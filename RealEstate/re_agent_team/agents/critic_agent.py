"""批判レビューエージェント - 投資判定結果の客観的批判と品質チェック"""
from typing import List, Dict

from .base_agent import BaseAgent
from models.property import Property
from models.valuation import ValuationResult
from models.simulation import SimulationResult
from models.judgment import JudgmentResult


class CriticAgent(BaseAgent):
    """
    投資判定結果を客観的に批判するエージェント。

    実際の投資判断に使えるかを検証し、以下を指摘する:
    - データ品質の問題（オフラインデータ依存、サンプル不足）
    - 計算ロジックの不整合
    - 現実との乖離（非現実的なIRR、利回り等）
    - 判定グレードの信頼性
    """

    # 現実的な範囲の閾値
    REALISTIC_BOUNDS = {
        "irr": (0.01, 0.15),           # 不動産IRR: 1-15%
        "gross_yield": (0.02, 0.12),    # 表面利回り: 2-12%
        "net_yield": (0.01, 0.09),      # 実質利回り: 1-9%
        "land_ratio": (0.10, 2.00),     # 土地値比率: 10-200%
        "dscr": (0.80, 3.00),           # DSCR: 0.8-3.0
        "ccr": (-0.05, 0.15),           # CCR: -5%~15%
    }

    def __init__(self):
        super().__init__("CriticAgent")

    def run(
        self,
        property: Property,
        valuation: ValuationResult,
        simulation: SimulationResult,
        judgment: JudgmentResult,
    ) -> Dict:
        """
        投資判定結果を批判的にレビュー

        Returns:
            {
                "reliability_grade": str,    # A/B/C/D/F
                "issues": List[dict],        # 発見された問題
                "warnings": List[str],       # 警告
                "recommendations": List[str],# 改善提案
                "data_quality_score": float, # 0-100
                "usable_for_investment": bool,# 実投資に使えるか
            }
        """
        issues = []
        warnings = []
        recommendations = []

        # 1. データソースの品質チェック
        dq_score = self._check_data_quality(property, valuation, issues, warnings)

        # 2. 計算結果のサニティチェック
        self._check_sanity(valuation, simulation, issues, warnings)

        # 3. 経費モデルの妥当性
        self._check_expense_model(property, simulation, issues, warnings)

        # 4. 判定グレードの整合性
        self._check_grade_consistency(valuation, simulation, judgment, issues, warnings)

        # 5. 改善提案の生成
        recommendations = self._generate_recommendations(
            property, valuation, simulation, judgment, issues
        )

        # 総合信頼性判定
        critical_count = sum(1 for i in issues if i["severity"] == "critical")
        major_count = sum(1 for i in issues if i["severity"] == "major")
        minor_count = sum(1 for i in issues if i["severity"] == "minor")

        if critical_count > 0:
            reliability = "F"
            usable = False
        elif major_count >= 2:
            reliability = "D"
            usable = False
        elif major_count == 1:
            reliability = "C"
            usable = False
        elif minor_count >= 3:
            reliability = "B"
            usable = True
        elif minor_count > 0:
            reliability = "A"
            usable = True
        else:
            reliability = "S"
            usable = True

        self.logger.info(
            f"批判レビュー完了: 信頼性={reliability}, "
            f"致命的={critical_count}, 重大={major_count}, 軽微={minor_count}, "
            f"投資判断利用={'可' if usable else '不可'}"
        )

        return {
            "reliability_grade": reliability,
            "issues": issues,
            "warnings": warnings,
            "recommendations": recommendations,
            "data_quality_score": dq_score,
            "usable_for_investment": usable,
            "issue_summary": {
                "critical": critical_count,
                "major": major_count,
                "minor": minor_count,
            },
        }

    def _check_data_quality(
        self, prop: Property, val: ValuationResult,
        issues: list, warnings: list,
    ) -> float:
        """データソースの品質をスコアリング"""
        score = 100.0

        # APIデータなし（参考テーブル依存）— sample_count=0で判定
        if getattr(val, 'sample_count', None) == 0 or (
            val.land_price_ratio == 1.0
            and not getattr(val, 'land_value_per_sqm', 0)
        ):
            issues.append({
                "severity": "major",
                "category": "data_source",
                "message": "地価推定の取引事例データが不足。"
                           "参考テーブルに依存している可能性あり",
                "impact": "地価推定の精度が低下する可能性（誤差10-30%）",
            })
            score -= 15

        # city_codeが不明
        if prop.city_code and prop.city_code.startswith("unknown"):
            issues.append({
                "severity": "critical",
                "category": "data_source",
                "message": f"市区町村コードが不明: {prop.city_code}。"
                           "地価・賃料推定が全てデフォルト値",
                "impact": "全ての推定値が信頼できない",
            })
            score -= 50

        # 賃貸事例不足
        if val.current_rent_vs_market is None:
            issues.append({
                "severity": "minor",
                "category": "data_source",
                "message": "現行賃料vs相場賃料の比較データなし",
                "impact": "賃料の妥当性が未検証",
            })
            score -= 10

        # 座標データなし
        if not prop.latitude or not prop.longitude:
            warnings.append(
                "座標データなし → 周辺地価の距離フィルタが機能していない"
            )
            score -= 10

        # 物件情報の欠損チェック
        missing = []
        if not prop.land_area:
            missing.append("土地面積")
        if not prop.building_area:
            missing.append("建物面積")
        if not prop.structure:
            missing.append("構造")
        if not prop.building_age and prop.building_age != 0:
            missing.append("築年数")
        if missing:
            issues.append({
                "severity": "minor" if len(missing) <= 1 else "major",
                "category": "data_completeness",
                "message": f"物件情報の欠損: {', '.join(missing)}",
                "impact": "バリュエーション精度が低下",
            })
            score -= len(missing) * 5

        return max(0, score)

    def _check_sanity(
        self, val: ValuationResult, sim: SimulationResult,
        issues: list, warnings: list,
    ):
        """計算結果のサニティチェック"""
        bounds = self.REALISTIC_BOUNDS

        # IRR
        if sim.irr > bounds["irr"][1]:
            issues.append({
                "severity": "critical",
                "category": "calculation",
                "message": f"IRR {sim.irr:.1%} は不動産投資では非現実的"
                           f"（通常{bounds['irr'][0]:.0%}-{bounds['irr'][1]:.0%}）",
                "impact": "出口想定の土地価格が膨張している可能性が高い。"
                          "投資判断の根拠として使用不可",
            })

        # 土地値比率
        lr = val.land_value_ratio_in_price
        if lr > bounds["land_ratio"][1]:
            issues.append({
                "severity": "critical",
                "category": "calculation",
                "message": f"土地値比率 {lr:.0%} は異常値"
                           f"（上限{bounds['land_ratio'][1]:.0%}）",
                "impact": "地価推定が実態と大幅に乖離。"
                          "参考テーブルのcity_codeマッピングを確認すべき",
            })
        elif lr > 1.5:
            warnings.append(
                f"土地値比率{lr:.0%}は高め。割安物件か地価推定の誤差か確認要"
            )

        # 表面利回り
        gy = val.gross_yield or 0
        if gy > bounds["gross_yield"][1]:
            warnings.append(
                f"表面利回り{gy:.1%}は高利回り物件。"
                "リスク（空室・修繕・立地）を慎重に評価すべき"
            )
        elif gy < bounds["gross_yield"][0]:
            warnings.append(
                f"表面利回り{gy:.1%}はキャピタルゲイン狙い以外では低すぎる"
            )

    def _check_expense_model(
        self, prop: Property, sim: SimulationResult,
        issues: list, warnings: list,
    ):
        """経費モデルの妥当性チェック"""
        age = prop.building_age or 0
        structure = prop.structure or ""

        # 木造築25年超 → 修繕費5%では過少
        if "木造" in structure and age > 25:
            issues.append({
                "severity": "major",
                "category": "expense_model",
                "message": f"木造築{age}年: 修繕積立率5%は過少。"
                           "実態は10-15%が必要",
                "impact": f"実質利回りが1-2%過大に見積もられている可能性",
            })

        # RC築30年超 → 大規模修繕リスク
        if structure in ("RC", "SRC") and age > 30:
            warnings.append(
                f"{structure}築{age}年: 大規模修繕（外壁・防水・設備更新）が"
                "必要な時期。一時金1000万-3000万円の見込みを加味すべき"
            )

        # DSCR < 1.0 → 返済不能
        if sim.dscr and sim.dscr < 1.0:
            issues.append({
                "severity": "critical",
                "category": "cash_flow",
                "message": f"DSCR {sim.dscr:.2f} < 1.0: "
                           "NOIでローン返済をカバーできない",
                "impact": "持ち出しが発生し、投資として成立しない",
            })

    def _check_grade_consistency(
        self, val: ValuationResult, sim: SimulationResult,
        judgment: JudgmentResult, issues: list, warnings: list,
    ):
        """判定グレードの整合性チェック"""
        # IRRが非現実的なのにS/Aグレード → グレードが信頼できない
        if sim.irr > 0.20 and judgment.grade in ("S", "A"):
            issues.append({
                "severity": "critical",
                "category": "grade",
                "message": f"グレード{judgment.grade}は非現実的なIRR{sim.irr:.1%}"
                           "に基づいている",
                "impact": "このグレードは信頼できない。"
                          "地価データを修正後に再判定すべき",
            })

        # CF赤字なのにB以上 → おかしい
        if sim.year1_cash_flow < 0 and judgment.grade in ("S", "A", "B"):
            warnings.append(
                f"初年度CF赤字（{sim.year1_cash_flow:,}円）なのに"
                f"グレード{judgment.grade}は楽観的すぎる可能性"
            )

    def _generate_recommendations(
        self, prop: Property, val: ValuationResult,
        sim: SimulationResult, judgment: JudgmentResult,
        issues: list,
    ) -> List[str]:
        """改善提案"""
        recs = []

        critical = [i for i in issues if i["severity"] == "critical"]
        if critical:
            recs.append(
                "【最優先】致命的な問題があるため、以下を先に解決すること: "
                + "; ".join(i["message"][:50] for i in critical)
            )

        if any(i["category"] == "data_source" for i in issues):
            recs.append(
                "不動産情報ライブラリAPIキーを取得し.envに設定すること。"
                "オフラインテーブルは精度が低い"
            )

        if val.land_value_ratio_in_price > 1.5:
            recs.append(
                "土地値比率が高すぎる場合、以下を確認: "
                "(1) city_codeが正しいか "
                "(2) 公示地価のサンプル数 "
                "(3) 実際の近隣取引事例"
            )

        if not prop.latitude:
            recs.append(
                "物件の緯度経度を設定すると、周辺地価の距離フィルタが"
                "機能し、精度が大幅に向上する"
            )

        if any(i["category"] == "expense_model" for i in issues):
            recs.append(
                "築古物件の経費率を構造・築年別に調整する仕組みを追加すべき"
            )

        if not recs:
            recs.append("データ品質に大きな問題なし。判定結果は参考に値する")

        return recs
