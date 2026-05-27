"""Cross-sectional momentum -- the academic factor.

What
----
Every month (default), rank the Nifty 100 universe by their trailing
6-month price return (excluding the most recent month, to dodge short-term
mean-reversion). Hold the top N equal-weight, rebalance, repeat.

Why
---
Cross-sectional momentum is the single most consistent return source in
equity markets, documented in every major market since the 1990s
(Jegadeesh & Titman 1993, Asness et al. for India). It works because:

  1. Winners keep winning (under-reaction to good news / persistent flows)
  2. It's a *relative* strength signal, so it stays invested in *something*
     regardless of overall market direction -- unlike time-series momentum
     (which is what all our other strategies are) that goes flat in chop.

This is the missing factor in our suite. The existing strategies are all
"does THIS stock look good?" -- this asks "which stocks look BEST?" across
the universe and concentrates capital into them.

Parameters
----------
  lookback_months : 6   (skip last month to avoid short-term reversal)
  skip_months     : 1
  top_n           : 20  (out of ~100 -- concentrated but not insanely so)
  rebalance_freq  : "1M"
  vol_scale       : True  (inverse-vol weight inside the top-N)

Expected behaviour
------------------
Stays ~100% invested most of the time, low drawdown in mild chop, painful
drawdown in market-wide crashes (the factor is long-only, fully exposed to
beta). The regime filter overlay (default ON) takes it to cash when the
broader market regime turns risk-off -- giving us asymmetric exposure:
beta when good, cash when bad.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtester.strategy_base import StrategyBase


class CrossSectionalMomentum(StrategyBase):
    name            = "xsec_momentum"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0
    warmup          = 200             # need ~7 months of daily bars

    is_portfolio_strategy = True
    rebalance_freq        = "1M"      # first trading day of each month
    # Trend-style strategy on top -- respects the regime filter by default.
    respect_regime_filter = True

    # Lookback config.
    LOOKBACK_MONTHS = 6
    SKIP_MONTHS     = 1               # drop last month (avoid short-term reversal)
    TOP_N           = 20
    VOL_SCALE       = True            # inverse-vol weighting within top-N

    # Liquidity filter: drop names whose 20-day average traded value < this.
    MIN_AVG_TRADED_VALUE = 50_000_000  # ~5 cr/day (sanity floor)

    def __init__(self):
        # No instance config so the discovery loop instantiates fresh
        # objects per slice with class defaults.
        pass

    def _momentum_score(self, df: pd.DataFrame, ts) -> float | None:
        """Return on the [ts-lookback, ts-skip] window, or None if not enough data."""
        df = df.loc[df.index <= ts]
        if df.empty:
            return None
        end_idx = df.index[-1]
        # Approximate "skip_months" by 21 trading days, "lookback" by ~21*N.
        skip_bars = self.SKIP_MONTHS * 21
        lookback_bars = self.LOOKBACK_MONTHS * 21
        if len(df) < skip_bars + lookback_bars + 5:
            return None
        end_pos = len(df) - skip_bars - 1
        start_pos = end_pos - lookback_bars
        if start_pos < 0:
            return None
        end_close = float(df['Close'].iloc[end_pos])
        start_close = float(df['Close'].iloc[start_pos])
        if start_close <= 0:
            return None
        return end_close / start_close - 1.0

    def _realized_vol(self, df: pd.DataFrame, ts, window: int = 60) -> float | None:
        df = df.loc[df.index <= ts]
        if len(df) < window + 1:
            return None
        rets = df['Close'].pct_change().tail(window)
        v = float(rets.std() * np.sqrt(252))
        return v if v > 0 else None

    def _passes_liquidity(self, df: pd.DataFrame, ts) -> bool:
        df = df.loc[df.index <= ts].tail(20)
        if df.empty or 'Volume' not in df.columns:
            return True
        avg_value = (df['Close'] * df['Volume']).mean()
        return avg_value >= self.MIN_AVG_TRADED_VALUE

    def generate_target_portfolio(self, data: dict, ts) -> dict[str, float]:
        """Rank universe by momentum, hold top N, optionally inverse-vol weighted."""
        scores: dict[str, float] = {}
        vols: dict[str, float] = {}
        for sym, df in data.items():
            if not self._passes_liquidity(df, ts):
                continue
            m = self._momentum_score(df, ts)
            if m is None:
                continue
            scores[sym] = m
            v = self._realized_vol(df, ts)
            if v is not None:
                vols[sym] = v

        if not scores:
            return {}

        # Pick top-N by momentum, filter out negative momentum (long-only
        # factor -- don't hold names that have already started losing).
        sorted_syms = sorted(scores, key=scores.get, reverse=True)
        positive_syms = [s for s in sorted_syms if scores[s] > 0]
        chosen = positive_syms[:self.TOP_N]
        if not chosen:
            return {}

        if self.VOL_SCALE and all(s in vols for s in chosen):
            # Inverse-vol weights, normalized to sum to 1.
            inv = {s: 1.0 / vols[s] for s in chosen}
            total = sum(inv.values())
            weights = {s: inv[s] / total for s in chosen}
        else:
            w = 1.0 / len(chosen)
            weights = {s: w for s in chosen}

        return weights
