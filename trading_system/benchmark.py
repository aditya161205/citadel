"""Nifty 50 buy-and-hold benchmark for the paper-trading window.

What this is
------------
A pseudo-"strategy" that buys the Nifty 50 index (^NSEI on yfinance) at
the start of the paper-trading window and holds it to the end. The equity
curve is scaled to the same initial_capital used by the real strategies
(₹1 crore) so the numbers are directly comparable.

Why this matters
----------------
Walk-forward returns (e.g. ema_rsi +5.17%) are meaningless without a
benchmark. If Nifty 50 buy-and-hold did 16% over the same window, ema_rsi
is destroying value. If it did 2%, ema_rsi is genuinely picking up edge.

Output
------
Writes state/benchmark_nifty50_paper.json with the same shape as a strategy
state file (equity_history + a single "trade") so paper_report renders it
side-by-side with the strategies.

Run
---
  py -m trading_system.benchmark
  py -m trading_system.benchmark --reset
"""

from __future__ import annotations

import json
import sys
from datetime import date

import pandas as pd
import yfinance as yf

from .backtester.metrics import compute_metrics
from .config import settings
from .utils.logger import get_logger

log = get_logger("benchmark")

# yfinance symbol for the Nifty 50 index.
NIFTY50_SYMBOL = "^NSEI"


def fetch_nifty50(start: str, end: str) -> pd.DataFrame:
    """Pull Nifty 50 daily OHLCV for the paper window with retry on rate limits."""
    import time
    log.info("Fetching %s from %s to %s ...", NIFTY50_SYMBOL, start, end)
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
        log.warning("Benchmark fetch attempt %d failed -- retrying in %ds", attempt + 1, wait)
        time.sleep(wait)
    if last_err:
        raise last_err
    raise RuntimeError(f"No data for {NIFTY50_SYMBOL} -- check connectivity.")


def build_benchmark_equity(initial_capital: float,
                           start: str, end: str) -> tuple[pd.Series, dict]:
    """Buy at first bar's Close, mark-to-market every day, sell never.

    Returns (equity_series, summary_dict).
    """
    df = fetch_nifty50(start, end)
    entry_price = float(df['Close'].iloc[0])
    entry_date = df.index[0]

    # Fractional units (since you can't actually buy a fractional index, but
    # this is a benchmark accounting exercise -- it's how an index ETF would
    # work in practice, e.g. NIFTYBEES).
    units = initial_capital / entry_price

    equity = df['Close'] * units  # mark-to-market every day
    summary = {
        "symbol":         NIFTY50_SYMBOL,
        "entry_date":     str(entry_date.date()),
        "entry_price":    entry_price,
        "units":          units,
        "exit_date":      str(df.index[-1].date()),
        "exit_price":     float(df['Close'].iloc[-1]),
        "final_value":    float(equity.iloc[-1]),
        "total_return_pct": (float(equity.iloc[-1]) - initial_capital) / initial_capital * 100,
    }
    return equity, summary


def persist_benchmark_state(equity: pd.Series, summary: dict,
                            initial_capital: float):
    """Write a strategy-shaped state file so paper_report.py picks it up."""
    state = {
        "initial_capital": initial_capital,
        "cash":            0.0,    # fully invested
        "positions":       {NIFTY50_SYMBOL: summary['units']},
        "trades": [{
            "date":   summary['entry_date'],
            "symbol": NIFTY50_SYMBOL,
            "side":   "BUY",
            "price":  summary['entry_price'],
            "qty":    summary['units'],
            "cash":   0.0,
        }],
        "last_bars":      {NIFTY50_SYMBOL: summary['exit_date']},
        "equity_history": {str(ts.date()): float(v) for ts, v in equity.items()},
        "signal_log":     [],
        "benchmark_summary": summary,
    }
    settings.STATE_DIR.mkdir(parents=True, exist_ok=True)
    out = settings.STATE_DIR / "benchmark_nifty50_paper.json"
    out.write_text(json.dumps(state, indent=2, default=str))
    log.info("Benchmark state written to %s", out)
    return out


def main():
    reset = "--reset" in sys.argv
    state_path = settings.STATE_DIR / "benchmark_nifty50_paper.json"
    if reset and state_path.exists():
        state_path.unlink()

    start = settings.PAPER_START_DATE
    end = settings.PAPER_END_DATE or date.today().isoformat()
    initial = settings.INITIAL_CAPITAL

    equity, summary = build_benchmark_equity(initial, start, end)
    persist_benchmark_state(equity, summary, initial)

    metrics = compute_metrics(equity, initial)
    print("\n===== NIFTY 50 BUY-AND-HOLD BENCHMARK =====")
    print(f"  Window         : {summary['entry_date']} -> {summary['exit_date']}")
    print(f"  Entry price    : {summary['entry_price']:>12,.2f}")
    print(f"  Exit price     : {summary['exit_price']:>12,.2f}")
    print(f"  Units held     : {summary['units']:>12,.4f}")
    for k, v in metrics.items():
        print(f"  {k:<15}: {v}")
    print("=" * 50)


if __name__ == "__main__":
    main()
