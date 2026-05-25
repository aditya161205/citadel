"""Paper trading report — run anytime without stopping the live loop.

Usage:  py -m trading_system.paper_report
        py -m trading_system.paper_report sma_crossover   (specific strategy)
"""

import json
import sys
from pathlib import Path

import yfinance as yf

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
    """Compute the average buy price for a currently held symbol from the trade log."""
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
            # Reset on full sell (equal-weight model sells all).
            total_qty = 0
            total_cost = 0.0
    return total_cost / total_qty if total_qty > 0 else 0.0


def report_strategy(state_path: Path):
    state = load_state(state_path)
    name = state_path.stem
    initial = state["initial_capital"]
    cash = state["cash"]
    positions = {s: int(float(q)) for s, q in state.get("positions", {}).items() if float(q) > 0}
    trades = state.get("trades", [])

    print(f"\n{'='*60}")
    print(f"  Paper Report: [{name}]")
    print(f"{'='*60}")

    # Fetch live prices for held symbols.
    prices = fetch_latest_prices(list(positions.keys())) if positions else {}

    # Portfolio value.
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
        print(f"    {'Symbol':<20} {'Qty':>6} {'Avg Cost':>10} {'Price':>10} {'P&L':>12}")
        print(f"    {'-'*18}  {'-'*6} {'-'*10} {'-'*10} {'-'*12}")
        for sym in sorted(positions):
            qty = positions[sym]
            avg = compute_avg_cost(trades, sym)
            price = prices.get(sym, 0)
            pnl = (price - avg) * qty
            print(f"    {sym:<20} {qty:>6} {avg:>10,.2f} {price:>10,.2f} {pnl:>+12,.2f}")

    # Trade history.
    print(f"\n  Trade History ({len(trades)} total):")
    if not trades:
        print("    (no trades yet)")
    else:
        # Show last 20 trades.
        show = trades[-20:]
        if len(trades) > 20:
            print(f"    (showing last 20 of {len(trades)})")
        print(f"    {'Date':<22} {'Side':<5} {'Symbol':<20} {'Qty':>6} {'Price':>10}")
        print(f"    {'-'*20}  {'-'*5} {'-'*18}  {'-'*6} {'-'*10}")
        for t in show:
            print(f"    {str(t.get('date','')):<22} {t.get('side',''):<5} "
                  f"{t.get('symbol',''):<20} {int(float(t.get('qty',0))):>6} "
                  f"{float(t.get('price',0)):>10,.2f}")

    # Days active.
    last_bars = state.get("last_bars", {})
    if last_bars:
        dates = set(last_bars.values())
        print(f"\n  Last processed bar : {max(dates)}")

    print(f"\n{'='*60}\n")


def main():
    name_filter = sys.argv[1] if len(sys.argv) > 1 else None

    state_dir = settings.STATE_DIR
    if not state_dir.exists():
        print("No state/ directory found. Has the paper trader run at least once?")
        return

    state_files = sorted(state_dir.glob("*.json"))
    if not state_files:
        print("No state files found. Run `py -m trading_system.main_live --once` first.")
        return

    for path in state_files:
        if name_filter and path.stem != name_filter:
            continue
        report_strategy(path)


if __name__ == "__main__":
    main()
