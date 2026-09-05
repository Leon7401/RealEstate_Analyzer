"""Critique data model."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Critique:
    strategy_id: str
    weaknesses: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    overall_grade: str = "C"

    def severity(self) -> float:
        grade_map = {"A": 0.1, "B": 0.3, "C": 0.5, "D": 0.7, "F": 1.0}
        return grade_map.get(self.overall_grade, 0.5)
