"""Performance metrics — risk-free-rate-aware Sharpe + a fuller set.

What we compute
---------------
- Total return / final value
- CAGR (annualized return, geometric)
- Sharpe ratio  : (excess return over RFR) / vol, annualized (the *real* one)
- Sortino ratio : excess return / downside vol (penalises only negative deviation)
- Calmar ratio  : CAGR / |max drawdown| (return per unit of worst drawdown)
- Annualized vol
- Max drawdown
- Trading bars in the window

Sharpe convention
-----------------
The earlier version used `mean / std * sqrt(252)` with no RFR subtraction.
Combined with crediting 6% on idle cash, that double-counted RFR — Sharpe
got inflated wherever a strategy held lots of cash. The fixed version
subtracts the daily-compounded RFR from each daily return BEFORE computing
the ratio, which is the textbook Sharpe formula.

Bars-per-year heuristic
-----------------------
- For ~250-260 bars per year, treat as daily   (annualize by sqrt(252))
- For ~1500+ bars per year, treat as hourly    (annualize by sqrt(1638))
"""

import numpy as np
import pandas as pd


def _bars_per_year(equity: pd.Series) -> int:
    """Detect daily vs hourly from the index density."""
    if len(equity) < 2:
        return 252
    span_days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0
    if span_days <= 0:
        return 252
    rate = len(equity) / span_days * 365.25     # bars per calendar year
    # Daily ~252, hourly ~1638. Snap to the nearest.
    return 1638 if rate > 800 else 252


def compute_metrics_raw(equity: pd.Series, initial_capital: float,
                        rf_annual: float | None = None) -> dict:
    """Numeric performance metrics — convenient for tables/CSV and aggregation."""
    from ..config import settings
    if rf_annual is None:
        rf_annual = getattr(settings, "CASH_INTEREST_RATE_ANNUAL", 0.0)

    if len(equity) < 2:
        return {
            "initial_capital":  initial_capital,
            "final_value":      float(equity.iloc[-1]) if len(equity) else initial_capital,
            "total_return_pct": 0.0,
            "cagr_pct":         0.0,
            "sharpe":           0.0,
            "sortino":          0.0,
            "calmar":           0.0,
            "vol_annual_pct":   0.0,
            "max_drawdown_pct": 0.0,
            "trading_days":     len(equity),
        }

    returns = equity.pct_change().dropna()
    bpy = _bars_per_year(equity)
    # Per-bar risk-free rate, geometric compounding to the chosen frequency.
    rf_per_bar = (1.0 + rf_annual) ** (1.0 / bpy) - 1.0
    excess = returns - rf_per_bar

    total_return_pct = (float(equity.iloc[-1]) - initial_capital) / initial_capital * 100.0
    years = max((equity.index[-1] - equity.index[0]).total_seconds() / (365.25 * 86400.0),
                1e-9)
    cagr = (float(equity.iloc[-1]) / initial_capital) ** (1.0 / years) - 1.0

    vol_per_bar = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0
    vol_annual = vol_per_bar * np.sqrt(bpy)

    # Sharpe (excess of RFR), annualized.
    if vol_per_bar > 0:
        sharpe = float(excess.mean() / vol_per_bar) * np.sqrt(bpy)
    else:
        sharpe = 0.0

    # Sortino: only downside deviation in the denominator.
    downside = excess.where(excess < 0, 0.0)
    downside_var = float((downside ** 2).mean())
    downside_std = np.sqrt(downside_var)
    if downside_std > 0:
        sortino = float(excess.mean() / downside_std) * np.sqrt(bpy)
    else:
        sortino = 0.0

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown_pct = float(drawdown.min()) * 100.0

    if max_drawdown_pct < 0:
        calmar = float(cagr * 100.0) / abs(max_drawdown_pct)
    else:
        calmar = 0.0

    return {
        "initial_capital":  initial_capital,
        "final_value":      float(equity.iloc[-1]),
        "total_return_pct": total_return_pct,
        "cagr_pct":         cagr * 100.0,
        "sharpe":           sharpe,
        "sortino":          sortino,
        "calmar":           calmar,
        "vol_annual_pct":   vol_annual * 100.0,
        "max_drawdown_pct": max_drawdown_pct,
        "trading_days":     len(equity),
        "bars_per_year":    bpy,
        "rf_annual":        rf_annual,
    }


def compute_metrics(equity: pd.Series, initial_capital: float,
                    rf_annual: float | None = None) -> dict:
    """Human-readable, formatted version of compute_metrics_raw."""
    m = compute_metrics_raw(equity, initial_capital, rf_annual=rf_annual)
    return {
        "Initial Capital":    f"Rs{m['initial_capital']:,.2f}",
        "Final Value":        f"Rs{m['final_value']:,.2f}",
        "Total Return":       f"{m['total_return_pct']:.2f}%",
        "CAGR":               f"{m['cagr_pct']:.2f}%",
        "Sharpe (excess RFR)":f"{m['sharpe']:.3f}",
        "Sortino":            f"{m['sortino']:.3f}",
        "Calmar":             f"{m['calmar']:.3f}",
        "Annualized Vol":     f"{m['vol_annual_pct']:.2f}%",
        "Max Drawdown":       f"{m['max_drawdown_pct']:.2f}%",
        "Total Trading Days": m['trading_days'],
    }
