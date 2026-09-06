"""Backtest result data model."""

from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class BacktestResult:
    strategy_id: str
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    total_trades: int
    calmar_ratio: float
    avg_trade_return: float
    equity_curve: pd.Series = field(default_factory=pd.Series, repr=False)
    trade_log: pd.DataFrame = field(default_factory=pd.DataFrame, repr=False)

    def summary(self) -> dict:
        return {
            "strategy_id": self.strategy_id,
            "total_return": f"{self.total_return:.2%}",
            "sharpe_ratio": f"{self.sharpe_ratio:.3f}",
            "max_drawdown": f"{self.max_drawdown:.2%}",
            "win_rate": f"{self.win_rate:.2%}",
            "profit_factor": f"{self.profit_factor:.3f}",
            "total_trades": self.total_trades,
            "calmar_ratio": f"{self.calmar_ratio:.3f}",
        }
