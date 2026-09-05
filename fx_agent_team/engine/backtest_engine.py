"""Vectorized backtest engine."""

from __future__ import annotations
import numpy as np
import pandas as pd
from models.strategy import Strategy
from models.backtest_result import BacktestResult
from indicators.registry import apply_indicator
from engine import metrics


def _resolve_column(df: pd.DataFrame, ref: str) -> pd.Series:
    """Resolve a column reference like 'SMA_50' or 'close'."""
    if ref in df.columns:
        return df[ref]
    # Try case-insensitive match
    for col in df.columns:
        if col.lower() == ref.lower():
            return df[col]
    raise KeyError(f"Column '{ref}' not found. Available: {list(df.columns)}")


def _evaluate_rule(df: pd.DataFrame, rule_type: str, params: dict) -> pd.Series:
    """Evaluate a single rule, returning a boolean Series."""
    if rule_type == "cross_above":
        fast = _resolve_column(df, params["fast"])
        slow = _resolve_column(df, params["slow"])
        return (fast > slow) & (fast.shift(1) <= slow.shift(1))

    elif rule_type == "cross_below":
        fast = _resolve_column(df, params["fast"])
        slow = _resolve_column(df, params["slow"])
        return (fast < slow) & (fast.shift(1) >= slow.shift(1))

    elif rule_type == "threshold_above":
        indicator = _resolve_column(df, params["indicator"])
        return indicator > params["value"]

    elif rule_type == "threshold_below":
        indicator = _resolve_column(df, params["indicator"])
        return indicator < params["value"]

    elif rule_type == "band_breakout":
        return df["close"] > _resolve_column(df, params["upper_band"])

    elif rule_type == "band_breakdown":
        return df["close"] < _resolve_column(df, params["lower_band"])

    else:
        raise ValueError(f"Unknown rule type: {rule_type}")


def run_backtest(
    strategy: Strategy,
    data: pd.DataFrame,
    initial_capital: float = 100_000,
) -> BacktestResult:
    """Run a full backtest for a strategy on historical data."""
    df = data.copy()

    # Apply all indicators
    for ind in strategy.indicators:
        df = apply_indicator(ind.name, df, ind.params)

    df = df.dropna()

    if len(df) < 50:
        return _empty_result(strategy.id)

    # Evaluate entry/exit signals
    entry_signals = pd.Series(True, index=df.index)
    for rule in strategy.entry_rules:
        try:
            entry_signals = entry_signals & _evaluate_rule(df, rule.type, rule.params)
        except (KeyError, ValueError):
            entry_signals = pd.Series(False, index=df.index)

    exit_signals = pd.Series(False, index=df.index)
    for rule in strategy.exit_rules:
        try:
            exit_signals = exit_signals | _evaluate_rule(df, rule.type, rule.params)
        except (KeyError, ValueError):
            pass

    # Generate positions with stop-loss and take-profit
    position = pd.Series(0.0, index=df.index)
    trades = []
    in_trade = False
    entry_price = 0.0
    entry_idx = None

    for i in range(1, len(df)):
        idx = df.index[i]
        prev_idx = df.index[i - 1]
        price = df["close"].iloc[i]

        if not in_trade and entry_signals.iloc[i]:
            position.iloc[i] = 1.0
            in_trade = True
            entry_price = price
            entry_idx = idx
        elif in_trade:
            pnl_pct = (price - entry_price) / entry_price

            # Check stop-loss / take-profit
            hit_stop = pnl_pct <= -strategy.risk.stop_loss_pct
            hit_tp = pnl_pct >= strategy.risk.take_profit_pct
            hit_exit = exit_signals.iloc[i]

            if hit_stop or hit_tp or hit_exit:
                position.iloc[i] = 0.0
                in_trade = False
                trades.append({
                    "entry_date": entry_idx,
                    "exit_date": idx,
                    "entry_price": entry_price,
                    "exit_price": price,
                    "return": pnl_pct,
                    "exit_reason": "stop_loss" if hit_stop else ("take_profit" if hit_tp else "signal"),
                })
            else:
                position.iloc[i] = 1.0

    # Compute returns
    price_returns = df["close"].pct_change().fillna(0)
    strategy_returns = position.shift(1).fillna(0) * price_returns
    equity = (1 + strategy_returns).cumprod() * initial_capital

    # Check max drawdown constraint
    max_dd = metrics.max_drawdown(equity)

    trade_df = pd.DataFrame(trades)
    trade_returns = pd.Series([t["return"] for t in trades]) if trades else pd.Series(dtype=float)

    total_ret = (equity.iloc[-1] / initial_capital) - 1

    return BacktestResult(
        strategy_id=strategy.id,
        total_return=total_ret,
        sharpe_ratio=metrics.sharpe_ratio(strategy_returns),
        max_drawdown=max_dd,
        win_rate=metrics.win_rate(trade_returns),
        profit_factor=metrics.profit_factor(trade_returns),
        total_trades=len(trades),
        calmar_ratio=metrics.calmar_ratio(total_ret, max_dd),
        avg_trade_return=float(trade_returns.mean()) if len(trade_returns) > 0 else 0.0,
        equity_curve=equity,
        trade_log=trade_df,
    )


def _empty_result(strategy_id: str) -> BacktestResult:
    return BacktestResult(
        strategy_id=strategy_id,
        total_return=0.0,
        sharpe_ratio=0.0,
        max_drawdown=0.0,
        win_rate=0.0,
        profit_factor=0.0,
        total_trades=0,
        calmar_ratio=0.0,
        avg_trade_return=0.0,
    )
