import sys
from datetime import datetime

import pandas as pd

from .backtester.engine import BacktestEngine
from .backtester.metrics import compute_metrics, compute_metrics_raw
from .config import settings
from .marketdata import get_data_source, resolve_universe
from .strategies import discover_strategies


def run_strategy(strategy):
    """Backtest a single strategy across its declared universe."""
    print(f"\n{'='*60}")
    print(f"  Backtesting [{strategy.name}]  interval={strategy.interval}")
    print(f"{'='*60}")

    symbols = resolve_universe(strategy.universe)
    print(f"Universe: {len(symbols)} symbols.")

    source = get_data_source(strategy.data_source)
    data = source.get_history(symbols, start=settings.START_DATE,
                              end=settings.END_DATE, interval=strategy.interval)
    if not data:
        print(f"  No data for [{strategy.name}] -- skipping.")
        return
    print(f"Got data for {len(data)} / {len(symbols)} symbols.")

    capital_per_stock = strategy.initial_capital / len(data)
    equity_curves = {}
    per_stock = []

    for symbol, df in data.items():
        engine = BacktestEngine(
            strategy=strategy,
            data=df,
            symbol=symbol,
            initial_capital=capital_per_stock,
            position_size=strategy.position_size,
        ).run()

        equity = engine.equity_curve['equity']
        equity_curves[symbol] = equity

        row = compute_metrics_raw(equity, capital_per_stock)
        row['symbol'] = symbol
        row['trades'] = len(engine.portfolio.trades)
        per_stock.append(row)

    portfolio_equity = _build_portfolio_equity(equity_curves, capital_per_stock)
    _write_per_stock_csv(per_stock, strategy.name)
    _print_portfolio_report(portfolio_equity, len(data), strategy)


def _build_portfolio_equity(equity_curves, capital_per_stock):
    frame = pd.DataFrame(equity_curves).sort_index()
    frame = frame.ffill().fillna(capital_per_stock)
    return frame.sum(axis=1)


def _write_per_stock_csv(per_stock, strategy_name):
    results = pd.DataFrame(per_stock)[[
        'symbol', 'initial_capital', 'final_value', 'total_return_pct',
        'sharpe', 'max_drawdown_pct', 'trades', 'trading_days',
    ]].sort_values('total_return_pct', ascending=False).round(4)

    settings.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = settings.RESULTS_DIR / f"{strategy_name}_per_stock.csv"
    try:
        results.to_csv(out_path, index=False)
    except PermissionError:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        out_path = settings.RESULTS_DIR / f"{strategy_name}_per_stock_{stamp}.csv"
        results.to_csv(out_path, index=False)
        print("  (default file locked -- wrote a timestamped copy instead)")
    print(f"\nPer-stock results ({len(results)} stocks) -> {out_path}")


def _print_portfolio_report(portfolio_equity, n_stocks, strategy):
    metrics = compute_metrics(portfolio_equity, strategy.initial_capital)
    print(f"\n===== OVERALL PORTFOLIO [{strategy.name}] "
          f"(equal-weight, {n_stocks} stocks) =====")
    for k, v in metrics.items():
        print(f"  {k:<25}: {v}")
    print("=" * 60 + "\n")


def main():
    # Accept an optional strategy name filter: py -m trading_system.main_backtest sma_crossover
    name_filter = sys.argv[1] if len(sys.argv) > 1 else None

    strategy_classes = discover_strategies()
    if not strategy_classes:
        print("No strategies found in strategies/. Nothing to backtest.")
        return

    for cls in strategy_classes:
        strat = cls()
        if name_filter and strat.name != name_filter:
            continue
        run_strategy(strat)


if __name__ == "__main__":
    main()
