import pandas as pd
import yfinance as yf

NIFTY100_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv"


def load_nifty100_symbols() -> list[str]:
    """Fetch the current Nifty 100 constituents from the NSE archives.

    The User-Agent is required: NSE silently blocks (hangs on) the default Python
    user-agent. Returns Yahoo Finance tickers (NSE symbol + the .NS suffix).
    """
    df = pd.read_csv(NIFTY100_URL, storage_options={"User-Agent": "Mozilla/5.0"})
    return [f"{sym}.NS" for sym in df["Symbol"].str.strip()]


def load_universe(symbols: list[str], start: str, end: str,
                  interval: str = "1d") -> dict[str, pd.DataFrame]:
    """Batch-download OHLCV for many symbols in one request.

    Returns a dict mapping symbol -> Date-indexed OHLCV DataFrame, skipping any
    symbol that returned no data.
    """
    raw = yf.download(symbols, start=start, end=end, interval=interval,
                      auto_adjust=True, progress=False, group_by="ticker",
                      threads=True)

    data = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna(how="all")
        except KeyError:
            continue
        if df.empty:
            continue
        df.index.name = "date"
        data[sym] = df
    return data


def load_data(symbol: str, start: str, end: str, interval: str = "1d") -> pd.DataFrame:
    """Fetch OHLCV data for a single symbol from Yahoo Finance.

    Nifty stocks use the .NS suffix, e.g. "RELIANCE.NS".
    Returns a Date-indexed DataFrame with Open/High/Low/Close/Volume columns.
    """
    df = yf.download(symbol, start=start, end=end, interval=interval,
                     auto_adjust=True, progress=False)

    if df is None or df.empty:
        raise ValueError(
            f"No data returned for '{symbol}' between {start} and {end}. "
            f"Check the symbol (Nifty names need the .NS suffix) and date range."
        )

    # yfinance returns a MultiIndex column header for single-ticker downloads
    # in recent versions; flatten it to plain OHLCV column names.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.index.name = "date"
    return df
