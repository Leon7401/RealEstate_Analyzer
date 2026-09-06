"""Optimizer agent - mutates strategies based on critique feedback."""

from __future__ import annotations
import random
import re
from agents.base_agent import BaseAgent
from models.strategy import Strategy, IndicatorConfig, RiskConfig
from models.critique import Critique
from indicators.registry import INDICATOR_REGISTRY, get_param_ranges


class OptimizerAgent(BaseAgent):
    def __init__(self):
        super().__init__("OptimizerAgent")

    def run(self, strategy: Strategy, critique: Critique, **kwargs) -> Strategy:
        self.logger.info(
            f"Optimizing {strategy.id} (grade={critique.overall_grade}, "
            f"severity={critique.severity():.1f})"
        )
        new = strategy.clone()
        intensity = critique.severity()

        for suggestion in critique.suggestions:
            self._apply_suggestion(new, suggestion, intensity)

        # Always apply some random mutation for exploration
        if random.random() < 0.3 + intensity * 0.3:
            self._random_mutation(new, intensity)

        return new

    def _apply_suggestion(self, strategy: Strategy, suggestion: str, intensity: float):
        s = suggestion.lower()

        if "stop-loss" in s or "stop loss" in s:
            delta = random.uniform(0.002, 0.01) * intensity
            if "tighten" in s:
                strategy.risk.stop_loss_pct = max(0.005, strategy.risk.stop_loss_pct - delta)
            else:
                strategy.risk.stop_loss_pct = min(0.05, strategy.risk.stop_loss_pct + delta)

        elif "take-profit" in s or "take profit" in s:
            delta = random.uniform(0.005, 0.02) * intensity
            if "widen" in s or "increase" in s:
                strategy.risk.take_profit_pct += delta
            else:
                strategy.risk.take_profit_pct = max(0.01, strategy.risk.take_profit_pct - delta)

        elif "position size" in s or "reduce position" in s:
            strategy.risk.max_position_pct = max(
                0.02, strategy.risk.max_position_pct * (1 - 0.2 * intensity)
            )

        elif "indicator param" in s or "fine-tune" in s:
            self._mutate_indicator_params(strategy, intensity)

        elif "entry threshold" in s or "more selective" in s or "loosen" in s:
            self._mutate_entry_thresholds(strategy, intensity, direction="loosen" if "loosen" in s else "tighten")

        elif "template" in s:
            self._swap_indicator(strategy)

    def _update_rule_references(self, strategy: Strategy, old_col: str, new_col: str):
        """Update all rule params that reference old_col to new_col."""
        if old_col == new_col:
            return
        for rule in strategy.entry_rules + strategy.exit_rules:
            for key, val in rule.params.items():
                if isinstance(val, str) and val == old_col:
                    rule.params[key] = new_col

    def _mutate_indicator_params(self, strategy: Strategy, intensity: float):
        if not strategy.indicators:
            return
        ind = random.choice(strategy.indicators)
        old_col = ind.column_name()
        ranges = get_param_ranges(ind.name)

        # For multi-output indicators, collect old sub-column names
        old_key_val = list(ind.params.values())[0] if ind.params else ""

        for param, (lo, hi) in ranges.items():
            if param in ind.params:
                current = ind.params[param]
                if isinstance(current, int):
                    delta = max(1, int((hi - lo) * 0.15 * intensity))
                    ind.params[param] = max(lo, min(hi, current + random.randint(-delta, delta)))
                elif isinstance(current, float):
                    delta = (hi - lo) * 0.15 * intensity
                    ind.params[param] = round(max(lo, min(hi, current + random.uniform(-delta, delta))), 2)

        new_col = ind.column_name()
        new_key_val = list(ind.params.values())[0] if ind.params else ""

        # Update rule references for both single and multi-output indicators
        self._update_rule_references(strategy, old_col, new_col)
        # For multi-output (e.g., stoch_14 -> stoch_12, stoch_signal_14 -> stoch_signal_12)
        if str(old_key_val) != str(new_key_val):
            for rule in strategy.entry_rules + strategy.exit_rules:
                for key, val in rule.params.items():
                    if isinstance(val, str) and str(old_key_val) in val:
                        rule.params[key] = val.replace(str(old_key_val), str(new_key_val))

    def _mutate_entry_thresholds(self, strategy: Strategy, intensity: float, direction: str = "tighten"):
        for rule in strategy.entry_rules:
            if "value" in rule.params:
                val = rule.params["value"]
                if isinstance(val, (int, float)):
                    delta = abs(val) * 0.1 * intensity
                    if direction == "loosen":
                        if rule.type == "threshold_below":
                            rule.params["value"] = val + delta
                        else:
                            rule.params["value"] = val - delta
                    else:
                        if rule.type == "threshold_below":
                            rule.params["value"] = val - delta
                        else:
                            rule.params["value"] = val + delta

    def _swap_indicator(self, strategy: Strategy):
        if not strategy.indicators:
            return
        idx = random.randrange(len(strategy.indicators))
        old = strategy.indicators[idx]
        old_key_val = str(list(old.params.values())[0]) if old.params else ""

        candidates = [n for n in INDICATOR_REGISTRY if n != old.name and INDICATOR_REGISTRY[n]["output_type"] == "single"]
        if candidates:
            new_name = random.choice(candidates)
            ranges = get_param_ranges(new_name)
            params = {}
            for p, (lo, hi) in ranges.items():
                if isinstance(lo, int):
                    params[p] = random.randint(lo, hi)
                else:
                    params[p] = round(random.uniform(lo, hi), 2)
            new_ind = IndicatorConfig(name=new_name, params=params)
            strategy.indicators[idx] = new_ind

            # Update rule references: replace old indicator references with new ones
            new_col = new_ind.column_name()
            old_col = old.column_name()
            self._update_rule_references(strategy, old_col, new_col)
            # Also update multi-output references
            new_key_val = str(list(params.values())[0]) if params else ""
            for rule in strategy.entry_rules + strategy.exit_rules:
                for key, val in rule.params.items():
                    if isinstance(val, str) and old.name in val:
                        rule.params[key] = val.replace(old.name, new_name).replace(old_key_val, new_key_val)

    def _random_mutation(self, strategy: Strategy, intensity: float):
        action = random.choice(["params", "params", "risk"])  # weight towards params
        if action == "params":
            self._mutate_indicator_params(strategy, intensity)
        elif action == "risk":
            delta = random.uniform(0.002, 0.01) * intensity
            if random.random() > 0.5:
                strategy.risk.stop_loss_pct = max(0.005, min(0.05,
                    strategy.risk.stop_loss_pct + random.uniform(-delta, delta)))
            else:
                strategy.risk.take_profit_pct = max(0.01,
                    strategy.risk.take_profit_pct + random.uniform(-delta, delta * 2))
