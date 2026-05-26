"""80% ema_rsi (trend) + 20% bb_pullback_daily (mean-reversion) -- a composite.

This isn't a "normal" single-signal strategy: it's a *portfolio* that holds two
sub-strategies, each running on its allocated capital share, with the combined
equity curve = sum of the two sub-curves.

The two sub-strategies use different engine paths (signal-based vs ATR-stop) so
they can't be folded into one signal stream. Instead, this file declares
`is_composite = True`, which main_backtest detects and routes to `run_blend()`
instead of the standard per-stock engine loop.

Approach in plain English:
  - 80% of capital (80 lakh) goes to ema_rsi (trend follower)
  - 20% (20 lakh) goes to bb_pullback_daily (mean reverter)
  - Each sub-strategy runs the full Nifty 100 with its proportional capital
  - The combined equity = sum of the two sub-equity curves over time
"""

import pandas as pd

from ..backtester.engine import BacktestEngine
from ..backtester.strategy_base import StrategyBase
from ..config import settings
from ..marketdata import get_data_source, resolve_universe


class BlendTrendMR(StrategyBase):
    name            = "blend_trend_mr"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0

    # Tells main_backtest to call run_blend() instead of the per-stock engine loop.
    is_composite = True

    # Capital allocation between sub-strategies (must sum to 1.0).
    ALLOCATIONS: dict[str, float] = {
        "ema_rsi":           0.80,
        "bb_pullback_daily": 0.20,
    }

    # Sub-strategies use their own start_date/end_date attributes
    # (or fall back to settings.START_DATE/END_DATE if unset).

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        # Composite strategies don't use the per-stock signal path.
        raise NotImplementedError(
            "blend_trend_mr is a composite strategy; main_backtest routes it through "
            "run_blend() instead of generate_signals()."
        )

    # -- composite entry point --

    def run_blend(self):
        """Run each sub-strategy on its proportional capital and return:
            (combined_equity: pd.Series, per_strategy_equity: dict[str, pd.Series])
        """
        # Avoid circular import (strategies/__init__.py imports this module).
        from . import discover_strategies

        # Build a {strategy_name -> class} registry of non-composite strategies.
        registry = {}
        for cls in discover_strategies():
            if getattr(cls, "is_composite", False):
                continue
            registry[cls().name] = cls

        per_strategy_equity: dict[str, pd.Series] = {}
        for sub_name, weight in self.ALLOCATIONS.items():
            if sub_name not in registry:
                raise RuntimeError(
                    f"Sub-strategy {sub_name!r} not found among discovered strategies."
                )
            sub_strategy = registry[sub_name]()
            sub_initial  = self.initial_capital * weight
            print(f"  -> {sub_name:<22} weight={weight:.0%}  capital={sub_initial:,.0f}")
            per_strategy_equity[sub_name] = self._run_sub(sub_strategy, sub_initial)

        # Align all sub-equity curves on a common (sorted, ffilled) date index.
        frame = pd.DataFrame(per_strategy_equity).sort_index().ffill()
        # Backfill any leading NaNs with each strategy's initial capital so the
        # combined series starts at the full initial_capital value.
        for name in frame.columns:
            sub_initial = self.initial_capital * self.ALLOCATIONS[name]
            frame[name] = frame[name].fillna(sub_initial)
        combined = frame.sum(axis=1)
        return combined, per_strategy_equity

    def _run_sub(self, sub_strategy: StrategyBase,
                 sub_initial_capital: float) -> pd.Series:
        """Replicate main_backtest.run_strategy's per-stock loop but with the
        sub-strategy's allocated capital. Returns a portfolio equity series."""
        symbols = resolve_universe(sub_strategy.universe)
        source  = get_data_source(sub_strategy.data_source)
        start = sub_strategy.start_date or settings.START_DATE
        end   = sub_strategy.end_date   or settings.END_DATE
        data  = source.get_history(symbols, start=start, end=end,
                                   interval=sub_strategy.interval)
        if not data:
            return pd.Series(dtype=float)

        cap_per_stock = sub_initial_capital / len(data)
        equity_curves: dict[str, pd.Series] = {}
        for symbol, df in data.items():
            engine = BacktestEngine(
                strategy=sub_strategy, data=df, symbol=symbol,
                initial_capital=cap_per_stock,
                position_size=sub_strategy.position_size,
            ).run()
            equity_curves[symbol] = engine.equity_curve['equity']

        frame = pd.DataFrame(equity_curves).sort_index().ffill().fillna(cap_per_stock)
        return frame.sum(axis=1)
