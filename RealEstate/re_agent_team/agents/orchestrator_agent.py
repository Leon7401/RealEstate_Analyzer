"""オーケストレーターエージェント - 全体を統括"""
from typing import Optional, List

from .base_agent import BaseAgent
from .land_price_agent import LandPriceAgent
from .rental_agent import RentalAgent
from .valuation_agent import ValuationAgent
from .simulation_agent import SimulationAgent
from .judgment_agent import JudgmentAgent
from .critic_agent import CriticAgent
from models.property import Property
from models.judgment import JudgmentResult
from storage.report_store import ReportStore
from storage.database import Database


class OrchestratorAgent(BaseAgent):
    """
    不動産投資判定パイプラインを統括するオーケストレーター

    パイプライン:
    1. LandPriceAgent  → エリア地価データ収集・分析
    2. RentalAgent     → エリア賃料データ収集・分析
    3. ValuationAgent  → 物件バリュエーション（土地値比率・利回り・妥当性）
    4. SimulationAgent → 投資キャッシュフローシミュレーション
    5. JudgmentAgent   → 最終投資判定（グレード・推奨・SWOT）
    6. CriticAgent     → 批判的レビュー（データ品質・信頼性評価）
    """

    def __init__(self):
        super().__init__("OrchestratorAgent")
        self.land_agent = LandPriceAgent()
        self.rental_agent = RentalAgent()
        self.valuation_agent = ValuationAgent()
        self.simulation_agent = SimulationAgent()
        self.judgment_agent = JudgmentAgent()
        self.report_store = ReportStore()
        self.critic_agent = CriticAgent()
        self.db = Database()

    def run(
        self,
        property: Property,
        loan_rate: float = None,
        loan_term: int = None,
        ltv: float = None,
        asset_score_grade: str = None,
    ) -> JudgmentResult:
        """
        物件の投資判定パイプラインを実行

        Args:
            property: 分析対象物件
            loan_rate: ローン金利（Noneでデフォルト）
            loan_term: 返済期間（年）
            ltv: LTV比率
        """
        self.logger.info(f"{'='*60}")
        self.logger.info(f"投資判定パイプライン開始: {property.name}")
        self.logger.info(f"{'='*60}")

        # Phase 1: 地価データ収集
        self.logger.info("[Phase 1/5] 地価データ収集...")
        land_summary = self.land_agent.run(
            prefecture_code=property.prefecture_code,
            city_code=property.city_code,
            target_lat=property.latitude,
            target_lng=property.longitude,
        )

        # Phase 2: 賃料データ分析
        self.logger.info("[Phase 2/5] 賃料データ分析...")
        rental_summary = self.rental_agent.run(
            city_code=property.city_code,
            structure=property.structure or "RC",
            building_age=property.building_age,
            station_distance_min=property.station_distance_min,
        )

        # Phase 3: バリュエーション
        self.logger.info("[Phase 3/5] バリュエーション実行...")
        valuation = self.valuation_agent.run(property)

        # Phase 4: シミュレーション
        self.logger.info("[Phase 4/5] 投資シミュレーション...")
        simulation = self.simulation_agent.run(
            property=property,
            valuation=valuation,
            loan_rate=loan_rate,
            loan_term=loan_term,
            ltv=ltv,
        )

        # Phase 5: 最終判定
        self.logger.info("[Phase 5/5] 投資判定...")
        judgment = self.judgment_agent.run(
            property=property,
            valuation=valuation,
            simulation=simulation,
            asset_score_grade=asset_score_grade,
        )

        # Phase 6: 批判的レビュー
        self.logger.info("[Phase 6/6] 批判的レビュー...")
        critic_review = self.critic_agent.run(
            property=property,
            valuation=valuation,
            simulation=simulation,
            judgment=judgment,
        )
        # 信頼性が低い場合は警告
        if not critic_review["usable_for_investment"]:
            self.logger.warning(
                f"⚠ CriticAgent: 信頼性{critic_review['reliability_grade']} "
                f"- この判定結果は実投資判断に使用不可"
            )
            for issue in critic_review["issues"]:
                if issue["severity"] in ("critical", "major"):
                    self.logger.warning(f"  [{issue['severity']}] {issue['message']}")

        # レポート保存
        self.report_store.save(property, valuation, simulation, judgment)

        # DBにも保存
        try:
            self.db.upsert_property(property.to_dict())
            self.db.save_judgment(judgment.to_dict())
        except Exception as e:
            self.logger.warning(f"DB保存エラー: {e}")

        self.logger.info(f"{'='*60}")
        self.logger.info(f"判定結果: {judgment.grade} - {judgment.recommendation}")
        self.logger.info(f"{'='*60}")

        return {
            "judgment": judgment,
            "valuation": valuation,
            "simulation": simulation,
            "land_summary": land_summary,
            "rental_summary": rental_summary,
            "critic_review": critic_review,
        }

    def run_batch(
        self, properties: List[Property], **kwargs
    ) -> List[dict]:
        """複数物件の一括判定"""
        results = []
        for i, prop in enumerate(properties, 1):
            self.logger.info(f"\n[{i}/{len(properties)}] {prop.name}")
            try:
                result = self.run(prop, **kwargs)
                results.append(result)
            except Exception as e:
                self.logger.error(f"判定エラー: {prop.name} - {e}")
                continue

        # ランキング出力
        if results:
            self.logger.info("\n" + "="*60)
            self.logger.info("  投資判定ランキング")
            self.logger.info("="*60)
            sorted_results = sorted(
                results, key=lambda r: r["judgment"].overall_score, reverse=True
            )
            for rank, r in enumerate(sorted_results, 1):
                j = r["judgment"]
                self.logger.info(
                    f"  #{rank} [{j.grade}] {j.property_name} "
                    f"(スコア: {j.overall_score:.1f}) - {j.recommendation}"
                )

        return results
