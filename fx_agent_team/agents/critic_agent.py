"""Critic agent - analyzes backtest results and provides improvement suggestions."""

from __future__ import annotations
from agents.base_agent import BaseAgent
from models.backtest_result import BacktestResult
from models.critique import Critique


class CriticAgent(BaseAgent):
    def __init__(self):
        super().__init__("CriticAgent")

    def run(self, result: BacktestResult, **kwargs) -> Critique:
        self.logger.info(f"Critiquing strategy {result.strategy_id}")
        weaknesses = []
        suggestions = []
        scores = {}
        penalty = 0

        # Sharpe ratio analysis
        scores["sharpe"] = result.sharpe_ratio
        if result.sharpe_ratio < 0:
            weaknesses.append("Negative risk-adjusted return")
            suggestions.append("Change strategy template entirely")
            penalty += 3
        elif result.sharpe_ratio < 0.5:
            weaknesses.append("Low risk-adjusted return (Sharpe < 0.5)")
            suggestions.append("Adjust indicator parameters for better signal quality")
            penalty += 2
        elif result.sharpe_ratio < 1.0:
            suggestions.append("Fine-tune entry timing to improve Sharpe ratio")
            penalty += 1

        # Drawdown analysis
        scores["max_drawdown"] = result.max_drawdown
        if result.max_drawdown < -0.25:
            weaknesses.append("Severe drawdown (> 25%)")
            suggestions.append("Tighten stop-loss percentage")
            suggestions.append("Reduce position size")
            penalty += 3
        elif result.max_drawdown < -0.15:
            weaknesses.append("High drawdown (> 15%)")
            suggestions.append("Consider tighter stop-loss")
            penalty += 1

        # Win rate analysis
        scores["win_rate"] = result.win_rate
        if result.win_rate < 0.3:
            weaknesses.append("Very low win rate (< 30%)")
            suggestions.append("Adjust entry thresholds to be more selective")
            penalty += 2
        elif result.win_rate < 0.4:
            weaknesses.append("Low win rate (< 40%)")
            suggestions.append("Add confirmation indicator to entry rules")
            penalty += 1

        # Profit factor
        scores["profit_factor"] = result.profit_factor
        if result.profit_factor < 1.0:
            weaknesses.append("Unprofitable (profit factor < 1.0)")
            suggestions.append("Increase take-profit or tighten stop-loss")
            penalty += 2
        elif result.profit_factor < 1.2:
            weaknesses.append("Marginal edge (profit factor < 1.2)")
            suggestions.append("Widen take-profit target")
            penalty += 1

        # Trade count
        scores["total_trades"] = result.total_trades
        if result.total_trades < 10:
            weaknesses.append("Too few trades for statistical significance")
            suggestions.append("Relax entry conditions to generate more signals")
            penalty += 2
        elif result.total_trades < 30:
            weaknesses.append("Limited trade sample (< 30)")
            suggestions.append("Consider loosening entry thresholds slightly")
            penalty += 1

        # Total return
        scores["total_return"] = result.total_return
        if result.total_return < 0:
            weaknesses.append("Negative total return")
            penalty += 2

        # Grade assignment
        if penalty == 0:
            grade = "A"
        elif penalty <= 2:
            grade = "B"
        elif penalty <= 4:
            grade = "C"
        elif penalty <= 6:
            grade = "D"
        else:
            grade = "F"

        critique = Critique(
            strategy_id=result.strategy_id,
            weaknesses=weaknesses,
            scores=scores,
            suggestions=suggestions,
            overall_grade=grade,
        )

        self.logger.info(f"  Grade: {grade} | Weaknesses: {len(weaknesses)} | Suggestions: {len(suggestions)}")
        return critique
