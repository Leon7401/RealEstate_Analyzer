"""Backtest agent - runs strategies against historical data."""

from __future__ import annotations
import pandas as pd
from agents.base_agent import BaseAgent
from models.strategy import Strategy
from models.backtest_result import BacktestResult
from engine.backtest_engine import run_backtest
import config


class BacktestAgent(BaseAgent):
    def __init__(self):
        super().__init__("BacktestAgent")

    def run(self, strategy: Strategy, data: pd.DataFrame, **kwargs) -> BacktestResult:
        self.logger.info(f"Backtesting strategy {strategy.id} (v{strategy.version})")
        result = run_backtest(strategy, data, initial_capital=config.INITIAL_CAPITAL)
        self.logger.info(
            f"  Result: return={result.total_return:.2%}, "
            f"sharpe={result.sharpe_ratio:.3f}, "
            f"drawdown={result.max_drawdown:.2%}, "
            f"trades={result.total_trades}"
        )
        return result
