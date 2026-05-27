"""Side-by-side performance report for every paper-trading state file.

Reads every state/<name>_paper*.json and prints a single table comparing
all strategies on the same metric set, ranked by Sharpe.

Run:
  py -m trading_system.performance_report
  py -m trading_system.performance_report --csv results/performance.csv
  py -m trading_system.performance_report --sort sharpe        (default)
  py -m trading_system.performance_report --sort return        (by total return)
  py -m trading_system.performance_report --sort calmar
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from .backtester.metrics import compute_metrics_raw
from .config import settings


# State files we want to include in the master comparison. The walk-forward
# writes <name>_paper_regime.json (or _paper.json when --regime is off).
# Composite blends also write _paper_regime_combined.json. Rolling slices
# write _paper_S{i} variants -- excluded from the headline table.
def _gather_state_files() -> list[Path]:
    state_dir = settings.STATE_DIR
    out: list[Path] = []
    for p in sorted(state_dir.glob("*.json")):
        name = p.stem
        # Skip rolling-slice files.
        if "_paper_S" in name:
            continue
        # Skip sub-strategy files (kept under their composite suffix).
        if "_paper_under_" in name or "_paper_regime_under_" in name:
            continue
        out.append(p)
    return out


def _load_equity(blob: dict) -> pd.Series:
    eq_map = blob.get("equity_history", {})
    if not eq_map:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(k): float(v) for k, v in eq_map.items()})
    return s.sort_index()


def _label_from_filename(path: Path) -> str:
    name = path.stem
    # Trim known suffixes for cleaner labels.
    for suf in ("_paper_regime_combined", "_paper_combined",
                "_paper_regime", "_paper"):
        if name.endswith(suf):
            return name[:-len(suf)]
    return name


def _metric_row(path: Path, benchmark_equity: pd.Series | None) -> dict | None:
    try:
        blob = json.loads(path.read_text())
    except Exception:
        return None

    eq = _load_equity(blob)
    if eq.empty:
        return None

    initial = float(blob.get("initial_capital", eq.iloc[0]))
    m = compute_metrics_raw(eq, initial)

    label = _label_from_filename(path)

    # Trade / turnover stats.
    trades = blob.get("trades")
    n_trades = blob.get("n_trades", len(trades) if trades else 0)
    total_notional = float(blob.get("total_traded_notional", 0.0))
    total_fees = float(blob.get("total_transaction_costs", 0.0))
    years = max((eq.index[-1] - eq.index[0]).total_seconds() / (365.25 * 86400.0), 1e-9)
    annual_turnover_pct = (total_notional / initial / years) * 100.0 if initial else 0.0
    fees_as_pct_initial = (total_fees / initial) * 100.0 if initial else 0.0

    row = {
        "strategy":          label,
        "is_composite":      "sub_strategies" in blob,
        "is_benchmark":      "benchmark_summary" in blob,
        "initial":           initial,
        "final":             float(eq.iloc[-1]),
        "return_pct":        m["total_return_pct"],
        "cagr_pct":          m["cagr_pct"],
        "sharpe":            m["sharpe"],
        "sortino":           m["sortino"],
        "calmar":            m["calmar"],
        "vol_pct":           m["vol_annual_pct"],
        "max_dd_pct":        m["max_drawdown_pct"],
        "trading_days":      m["trading_days"],
        "n_trades":          n_trades,
        "annual_turnover_%": annual_turnover_pct,
        "fees_paid_%":       fees_as_pct_initial,
        "window_start":      eq.index[0].date().isoformat(),
        "window_end":        eq.index[-1].date().isoformat(),
    }

    # vs-benchmark deltas (return, info ratio).
    if benchmark_equity is not None and not benchmark_equity.empty and not row["is_benchmark"]:
        # Align on common dates.
        bench = benchmark_equity.copy()
        bench.index = pd.to_datetime(bench.index)
        common = eq.index.intersection(bench.index)
        if len(common) >= 5:
            eq_c = eq.loc[common]
            bench_c = bench.loc[common]
            strat_ret = eq_c.pct_change().dropna()
            bench_ret = bench_c.pct_change().dropna()
            common_ret = strat_ret.index.intersection(bench_ret.index)
            if len(common_ret) > 1:
                active = strat_ret.loc[common_ret] - bench_ret.loc[common_ret]
                te = float(active.std(ddof=1))
                bpy = m["bars_per_year"]
                info = float(active.mean() / te * np.sqrt(bpy)) if te > 0 else 0.0
                row["alpha_pct"] = row["return_pct"] - (
                    (float(bench_c.iloc[-1]) - float(bench_c.iloc[0])) /
                    float(bench_c.iloc[0]) * 100.0
                )
                row["info_ratio"] = info
            else:
                row["alpha_pct"] = 0.0
                row["info_ratio"] = 0.0
        else:
            row["alpha_pct"] = 0.0
            row["info_ratio"] = 0.0
    else:
        row["alpha_pct"] = 0.0
        row["info_ratio"] = 0.0

    return row


def build_table(sort_by: str = "sharpe") -> pd.DataFrame:
    files = _gather_state_files()

    # Find benchmark first so others get vs-benchmark deltas.
    benchmark_path = next(
        (p for p in files if "benchmark" in p.stem.lower()), None)
    benchmark_eq = None
    if benchmark_path is not None:
        try:
            blob = json.loads(benchmark_path.read_text())
            benchmark_eq = _load_equity(blob)
        except Exception:
            pass

    rows = []
    for p in files:
        r = _metric_row(p, benchmark_eq)
        if r:
            rows.append(r)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # Sort: benchmark always last for visual separation, then by requested metric.
    sort_col = {
        "sharpe":  "sharpe",
        "return":  "return_pct",
        "calmar":  "calmar",
        "sortino": "sortino",
        "cagr":    "cagr_pct",
    }.get(sort_by, "sharpe")
    df = df.sort_values(["is_benchmark", sort_col], ascending=[True, False])
    return df.reset_index(drop=True)


def print_report(df: pd.DataFrame):
    if df.empty:
        print("No state files found in", settings.STATE_DIR)
        return

    paper_start = df["window_start"].mode().iloc[0]
    paper_end   = df["window_end"].mode().iloc[0]
    print()
    print("=" * 138)
    print(f"  PERFORMANCE REPORT  (paper window {paper_start} -> {paper_end})")
    print("=" * 138)

    print(
        f"  {'Strategy':<22} {'Ret%':>7} {'CAGR%':>7} "
        f"{'Sharpe':>7} {'Sortino':>8} {'Calmar':>7} "
        f"{'Vol%':>6} {'MaxDD%':>7} {'Trades':>7} "
        f"{'Turn%/y':>8} {'Fees%':>6} {'Alpha%':>11}")
    print("  " + "-" * 134)

    for _, r in df.iterrows():
        # Annotate benchmark / composite rows.
        label = r['strategy']
        if r['is_benchmark']:
            label = f"* {label}"
        elif r['is_composite']:
            label = f"+ {label}"
        print(
            f"  {label:<22} "
            f"{r['return_pct']:>7.2f} "
            f"{r['cagr_pct']:>7.2f} "
            f"{r['sharpe']:>7.3f} "
            f"{r['sortino']:>8.3f} "
            f"{r['calmar']:>7.3f} "
            f"{r['vol_pct']:>6.2f} "
            f"{r['max_dd_pct']:>7.2f} "
            f"{int(r['n_trades']):>7} "
            f"{r['annual_turnover_%']:>8.1f} "
            f"{r['fees_paid_%']:>6.2f} "
            f"{r['alpha_pct']:>11.2f}")

    print("  " + "-" * 134)
    print("  * = benchmark (Nifty 50 buy-and-hold)   + = composite strategy")
    print(f"  All numbers AFTER {settings.TRANSACTION_COST_RATE*10000:.0f} bps/side "
          f"transaction costs and Sharpe is computed against "
          f"{settings.CASH_INTEREST_RATE_ANNUAL*100:.1f}% risk-free rate.")
    print("=" * 138)


def main():
    argv = sys.argv[1:]
    sort_by = "sharpe"
    if "--sort" in argv:
        i = argv.index("--sort")
        if i + 1 < len(argv):
            sort_by = argv[i + 1]
            del argv[i:i + 2]

    csv_path = None
    if "--csv" in argv:
        i = argv.index("--csv")
        if i + 1 < len(argv):
            csv_path = argv[i + 1]
            del argv[i:i + 2]

    df = build_table(sort_by=sort_by)
    print_report(df)

    if csv_path:
        out = Path(csv_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
