"""分析結果DTO（as-is / rebuild / selected）"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional
from datetime import datetime

from .palette import normalize_grade


@dataclass
class ScenarioResultDTO:
    """単一シナリオ（現況 or 建替）の判定サマリー"""
    scenario: str  # as_is | rebuild
    grade: str = ""
    score: float = 0.0
    recommendation: str = ""
    confidence: Optional[float] = None
    gross_yield: Optional[float] = None
    net_yield: Optional[float] = None
    metrics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["grade"] = normalize_grade(self.grade) or self.grade
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict], default_scenario: str = "") -> "ScenarioResultDTO":
        d = d or {}
        return cls(
            scenario=str(d.get("scenario") or default_scenario),
            grade=normalize_grade(d.get("grade")) or str(d.get("grade") or ""),
            score=float(d.get("score") or d.get("overall_score") or 0.0),
            recommendation=str(d.get("recommendation") or ""),
            confidence=d.get("confidence"),
            gross_yield=d.get("gross_yield"),
            net_yield=d.get("net_yield"),
            metrics=dict(d.get("metrics") or {}),
        )


@dataclass
class SelectedJudgmentDTO:
    """UI・地図・ランキングが参照する選択済み判定（単一ソース）"""
    grade: str = ""
    score: float = 0.0
    recommendation: str = ""
    scenario: str = ""  # as_is | rebuild
    confidence: Optional[float] = None
    gross_yield: Optional[float] = None
    net_yield: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "grade": normalize_grade(self.grade) or self.grade,
            "score": float(self.score or 0.0),
            "recommendation": self.recommendation,
            "scenario": self.scenario,
            "confidence": self.confidence,
            "gross_yield": self.gross_yield,
            "net_yield": self.net_yield,
        }

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "SelectedJudgmentDTO":
        d = d or {}
        return cls(
            grade=normalize_grade(d.get("grade")) or str(d.get("grade") or ""),
            score=float(d.get("score") or d.get("overall_score") or 0.0),
            recommendation=str(d.get("recommendation") or ""),
            scenario=str(d.get("scenario") or ""),
            confidence=d.get("confidence"),
            gross_yield=d.get("gross_yield"),
            net_yield=d.get("net_yield"),
        )


@dataclass
class AnalysisResultDTO:
    """as-is / rebuild 比較を1物件にまとめた分析結果"""
    property_id: str
    property_name: str = ""
    as_is: Optional[ScenarioResultDTO] = None
    rebuild: Optional[ScenarioResultDTO] = None
    selected: SelectedJudgmentDTO = field(default_factory=SelectedJudgmentDTO)
    asset_grade: str = ""  # 資産性グレード（投資グレードと分離）
    asset_score: Optional[float] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "property_id": self.property_id,
            "property_name": self.property_name,
            "as_is": self.as_is.to_dict() if self.as_is else None,
            "rebuild": self.rebuild.to_dict() if self.rebuild else None,
            "selected": self.selected.to_dict(),
            "asset_grade": normalize_grade(self.asset_grade) or self.asset_grade,
            "asset_score": self.asset_score,
            "updated_at": self.updated_at,
            "extras": self.extras,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AnalysisResultDTO":
        return cls(
            property_id=str(d.get("property_id") or d.get("id") or ""),
            property_name=str(d.get("property_name") or d.get("name") or ""),
            as_is=ScenarioResultDTO.from_dict(d.get("as_is"), "as_is") if d.get("as_is") else None,
            rebuild=ScenarioResultDTO.from_dict(d.get("rebuild"), "rebuild") if d.get("rebuild") else None,
            selected=SelectedJudgmentDTO.from_dict(d.get("selected")),
            asset_grade=str(d.get("asset_grade") or ""),
            asset_score=d.get("asset_score"),
            updated_at=str(d.get("updated_at") or datetime.now().isoformat()),
            extras=dict(d.get("extras") or {}),
        )

    @classmethod
    def from_judgment(
        cls,
        property_id: str,
        property_name: str,
        judgment,
        *,
        scenario: str = "as_is",
        as_is=None,
        rebuild=None,
        asset_grade: str = "",
        asset_score: Optional[float] = None,
    ) -> "AnalysisResultDTO":
        if judgment is None:
            grade, score, recommendation, confidence = "", 0.0, "", None
        elif isinstance(judgment, dict):
            grade = judgment.get("grade", "")
            score = judgment.get("overall_score") or judgment.get("score") or 0.0
            recommendation = judgment.get("recommendation", "")
            confidence = judgment.get("confidence")
        else:
            grade = getattr(judgment, "grade", "") or ""
            score = getattr(judgment, "overall_score", None)
            if score is None:
                score = 0.0
            recommendation = getattr(judgment, "recommendation", "") or ""
            confidence = getattr(judgment, "confidence", None)

        selected = SelectedJudgmentDTO(
            grade=normalize_grade(grade) or str(grade or ""),
            score=float(score or 0.0),
            recommendation=str(recommendation or ""),
            scenario=scenario,
            confidence=confidence,
        )
        return cls(
            property_id=property_id,
            property_name=property_name,
            as_is=as_is,
            rebuild=rebuild,
            selected=selected,
            asset_grade=asset_grade,
            asset_score=asset_score,
        )

# 互換エイリアス
ScenarioResultDTO = ScenarioResultDTO
SelectedJudgmentDTO = SelectedJudgmentDTO
