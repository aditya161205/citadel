"""Paper trading report — reads state/<name>.json and prints a summary.

Reports cover both kinds of paper-trading state:

  - "Live" state, written by main_live (real-time cycles)
  - "Walk-forward" state, written by paper_walkforward (back-replay over the
    out-of-sample test window). The walk-forward state files end in `_paper`,
    and a composite strategy also writes `_paper_combined.json`.

Usage
-----
  py -m trading_system.paper_report                       # all state files
  py -m trading_system.paper_report ema_rsi_paper         # one
  py -m trading_system.paper_report --signals             # also dump signal log
  py -m trading_system.paper_report --signals 50          # last 50 signals
"""

import json
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

from .backtester.metrics import compute_metrics
from .config import settings


def load_state(path: Path) -> dict:
    return json.loads(path.read_text())


def fetch_latest_prices(symbols: list[str]) -> dict[str, float]:
    """Get the latest closing price for a list of symbols."""
    if not symbols:
        return {}
    data = yf.download(symbols, period="5d", interval="1d",
                       auto_adjust=True, progress=False, group_by="ticker")
    prices = {}
    for sym in symbols:
        try:
            col = data[sym]["Close"].dropna()
            if not col.empty:
                prices[sym] = float(col.iloc[-1])
        except (KeyError, TypeError):
            pass
    return prices


def compute_avg_cost(trades: list, symbol: str) -> float:
    total_qty = 0
    total_cost = 0.0
    for t in trades:
        if t.get("symbol") != symbol:
            continue
        qty = int(float(t.get("qty", 0)))
        price = float(t.get("price", 0))
        if t.get("side") == "BUY":
            total_qty += qty
            total_cost += qty * price
        elif t.get("side") == "SELL":
            total_qty = 0
            total_cost = 0.0
    return total_cost / total_qty if total_qty > 0 else 0.0


def _composite_report(state_path: Path):
    """A composite blend writes a combined equity series + counts (no trades)."""
    state = load_state(state_path)
    name = state_path.stem
    eq_map = state.get("equity_history", {})
    eq = pd.Series({pd.Timestamp(k): float(v) for k, v in eq_map.items()}).sort_index()

    print(f"\n{'='*60}")
    print(f"  Paper Report (composite): [{name}]")
    print(f"{'='*60}")

    print(f"\n  Initial Capital : {state['initial_capital']:>15,.2f}")
    print(f"  Sub-strategies  : {state.get('sub_strategies', [])}")
    print(f"  Allocations     : {state.get('allocations', {})}")
    print(f"  Total trades    : {state.get('n_trades', 0)}")
    print(f"  Total signals   : {state.get('n_signals', 0)}")

    if eq.empty:
        print("  (no equity history)")
        return

    metrics = compute_metrics(eq, state['initial_capital'])
    print(f"\n  Walk-forward window: {eq.index[0].date()} -> {eq.index[-1].date()}")
    for k, v in metrics.items():
        print(f"    {k:<20}: {v}")
    print(f"\n{'='*60}\n")


def report_strategy(state_path: Path, show_signals: int = 0):
    state = load_state(state_path)
    if "sub_strategies" in state:
        _composite_report(state_path)
        return

    name = state_path.stem
    initial = state["initial_capital"]
    cash = state["cash"]
    positions = {s: int(float(q)) for s, q in state.get("positions", {}).items()
                 if float(q) > 0}
    trades = state.get("trades", [])
    equity_history = state.get("equity_history", {})
    signal_log = state.get("signal_log", [])

    print(f"\n{'='*60}")
    print(f"  Paper Report: [{name}]")
    print(f"{'='*60}")

    # If we have a recorded equity history (walk-forward or live with
    # mark_equity), prefer it — those are the true paper-trading metrics.
    if equity_history:
        eq = pd.Series(
            {pd.Timestamp(k): float(v) for k, v in equity_history.items()}
        ).sort_index()
        portfolio_value = float(eq.iloc[-1])
        total_pnl = portfolio_value - initial
        total_pnl_pct = (total_pnl / initial) * 100

        print(f"\n  Initial Capital    : {initial:>15,.2f}")
        print(f"  Latest Equity      : {portfolio_value:>15,.2f}")
        print(f"  Cash               : {cash:>15,.2f}")
        print(f"  Total P&L          : {total_pnl:>+15,.2f}  ({total_pnl_pct:+.2f}%)")
        print(f"  Window             : {eq.index[0].date()} -> {eq.index[-1].date()}")
        print(f"  Bars marked        : {len(eq)}")
        metrics = compute_metrics(eq, initial)
        print(f"  --- paper-trading metrics ---")
        for k, v in metrics.items():
            print(f"    {k:<22}: {v}")
    else:
        # Fallback: legacy state without equity history -- fetch live prices.
        prices = fetch_latest_prices(list(positions.keys())) if positions else {}
        holdings_value = sum(positions[s] * prices.get(s, 0) for s in positions)
        portfolio_value = cash + holdings_value
        total_pnl = portfolio_value - initial
        total_pnl_pct = (total_pnl / initial) * 100
        print(f"\n  Initial Capital    : {initial:>15,.2f}")
        print(f"  Cash               : {cash:>15,.2f}")
        print(f"  Holdings Value     : {holdings_value:>15,.2f}")
        print(f"  Portfolio Value    : {portfolio_value:>15,.2f}")
        print(f"  Total P&L          : {total_pnl:>+15,.2f}  ({total_pnl_pct:+.2f}%)")

    # Open positions.
    print(f"\n  Open Positions ({len(positions)}):")
    if not positions:
        print("    (none)")
    else:
        print(f"    {'Symbol':<20} {'Qty':>6} {'Avg Cost':>10}")
        print(f"    {'-'*18}  {'-'*6} {'-'*10}")
        for sym in sorted(positions):
            qty = positions[sym]
            avg = compute_avg_cost(trades, sym)
            print(f"    {sym:<20} {qty:>6} {avg:>10,.2f}")

    # Trades.
    print(f"\n  Trade History ({len(trades)} total):")
    if not trades:
        print("    (no trades yet)")
    else:
        show = trades[-20:]
        if len(trades) > 20:
            print(f"    (showing last 20 of {len(trades)})")
        print(f"    {'Date':<22} {'Side':<5} {'Symbol':<20} {'Qty':>6} {'Price':>10}")
        print(f"    {'-'*20}  {'-'*5} {'-'*18}  {'-'*6} {'-'*10}")
        for t in show:
            print(f"    {str(t.get('date','')):<22} {t.get('side',''):<5} "
                  f"{t.get('symbol',''):<20} {int(float(t.get('qty',0))):>6} "
                  f"{float(t.get('price',0)):>10,.2f}")

    # Signal log (the new thing the user explicitly asked for).
    print(f"\n  Signal Log ({len(signal_log)} entries):")
    if not signal_log:
        print("    (no signals recorded)")
    else:
        n = show_signals or 20
        show = signal_log[-n:]
        if len(signal_log) > n:
            print(f"    (showing last {n} of {len(signal_log)})")
        print(f"    {'Date':<22} {'Symbol':<18} {'Sig':>4} {'Price':>10} {'Action':<8}")
        print(f"    {'-'*22} {'-'*18} {'-'*4} {'-'*10} {'-'*8}")
        for s in show:
            sig = int(s.get('signal', 0))
            sig_str = f"{sig:+d}" if sig != 0 else "0"
            print(f"    {str(s.get('ts',''))[:22]:<22} "
                  f"{s.get('symbol',''):<18} {sig_str:>4} "
                  f"{float(s.get('price', 0)):>10,.2f} {s.get('action',''):<8}")

    # Cycle markers.
    last_bars = state.get("last_bars", {})
    if last_bars:
        dates = set(last_bars.values())
        print(f"\n  Last processed bar : {max(dates)}")

    print(f"\n{'='*60}\n")


def main():
    argv = sys.argv[1:]
    show_signals = 0
    if "--signals" in argv:
        idx = argv.index("--signals")
        # Optional count after --signals.
        if idx + 1 < len(argv) and argv[idx + 1].isdigit():
            show_signals = int(argv[idx + 1])
            del argv[idx:idx + 2]
        else:
            show_signals = 50
            del argv[idx]

    name_filter = argv[0] if argv else None

    state_dir = settings.STATE_DIR
    if not state_dir.exists():
        print("No state/ directory found. Run paper_walkforward or main_live first.")
        return

    state_files = sorted(state_dir.glob("*.json"))
    if not state_files:
        print("No state files found. Run `py -m trading_system.paper_walkforward` first.")
        return

    for path in state_files:
        if name_filter and path.stem != name_filter:
            continue
        report_strategy(path, show_signals=show_signals)


if __name__ == "__main__":
    main()
