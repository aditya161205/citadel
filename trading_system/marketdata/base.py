from abc import ABC, abstractmethod
import pandas as pd


class DataSource(ABC):
    """Interface for all market-data providers.

    Subclass and implement `get_history` + `get_recent` to add a new source
    (e.g. CSV, broker API).  Both methods return the same shape:
        {symbol: Date-indexed DataFrame with Open/High/Low/Close/Volume}
    """

    @abstractmethod
    def get_history(self, symbols: list[str], start: str, end: str,
                    interval: str = "1d") -> dict[str, pd.DataFrame]:
        """Full historical data between start and end (for backtesting)."""
        ...

    @abstractmethod
    def get_recent(self, symbols: list[str], interval: str = "1d",
                   warmup: int = 200) -> dict[str, pd.DataFrame]:
        """Most recent data — enough bars for `warmup` (for paper/live trading)."""
        ...
