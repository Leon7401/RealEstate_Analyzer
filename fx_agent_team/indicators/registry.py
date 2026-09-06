"""Indicator registry mapping names to ta library classes."""

from __future__ import annotations
import pandas as pd
import ta


INDICATOR_REGISTRY = {
    "RSI": {
        "builder": lambda df, **p: ta.momentum.RSIIndicator(df["close"], **p).rsi(),
        "params": {"window": (5, 30)},
        "output_type": "single",
    },
    "SMA": {
        "builder": lambda df, **p: ta.trend.SMAIndicator(df["close"], **p).sma_indicator(),
        "params": {"window": (10, 200)},
        "output_type": "single",
    },
    "EMA": {
        "builder": lambda df, **p: ta.trend.EMAIndicator(df["close"], **p).ema_indicator(),
        "params": {"window": (10, 200)},
        "output_type": "single",
    },
    "MACD": {
        "builder": lambda df, **p: _build_macd(df, **p),
        "params": {"window_slow": (20, 30), "window_fast": (8, 15), "window_sign": (5, 12)},
        "output_type": "multi",
    },
    "BollingerBands": {
        "builder": lambda df, **p: _build_bbands(df, **p),
        "params": {"window": (10, 30), "window_dev": (1.5, 3.0)},
        "output_type": "multi",
    },
    "ATR": {
        "builder": lambda df, **p: ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], **p
        ).average_true_range(),
        "params": {"window": (7, 21)},
        "output_type": "single",
    },
    "Stochastic": {
        "builder": lambda df, **p: _build_stochastic(df, **p),
        "params": {"window": (7, 21), "smooth_window": (3, 7)},
        "output_type": "multi",
    },
}


def _build_macd(df: pd.DataFrame, **params) -> dict[str, pd.Series]:
    m = ta.trend.MACD(df["close"], **params)
    return {"macd": m.macd(), "macd_signal": m.macd_signal(), "macd_diff": m.macd_diff()}


def _build_bbands(df: pd.DataFrame, **params) -> dict[str, pd.Series]:
    bb = ta.volatility.BollingerBands(df["close"], **params)
    return {"bb_mid": bb.bollinger_mavg(), "bb_upper": bb.bollinger_hband(), "bb_lower": bb.bollinger_lband()}


def _build_stochastic(df: pd.DataFrame, **params) -> dict[str, pd.Series]:
    s = ta.momentum.StochasticOscillator(df["high"], df["low"], df["close"], **params)
    return {"stoch": s.stoch(), "stoch_signal": s.stoch_signal()}


def apply_indicator(name: str, df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Apply an indicator to the dataframe, adding columns with canonical names."""
    reg = INDICATOR_REGISTRY[name]
    result = reg["builder"](df, **params)

    if reg["output_type"] == "single":
        key_val = list(params.values())[0] if params else ""
        col_name = f"{name}_{key_val}"
        df[col_name] = result
    else:
        for sub_name, series in result.items():
            key_val = list(params.values())[0] if params else ""
            col_name = f"{sub_name}_{key_val}"
            df[col_name] = series
    return df


def get_param_ranges(name: str) -> dict[str, tuple]:
    return INDICATOR_REGISTRY[name]["params"]
