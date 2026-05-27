"""Smart blend: factor + trend + beta, diversified for return AND drawdown.

Allocation
----------
  50% xsec_momentum       (cross-sectional factor — the return engine)
  30% sma_crossover       (most consistent trend follower across rolling slices)
  20% nifty_trend_follow  (beta sleeve with regime kill switch)

Why this mix
------------
Each sleeve makes money in a different regime:

  - xsec_momentum captures dispersion between winners and losers — works
    even in flat markets, gets hurt in market-wide crashes.
  - sma_crossover captures sustained trends in individual names — wins in
    bull / bear runs, sits in cash during chop.
  - nifty_trend_follow captures pure market beta when the regime is on,
    sits in cash when off.

The three sleeves have low pairwise return correlation, so the blended
equity curve smooths the worst of each strategy's drawdowns while keeping
most of the upside.

Implementation
--------------
Like blend_trend_mr: declares is_composite=True and run_blend() runs each
sub-strategy on its allocated capital. The walk-forward simulator and the
backtest engine both detect is_composite and route accordingly.
"""

from __future__ import annotations

import pandas as pd

from ..backtester.engine import BacktestEngine
from ..backtester.strategy_base import StrategyBase
from ..config import settings
from ..marketdata import get_data_source, resolve_universe


class SmartBlend(StrategyBase):
    name            = "smart_blend"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0

    is_composite = True

    ALLOCATIONS: dict[str, float] = {
        "xsec_momentum":       0.50,
        "sma_crossover":       0.30,
        "nifty_trend_follow":  0.20,
    }

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError(
            "smart_blend is a composite strategy — routed through run_blend().")

    # -- backtester composite path (run_blend, called by main_backtest) --

    def run_blend(self):
        """Run each sub-strategy on its proportional capital and combine."""
        from . import discover_strategies
        registry = {cls().name: cls for cls in discover_strategies()
                    if not getattr(cls, "is_composite", False)}

        per_strategy_equity: dict[str, pd.Series] = {}
        for sub_name, weight in self.ALLOCATIONS.items():
            if sub_name not in registry:
                raise RuntimeError(f"Sub-strategy {sub_name!r} not found.")
            sub = registry[sub_name]()
            sub_initial = self.initial_capital * weight
            print(f"  -> {sub_name:<22} weight={weight:.0%}  capital={sub_initial:,.0f}")
            per_strategy_equity[sub_name] = self._run_sub(sub, sub_initial)

        frame = pd.DataFrame(per_strategy_equity).sort_index().ffill()
        for n in frame.columns:
            frame[n] = frame[n].fillna(self.initial_capital * self.ALLOCATIONS[n])
        return frame.sum(axis=1), per_strategy_equity

    def _run_sub(self, sub: StrategyBase, sub_initial: float) -> pd.Series:
        """Replicate main_backtest.run_strategy for a single sub-strategy.

        Portfolio strategies don't fit the per-stock backtest path — the
        backtester only knows about per-stock engines. For now we just skip
        portfolio sub-strategies in the legacy backtest. The walk-forward
        composite path handles them correctly via run_walkforward.
        """
        if getattr(sub, "is_portfolio_strategy", False):
            # Defer to paper_walkforward at run-time; can't backtest this
            # via the per-stock engine cleanly. Return an empty series.
            return pd.Series(dtype=float)

        symbols = resolve_universe(sub.universe)
        source  = get_data_source(sub.data_source)
        start = sub.start_date or settings.START_DATE
        end   = sub.end_date   or settings.END_DATE
        data  = source.get_history(symbols, start=start, end=end,
                                   interval=sub.interval)
        if not data:
            return pd.Series(dtype=float)

        cap_per_stock = sub_initial / len(data)
        curves: dict[str, pd.Series] = {}
        for sym, df in data.items():
            engine = BacktestEngine(
                strategy=sub, data=df, symbol=sym,
                initial_capital=cap_per_stock, position_size=sub.position_size,
            ).run()
            curves[sym] = engine.equity_curve['equity']
        frame = pd.DataFrame(curves).sort_index().ffill().fillna(cap_per_stock)
        return frame.sum(axis=1)
