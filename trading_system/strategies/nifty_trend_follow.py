"""Nifty-tracker with a regime kill switch.

What
----
Pure beta exposure to the Nifty 50, except cash when the regime filter is
off. Implemented as a portfolio strategy holding a single name -- the
NIFTYBEES.NS ETF -- which physically tracks Nifty 50.

  regime ON  -> 100% NIFTYBEES.NS
  regime OFF -> 100% cash (earns risk-free rate)

Why
---
Buy-and-hold Nifty 50 over 2024-2026 returned 9.99% but ate a -15.77%
drawdown. We can do better by stepping out of the way when the regime
filter says "down market or chop". The cost is missing some recoveries
(re-entry whipsaw); the benefit is a much smaller drawdown.

This is the simplest possible "trend follow the market itself" strategy:
no stock selection, no signal engineering, just on/off based on the macro
regime. Acts as the beta sleeve in the smart_blend composite.
"""

from __future__ import annotations

import pandas as pd

from ..backtester.strategy_base import StrategyBase

NIFTY_ETF = "NIFTYBEES.NS"


class NiftyTrendFollow(StrategyBase):
    name            = "nifty_trend_follow"
    interval        = "1d"
    # Force the universe to just the ETF.
    universe        = [NIFTY_ETF]
    initial_capital = 10_000_000.0
    warmup          = 5

    is_portfolio_strategy = True
    rebalance_freq        = "1W"      # weekly, to react to regime flips faster
    respect_regime_filter = True      # the WHOLE point of this strategy

    def generate_target_portfolio(self, data: dict, ts) -> dict[str, float]:
        """Hold 100% NIFTYBEES.NS. The regime filter in the simulator will
        force this to {} (full cash) automatically when conditions are bad."""
        if NIFTY_ETF in data:
            return {NIFTY_ETF: 1.0}
        return {}
