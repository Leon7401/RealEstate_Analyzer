"""Strategy generation agent."""

from __future__ import annotations
import random
from agents.base_agent import BaseAgent
from models.strategy import Strategy, IndicatorConfig, RuleConfig, RiskConfig
from indicators.registry import INDICATOR_REGISTRY, get_param_ranges


# Strategy templates that combine indicators with matching entry/exit rules
TEMPLATES = [
    {
        "name": "MA_Crossover",
        "indicators": [
            {"name": "SMA", "param_key": "window", "values": [("fast", 20, 50), ("slow", 100, 200)]},
            {"name": "RSI", "param_key": "window", "values": [("filter", 10, 20)]},
        ],
        "entry_rules": [
            {"type": "cross_above", "params_tpl": {"fast": "SMA_{fast}", "slow": "SMA_{slow}"}},
            {"type": "threshold_below", "params_tpl": {"indicator": "RSI_{filter}", "value": 70}},
        ],
        "exit_rules": [
            {"type": "cross_below", "params_tpl": {"fast": "SMA_{fast}", "slow": "SMA_{slow}"}},
        ],
    },
    {
        "name": "RSI_MeanReversion",
        "indicators": [
            {"name": "RSI", "param_key": "window", "values": [("rsi", 10, 25)]},
            {"name": "BollingerBands", "param_key": "window", "values": [("bb", 15, 25)]},
        ],
        "entry_rules": [
            {"type": "threshold_below", "params_tpl": {"indicator": "RSI_{rsi}", "value": 30}},
        ],
        "exit_rules": [
            {"type": "threshold_above", "params_tpl": {"indicator": "RSI_{rsi}", "value": 70}},
        ],
    },
    {
        "name": "MACD_Trend",
        "indicators": [
            {"name": "MACD", "param_key": "window_slow", "values": [("macd", 22, 28)]},
            {"name": "EMA", "param_key": "window", "values": [("trend", 50, 200)]},
        ],
        "entry_rules": [
            {"type": "threshold_above", "params_tpl": {"indicator": "macd_diff_{macd}", "value": 0}},
            {"type": "threshold_above", "params_tpl": {"indicator": "close", "value": 0}},
        ],
        "exit_rules": [
            {"type": "threshold_below", "params_tpl": {"indicator": "macd_diff_{macd}", "value": 0}},
        ],
    },
    {
        "name": "Bollinger_Breakout",
        "indicators": [
            {"name": "BollingerBands", "param_key": "window", "values": [("bb", 15, 25)]},
            {"name": "ATR", "param_key": "window", "values": [("atr", 10, 20)]},
        ],
        "entry_rules": [
            {"type": "band_breakout", "params_tpl": {"upper_band": "bb_upper_{bb}"}},
        ],
        "exit_rules": [
            {"type": "band_breakdown", "params_tpl": {"lower_band": "bb_lower_{bb}"}},
        ],
    },
    {
        "name": "Stochastic_RSI",
        "indicators": [
            {"name": "Stochastic", "param_key": "window", "values": [("stoch", 10, 18)]},
            {"name": "RSI", "param_key": "window", "values": [("rsi", 10, 20)]},
        ],
        "entry_rules": [
            {"type": "threshold_below", "params_tpl": {"indicator": "stoch_{stoch}", "value": 20}},
            {"type": "threshold_below", "params_tpl": {"indicator": "RSI_{rsi}", "value": 35}},
        ],
        "exit_rules": [
            {"type": "threshold_above", "params_tpl": {"indicator": "stoch_{stoch}", "value": 80}},
        ],
    },
]


class StrategyAgent(BaseAgent):
    def __init__(self):
        super().__init__("StrategyAgent")

    def run(self, pair: str = "EURUSD=X", **kwargs) -> Strategy:
        template = random.choice(TEMPLATES)
        self.logger.info(f"Generating strategy from template: {template['name']}")

        # Resolve indicator parameters
        resolved = {}
        indicators = []
        for ind_spec in template["indicators"]:
            for label, lo, hi in ind_spec["values"]:
                val = random.randint(lo, hi)
                resolved[label] = val
                params = {ind_spec["param_key"]: val}
                # Add additional default params for multi-param indicators
                if ind_spec["name"] == "MACD":
                    params.setdefault("window_fast", random.randint(8, 15))
                    params.setdefault("window_sign", random.randint(5, 12))
                elif ind_spec["name"] == "BollingerBands":
                    params.setdefault("window_dev", round(random.uniform(1.5, 3.0), 1))
                elif ind_spec["name"] == "Stochastic":
                    params.setdefault("smooth_window", random.randint(3, 7))
                indicators.append(IndicatorConfig(name=ind_spec["name"], params=params))

        # Resolve rules
        entry_rules = []
        for rule_tpl in template["entry_rules"]:
            params = {}
            for k, v in rule_tpl["params_tpl"].items():
                if isinstance(v, str):
                    for label, val in resolved.items():
                        v = v.replace(f"{{{label}}}", str(val))
                params[k] = v
            entry_rules.append(RuleConfig(type=rule_tpl["type"], params=params))

        exit_rules = []
        for rule_tpl in template["exit_rules"]:
            params = {}
            for k, v in rule_tpl["params_tpl"].items():
                if isinstance(v, str):
                    for label, val in resolved.items():
                        v = v.replace(f"{{{label}}}", str(val))
                params[k] = v
            exit_rules.append(RuleConfig(type=rule_tpl["type"], params=params))

        # Random risk params
        sl = round(random.uniform(0.01, 0.04), 3)
        risk = RiskConfig(
            stop_loss_pct=sl,
            take_profit_pct=round(sl * random.uniform(1.5, 4.0), 3),
            max_position_pct=round(random.uniform(0.05, 0.15), 2),
            max_drawdown_pct=round(random.uniform(0.10, 0.20), 2),
        )

        return Strategy(
            indicators=indicators,
            entry_rules=entry_rules,
            exit_rules=exit_rules,
            risk=risk,
            pair=pair,
        )
