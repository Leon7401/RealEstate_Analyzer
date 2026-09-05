"""Performance metrics computation."""

from __future__ import annotations
import numpy as np
import pandas as pd


def sharpe_ratio(returns: pd.Series, periods_per_year: int = 252) -> float:
    if returns.std() == 0:
        return 0.0
    return float((returns.mean() / returns.std()) * np.sqrt(periods_per_year))


def max_drawdown(equity_curve: pd.Series) -> float:
    peak = equity_curve.cummax()
    dd = (equity_curve - peak) / peak
    return float(dd.min())


def win_rate(trade_returns: pd.Series) -> float:
    if len(trade_returns) == 0:
        return 0.0
    return float((trade_returns > 0).sum() / len(trade_returns))


def profit_factor(trade_returns: pd.Series) -> float:
    gross_profit = trade_returns[trade_returns > 0].sum()
    gross_loss = abs(trade_returns[trade_returns < 0].sum())
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def calmar_ratio(total_return: float, max_dd: float) -> float:
    if max_dd == 0:
        return 0.0
    return float(total_return / abs(max_dd))
