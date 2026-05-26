"""Diversification analysis: ema_rsi (trend) + bb_pullback_daily (mean-reversion).

Runs both strategies on the full 2018-2024 TRAINING window (the same window
used to evaluate every other strategy in this project), aligns to daily
equity, and reports:
  - return correlation between the two strategies
  - blended-portfolio metrics at several weights (Sharpe, return, max DD)

We use the *daily* BB-pullback variant here because yfinance caps hourly data
at ~730 days, so the original hourly bb_pullback can't run before 2024.

Run: py -m trading_system.analyze_blend
"""

import numpy as np
import pandas as pd

from .backtester.engine import BacktestEngine
from .config import settings
from .marketdata import get_data_source, resolve_universe
from .strategies import discover_strategies


COMMON_START = settings.START_DATE   # "2018-01-01"
COMMON_END   = settings.END_DATE     # "2024-01-01"


def run_portfolio(strat) -> pd.Series:
    """Run a strategy across its universe and return the equal-weight portfolio
    equity curve indexed by bar."""
    symbols = resolve_universe(strat.universe)
    source  = get_data_source(strat.data_source)
    data    = source.get_history(symbols,
                                 start=strat.start_date or COMMON_START,
                                 end=strat.end_date     or COMMON_END,
                                 interval=strat.interval)
    capital_per_stock = strat.initial_capital / len(data)
    equity_curves = {}
    for symbol, df in data.items():
        eng = BacktestEngine(
            strategy=strat, data=df, symbol=symbol,
            initial_capital=capital_per_stock,
            position_size=strat.position_size,
        ).run()
        equity_curves[symbol] = eng.equity_curve['equity']

    frame = pd.DataFrame(equity_curves).sort_index()
    frame = frame.ffill().fillna(capital_per_stock)
    return frame.sum(axis=1)


def to_daily(eq: pd.Series) -> pd.Series:
    """Collapse hourly (or daily) equity to one value per IST trading day."""
    idx = pd.DatetimeIndex(eq.index)
    if idx.tz is not None:
        idx = idx.tz_convert("Asia/Kolkata")
    idx = idx.tz_localize(None)
    out = pd.Series(eq.values, index=idx).sort_index()
    return out.resample("1D").last().dropna()


def metrics(equity: pd.Series, label: str) -> dict:
    initial = equity.iloc[0]
    final   = equity.iloc[-1]
    ret = equity.pct_change().dropna()
    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    dd = ((equity - equity.cummax()) / equity.cummax()).min() * 100
    total_return = (final / initial - 1) * 100
    return {
        "label":   label,
        "return":  total_return,
        "sharpe":  sharpe,
        "max_dd":  dd,
    }


def main():
    classes = {c.__name__: c for c in discover_strategies()}

    print(f"\nRunning bb_pullback_daily on {COMMON_START}..{COMMON_END} ...")
    bb = classes['BBPullbackDaily']()
    bb_eq = run_portfolio(bb)

    print(f"Running ema_rsi on the same window ...")
    em = classes['EMARSI']()
    em_eq = run_portfolio(em)

    # Collapse both to a common daily index.
    bb_d = to_daily(bb_eq)
    em_d = to_daily(em_eq)
    joint = pd.DataFrame({"ema_rsi": em_d, "bb_pullback": bb_d}).dropna()
    print(f"\nAligned daily series: {len(joint)} days "
          f"({joint.index[0].date()} -> {joint.index[-1].date()})")

    # Correlation on daily returns.
    rets = joint.pct_change().dropna()
    corr = rets.corr().iloc[0, 1]
    print(f"\n*** Daily-return correlation: {corr:+.4f} ***")
    if corr < 0.2:
        print("   -> Genuinely uncorrelated. Diversification value is real.")
    elif corr < 0.5:
        print("   -> Mildly correlated. Some diversification benefit.")
    else:
        print("   -> Substantially correlated. Limited diversification value.")

    # Standalone metrics on the common window.
    print("\nStandalone (on the common 2024-06..2026-05 window):")
    print(f"  {'Strategy':<15} {'Return':>8}  {'Sharpe':>7}  {'Max DD':>8}")
    print(f"  {'-'*15} {'-'*8}  {'-'*7}  {'-'*8}")
    for col, m in [("ema_rsi", metrics(joint['ema_rsi'], "ema_rsi")),
                   ("bb_pullback", metrics(joint['bb_pullback'], "bb_pullback"))]:
        print(f"  {col:<15} {m['return']:>7.2f}%  {m['sharpe']:>7.3f}  {m['max_dd']:>7.2f}%")

    # Blended portfolio at various trend/MR weights.
    # Normalize each curve so they start at 1.0, then weighted-sum.
    norm = joint.div(joint.iloc[0])
    print("\nBlended portfolio (renormalised so each strategy starts at 1.0):")
    print(f"  {'Mix (trend/MR)':<15} {'Return':>8}  {'Sharpe':>7}  {'Max DD':>8}")
    print(f"  {'-'*15} {'-'*8}  {'-'*7}  {'-'*8}")
    for w_mr in [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0]:
        w_tr = 1 - w_mr
        blend = w_tr * norm['ema_rsi'] + w_mr * norm['bb_pullback']
        m = metrics(blend, f"{int(w_tr*100)}/{int(w_mr*100)}")
        print(f"  {int(w_tr*100):>3}/{int(w_mr*100):<3}        "
              f"{m['return']:>7.2f}%  {m['sharpe']:>7.3f}  {m['max_dd']:>7.2f}%")
    print()


if __name__ == "__main__":
    main()
