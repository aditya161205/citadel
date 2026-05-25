from abc import ABC, abstractmethod
import pandas as pd


class StrategyBase(ABC):
    """Interface every strategy implements.

    Subclass, set the metadata attributes, and implement generate_signals.
    Both the backtester and the paper engine read these attributes — so a new
    strategy file in strategies/ is all you need to add.
    """

    # -- metadata: override these in your subclass --
    name:           str            = "unnamed"
    interval:       str            = "1d"       # "1d", "5m", "15m", …
    warmup:         int            = 200        # bars of history needed
    universe                       = "nifty100" # spec fed to resolve_universe
    initial_capital: float         = 10_000_000.0
    position_size:  float          = 0.95
    data_source:    str | None     = None       # None = global default

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Return data with an added integer 'signal' column: 1=buy, -1=sell, 0=hold."""
        ...
