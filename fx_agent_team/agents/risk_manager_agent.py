"""Risk manager agent - enforces hard risk constraints."""

from __future__ import annotations
from agents.base_agent import BaseAgent
from models.strategy import Strategy
import config


class RiskManagerAgent(BaseAgent):
    def __init__(self):
        super().__init__("RiskManagerAgent")

    def run(self, strategy: Strategy, **kwargs) -> Strategy:
        r = strategy.risk

        # Clamp stop-loss
        r.stop_loss_pct = max(config.MIN_STOP_LOSS, min(config.MAX_STOP_LOSS, r.stop_loss_pct))

        # Clamp position size
        r.max_position_pct = max(config.MIN_POSITION_PCT, min(config.MAX_POSITION_PCT, r.max_position_pct))

        # Clamp max drawdown
        r.max_drawdown_pct = max(config.MIN_MAX_DRAWDOWN, min(config.MAX_MAX_DRAWDOWN, r.max_drawdown_pct))

        # Enforce minimum reward-to-risk ratio
        min_tp = r.stop_loss_pct * config.MIN_REWARD_RISK_RATIO
        if r.take_profit_pct < min_tp:
            self.logger.info(
                f"  Adjusted TP from {r.take_profit_pct:.3f} to {min_tp:.3f} "
                f"(min R:R = {config.MIN_REWARD_RISK_RATIO})"
            )
            r.take_profit_pct = min_tp

        return strategy
