import math
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from .base import DataSource

# yfinance period caps by interval (approximate safe limits).
_PERIOD_CAPS = {
    "1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d",
    "30m": "60d", "60m": "730d", "90m": "60d",
}


class YahooDataSource(DataSource):
    """Fetch OHLCV from Yahoo Finance (via yfinance)."""

    # -- backtest path --
    def get_history(self, symbols, start, end, interval="1d"):
        raw = yf.download(
            symbols, start=start, end=end, interval=interval,
            auto_adjust=True, progress=False, group_by="ticker", threads=True,
        )
        return self._split(raw, symbols)

    # -- paper / live path --
    def get_recent(self, symbols, interval="1d", warmup=200):
        if interval in _PERIOD_CAPS:
            period = _PERIOD_CAPS[interval]
        else:
            # Daily: warmup trading days ≈ warmup * 1.5 calendar days + buffer.
            days = math.ceil(warmup * 1.5) + 30
            period = f"{days}d"

        raw = yf.download(
            symbols, period=period, interval=interval,
            auto_adjust=True, progress=False, group_by="ticker", threads=True,
        )
        return self._split(raw, symbols)

    # -- shared helper --
    @staticmethod
    def _split(raw, symbols):
        """Slice a grouped yf.download result into {symbol: DataFrame}."""
        data = {}
        for sym in symbols:
            try:
                df = raw[sym].dropna(how="all")
            except KeyError:
                continue
            if df.empty:
                continue
            # Flatten MultiIndex columns if present.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index.name = "date"
            data[sym] = df
        return data
