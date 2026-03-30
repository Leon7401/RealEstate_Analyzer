"""投資判定結果モデル"""
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict
import json
from datetime import datetime


@dataclass
class JudgmentResult:
    """最終投資判定"""
    property_id: str
    property_name: str

    # 判定
    grade: str                              # S/A/B/C/D/F
    recommendation: str                     # 強く推奨 / 推奨 / 条件付推奨 / 見送り / 強く見送り
    confidence: float                       # 確信度 0.0-1.0

    # サマリースコア (0-100)
    overall_score: float
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    # {
    #   "location":      85,  # 立地
    #   "land_value":    70,  # 土地値
    #   "yield":         65,  # 利回り
    #   "cash_flow":     60,  # キャッシュフロー
    #   "growth":        50,  # 成長性
    #   "risk":          75,  # リスク（低い=高スコア）
    # }

    # 判定根拠
    strengths: List[str] = field(default_factory=list)    # 強み
    weaknesses: List[str] = field(default_factory=list)   # 弱み
    risks: List[str] = field(default_factory=list)        # リスク要因
    opportunities: List[str] = field(default_factory=list)  # 機会

    # 主要指標サマリー
    key_metrics: Dict[str, str] = field(default_factory=dict)
    # {
    #   "表面利回り": "5.8%",
    #   "実質利回り": "4.2%",
    #   "土地値比率": "62%",
    #   "IRR": "6.5%",
    #   "賃料妥当性": "やや割安",
    # }

    # メタデータ
    judged_at: str = field(default_factory=lambda: datetime.now().isoformat())
    analysis_version: str = "1.0.0"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @property
    def summary_text(self) -> str:
        lines = [
            f"{'='*60}",
            f"  投資判定レポート: {self.property_name}",
            f"{'='*60}",
            f"  グレード: {self.grade}  |  判定: {self.recommendation}",
            f"  総合スコア: {self.overall_score:.1f}/100  |  確信度: {self.confidence:.0%}",
            f"{'─'*60}",
        ]
        if self.key_metrics:
            lines.append("  【主要指標】")
            for k, v in self.key_metrics.items():
                lines.append(f"    {k}: {v}")
        if self.strengths:
            lines.append(f"{'-'*60}")
            lines.append("  [強み]")
            for s in self.strengths:
                lines.append(f"    + {s}")
        if self.weaknesses:
            lines.append("  [弱み]")
            for w in self.weaknesses:
                lines.append(f"    - {w}")
        if self.risks:
            lines.append("  [リスク]")
            for r in self.risks:
                lines.append(f"    ! {r}")
        lines.append(f"{'='*60}")
        return "\n".join(lines)
