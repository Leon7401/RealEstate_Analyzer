"""Strategy data model with JSON serialization."""

from __future__ import annotations
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


@dataclass
class IndicatorConfig:
    name: str
    params: dict[str, Any]

    def column_name(self) -> str:
        key_param = list(self.params.values())[0] if self.params else ""
        return f"{self.name}_{key_param}"


@dataclass
class RuleConfig:
    type: str  # cross_above, cross_below, threshold_above, threshold_below
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskConfig:
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    max_position_pct: float = 0.10
    max_drawdown_pct: float = 0.15


@dataclass
class Strategy:
    indicators: list[IndicatorConfig]
    entry_rules: list[RuleConfig]
    exit_rules: list[RuleConfig]
    risk: RiskConfig = field(default_factory=RiskConfig)
    pair: str = "EURUSD=X"
    timeframe: str = "1d"
    id: str = field(default_factory=lambda: f"strat_{uuid.uuid4().hex[:8]}")
    version: int = 1
    parent_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, d: dict) -> Strategy:
        d = d.copy()
        d["indicators"] = [IndicatorConfig(**i) for i in d["indicators"]]
        d["entry_rules"] = [RuleConfig(**r) for r in d["entry_rules"]]
        d["exit_rules"] = [RuleConfig(**r) for r in d["exit_rules"]]
        d["risk"] = RiskConfig(**d["risk"])
        return cls(**d)

    @classmethod
    def from_json(cls, s: str) -> Strategy:
        return cls.from_dict(json.loads(s))

    def clone(self) -> Strategy:
        new = Strategy.from_dict(self.to_dict())
        new.parent_id = self.id
        new.version = self.version + 1
        new.id = f"strat_{uuid.uuid4().hex[:8]}"
        new.created_at = datetime.now().isoformat()
        return new
