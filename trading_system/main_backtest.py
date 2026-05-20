from datetime import datetime

import pandas as pd

from .backtester.data_loader import load_nifty100_symbols, load_universe
from .backtester.engine import BacktestEngine
from .backtester.metrics import compute_metrics, compute_metrics_raw
from .strategies.sma_crossover import SMACrossover
from .config import settings


def run_universe():
    symbols = load_nifty100_symbols()
    print(f"Fetched {len(symbols)} Nifty 100 symbols. Downloading price data...")

    data = load_universe(symbols, start=settings.START_DATE, end=settings.END_DATE)
    if not data:
        raise RuntimeError("No price data downloaded for any symbol.")
    print(f"Got data for {len(data)} / {len(symbols)} symbols.")

    capital_per_stock = settings.INITIAL_CAPITAL / len(data)
    strategy = SMACrossover(settings.SHORT_WINDOW, settings.LONG_WINDOW)

    equity_curves = {}
    per_stock = []

    for symbol, df in data.items():
        engine = BacktestEngine(
            strategy=strategy,
            data=df,
            symbol=symbol,
            initial_capital=capital_per_stock,
            position_size=settings.POSITION_SIZE,
        ).run()

        equity = engine.equity_curve['equity']
        equity_curves[symbol] = equity

        row = compute_metrics_raw(equity, capital_per_stock)
        row['symbol'] = symbol
        row['trades'] = len(engine.portfolio.trades)
        per_stock.append(row)

    portfolio_equity = _build_portfolio_equity(equity_curves, capital_per_stock)

    _write_per_stock_csv(per_stock)
    _print_portfolio_report(portfolio_equity, len(data))


def _build_portfolio_equity(equity_curves: dict, capital_per_stock: float) -> pd.Series:
    """Equal-weight portfolio = sum of every stock's equity curve, aligned by date.

    Before a stock has data its slice sits in cash (capital_per_stock); after its
    history ends the last value is carried forward.
    """
    frame = pd.DataFrame(equity_curves).sort_index()
    frame = frame.ffill().fillna(capital_per_stock)
    return frame.sum(axis=1)


def _write_per_stock_csv(per_stock: list):
    results = pd.DataFrame(per_stock)[[
        'symbol', 'initial_capital', 'final_value', 'total_return_pct',
        'sharpe', 'max_drawdown_pct', 'trades', 'trading_days',
    ]].sort_values('total_return_pct', ascending=False).round(4)

    settings.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.RESULTS_DIR / "sma_crossover_per_stock.csv"
    try:
        results.to_csv(out_path, index=False)
    except PermissionError:
        # Default file is locked (usually open in Excel) — write a timestamped copy.
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_path = settings.RESULTS_DIR / f"sma_crossover_per_stock_{stamp}.csv"
        results.to_csv(out_path, index=False)
        print("  (default file was locked — wrote a timestamped copy instead)")
    print(f"\nPer-stock results ({len(results)} stocks) written to: {out_path}")


def _print_portfolio_report(portfolio_equity: pd.Series, n_stocks: int):
    metrics = compute_metrics(portfolio_equity, settings.INITIAL_CAPITAL)
    print("\n===== OVERALL PORTFOLIO (equal-weight, "
          f"{n_stocks} stocks) =====")
    for k, v in metrics.items():
        print(f"  {k:<25}: {v}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    run_universe()
