import numpy as np
import pandas as pd

def compute_metrics_raw(equity: pd.Series, initial_capital: float) -> dict:
    """Numeric performance metrics — convenient for tables/CSV and aggregation."""
    returns = equity.pct_change().dropna()
    total_return = (equity.iloc[-1] - initial_capital) / initial_capital * 100
    sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() != 0 else 0

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min() * 100

    return {
        "initial_capital":  initial_capital,
        "final_value":      equity.iloc[-1],
        "total_return_pct": total_return,
        "sharpe":           sharpe,
        "max_drawdown_pct": max_drawdown,
        "trading_days":     len(equity),
    }


def compute_metrics(equity: pd.Series, initial_capital: float) -> dict:
    """Human-readable, formatted version of compute_metrics_raw."""
    m = compute_metrics_raw(equity, initial_capital)
    return {
        "Initial Capital":    f"${m['initial_capital']:,.2f}",
        "Final Value":        f"${m['final_value']:,.2f}",
        "Total Return":       f"{m['total_return_pct']:.2f}%",
        "Sharpe Ratio":       f"{m['sharpe']:.4f}",
        "Max Drawdown":       f"{m['max_drawdown_pct']:.2f}%",
        "Total Trading Days": m['trading_days'],
    }
