"""Market-regime filter — gate trend-following entries by overall market state.

Rule
----
Compute two things on Nifty 50 daily closes:

  1. 200-day SMA trend gate: regime is "on" when Close > 200-day SMA.
  2. Realized-vol ceiling : regime is "on" only when 20-day annualized
     return-std is below a threshold (default 18% — historically Nifty's
     long-run realized vol sits around 14-22%; >18% has been a chop /
     drawdown marker).

Both conditions must hold. When the regime is "off" the walk-forward
simulator blocks NEW long entries (existing positions exit as normal so the
strategy can de-risk). Mean-reversion strategies opt out via the
respect_regime_filter class flag — chop is *good* for mean reversion.

Why this matters
----------------
Trend followers (ema_rsi, sma_crossover, macd_trend) die in chop. The
post-2024 OOS window had several extended chop periods that the regime
filter would have skipped. Strategies that survive in cash during chop and
re-deploy in trends should net out ahead.

This is a portfolio-level filter — strategies don't know about it. The
filter is computed once per walk-forward call and queried by timestamp.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache

import numpy as np
import pandas as pd
import yfinance as yf

from .config import settings
from .utils.logger import get_logger

log = get_logger("regime_filter")

NIFTY50_SYMBOL = "^NSEI"


def _fetch_nifty50_daily(start: str, end: str) -> pd.DataFrame:
    """Fetch Nifty 50 daily bars with retry against transient rate limiting."""
    import time
    last_err = None
    for attempt in range(3):
        try:
            df = yf.download(NIFTY50_SYMBOL, start=start, end=end, interval="1d",
                             auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if not df.empty:
                return df
        except Exception as e:
            last_err = e
        wait = 5 * (attempt + 1)
        log.warning("Nifty 50 fetch attempt %d failed -- retrying in %ds", attempt + 1, wait)
        time.sleep(wait)
    if last_err:
        raise last_err
    return pd.DataFrame()


def build_regime_series(start: str, end: str,
                        sma_window: int = 200,
                        vol_window: int = 20,
                        vol_threshold: float = 0.18) -> pd.Series:
    """Build a daily boolean series: True = regime ON (entries allowed).

    Indexed by date (no time-of-day). Fetches enough Nifty history *before*
    `start` that the 200-SMA is settled by the first bar in the window.
    """
    # Pad enough calendar days before `start` so the 200-SMA is settled.
    pad_days = int(sma_window * 1.6) + 30
    fetch_start = (pd.Timestamp(start) - pd.Timedelta(days=pad_days)).strftime(
        "%Y-%m-%d")
    log.info("Building regime series Nifty 50: %s -> %s (pad %dd, sma=%d, vol_w=%d, vol<%.0f%%)",
             fetch_start, end, pad_days, sma_window, vol_window, vol_threshold * 100)
    df = _fetch_nifty50_daily(fetch_start, end)
    if df.empty:
        raise RuntimeError("Couldn't fetch Nifty 50 for regime filter.")

    close = df['Close']
    sma = close.rolling(sma_window).mean()
    rets = close.pct_change()
    # Annualized realized vol (252 trading days).
    rvol = rets.rolling(vol_window).std() * np.sqrt(252)

    trend_on = close > sma
    vol_ok = rvol < vol_threshold
    regime = (trend_on & vol_ok).fillna(False)

    # Strip pre-`start` rows -- the user only cares about the paper window.
    regime = regime.loc[regime.index >= pd.Timestamp(start)]
    # Date-only index for cheap lookups.
    regime.index = regime.index.normalize()

    n_on = int(regime.sum())
    n_total = len(regime)
    pct_on = (n_on / n_total * 100) if n_total else 0.0
    log.info("Regime ON %d / %d days (%.1f%%) over %s -> %s",
             n_on, n_total, pct_on,
             regime.index.min().date(), regime.index.max().date())
    return regime


class RegimeFilter:
    """Lookup-by-timestamp wrapper around the daily regime series."""

    def __init__(self, regime: pd.Series):
        self._regime = regime

    @classmethod
    def for_paper_window(cls) -> "RegimeFilter":
        start = settings.PAPER_START_DATE
        end = settings.PAPER_END_DATE or date.today().isoformat()
        regime = build_regime_series(
            start, end,
            sma_window=getattr(settings, "REGIME_SMA_WINDOW", 200),
            vol_window=getattr(settings, "REGIME_VOL_WINDOW", 20),
            vol_threshold=getattr(settings, "REGIME_VOL_THRESHOLD", 0.18),
        )
        return cls(regime)

    def is_on(self, ts) -> bool:
        """Return True if the regime allows new entries on the given timestamp."""
        ts = pd.Timestamp(ts)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
        ts = ts.normalize()
        # Find the most recent regime entry <= ts (ffill semantics).
        try:
            idx = self._regime.index.searchsorted(ts, side='right') - 1
            if idx < 0:
                return False
            return bool(self._regime.iloc[idx])
        except Exception:
            return False

    @property
    def series(self) -> pd.Series:
        return self._regime

    def summary(self) -> dict:
        s = self._regime
        return {
            "days_total": int(len(s)),
            "days_on":    int(s.sum()),
            "days_off":   int((~s).sum()),
            "pct_on":     float(s.mean() * 100) if len(s) else 0.0,
            "first":      str(s.index.min().date()) if len(s) else None,
            "last":       str(s.index.max().date()) if len(s) else None,
        }
