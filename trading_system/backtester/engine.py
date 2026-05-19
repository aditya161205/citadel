import numpy as np
import pandas as pd

def compute_metrics(equity: pd.Series, initial_capital: float) -> dict:
    returns = equity.pct_change().dropna()
    total_return = (equity.iloc[-1] - initial_capital) / initial_capital * 100
    sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() != 0 else 0

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min() * 100

    return {
        "Initial Capital":    f"${initial_capital:,.2f}",
        "Final Value":        f"${equity.iloc[-1]:,.2f}",
        "Total Return":       f"{total_return:.2f}%",
        "Sharpe Ratio":       f"{sharpe:.4f}",
        "Max Drawdown":       f"{max_drawdown:.2f}%",
        "Total Trading Days": len(equity),
    }
