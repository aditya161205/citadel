"""Shared technical-indicator helpers.

Centralises calculations that multiple strategies need (RSI, EMA, ATR, Bollinger).
Wilder smoothing is used wherever it's the textbook convention (RSI, ATR).
"""

import pandas as pd


def ema(series: pd.Series, span: int, min_periods: int | None = None) -> pd.Series:
    """Exponential moving average with adjust=False (the standard finance convention)."""
    return series.ewm(span=span, adjust=False,
                      min_periods=min_periods if min_periods is not None else span
                      ).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI (0-100). Uses EMA with alpha = 1/period (= Wilder smoothing)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series,
        period: int = 14) -> pd.Series:
    """Wilder's Average True Range. Measures volatility in price units."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def bollinger(close: pd.Series, period: int = 20,
              num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (middle, upper, lower) Bollinger Bands."""
    middle = close.rolling(period).mean()
    std    = close.rolling(period).std(ddof=0)
    upper  = middle + num_std * std
    lower  = middle - num_std * std
    return middle, upper, lower
