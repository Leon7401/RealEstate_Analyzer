"""FX data provider with local CSV caching."""

from __future__ import annotations
import os
import pandas as pd
import yfinance as yf


CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "output", "cache")


class DataProvider:
    def __init__(self, cache_dir: str = CACHE_DIR):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

    def fetch(self, pair: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
        cache_path = os.path.join(
            self.cache_dir, f"{pair}_{start}_{end}_{interval}.csv"
        )
        if os.path.exists(cache_path):
            df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            if len(df) > 0:
                return df

        ticker = yf.Ticker(pair)
        df = ticker.history(start=start, end=end, interval=interval)

        if df.empty:
            raise ValueError(f"No data returned for {pair} from {start} to {end}")

        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.columns = ["open", "high", "low", "close", "volume"]
        df.index.name = "date"

        df.to_csv(cache_path)
        return df
