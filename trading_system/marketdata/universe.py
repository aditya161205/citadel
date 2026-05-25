import pandas as pd

NIFTY100_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv"


def resolve_universe(spec) -> list[str]:
    """Turn a universe specification into a concrete list of ticker symbols.

    Supported specs:
        "nifty100"                      → live NSE Nifty 100 constituents (.NS)
        ["RELIANCE.NS", "TCS.NS", ...]  → pass-through (explicit list)
        "file:path/to/list.csv"         → one-column CSV of symbols
    """
    if isinstance(spec, list):
        return spec

    if spec == "nifty100":
        return _fetch_nifty100()

    if isinstance(spec, str) and spec.startswith("file:"):
        path = spec[len("file:"):]
        df = pd.read_csv(path, header=None)
        return [s.strip() for s in df.iloc[:, 0].tolist()]

    raise ValueError(
        f"Unknown universe spec: {spec!r}. "
        f"Use 'nifty100', an explicit list, or 'file:path.csv'."
    )


def _fetch_nifty100() -> list[str]:
    """Fetch Nifty 100 constituents from NSE archives."""
    df = pd.read_csv(NIFTY100_URL, storage_options={"User-Agent": "Mozilla/5.0"})
    return [f"{sym}.NS" for sym in df["Symbol"].str.strip()]
