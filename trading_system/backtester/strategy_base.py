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

    # -- per-strategy backtest window (None = use settings.START_DATE/END_DATE) --
    start_date:     str | None     = None
    end_date:       str | None     = None

    # -- ATR-based stop config (None = use legacy signal-only exit logic) --
    # When atr_sl_mult is set, the engine switches to _run_with_stops().
    atr_sl_mult:           float | None = None
    atr_tp_mult:           float | None = None
    trail_activation_atr:  float        = 0.35
    trail_distance_atr:    float        = 0.25
    min_stop_pct:          float        = 0.005

    # -- intraday session controls (None = no time-of-day filter) --
    entry_window:     tuple[str, str] | None = None  # e.g. ("10:15", "14:00")
    eod_flatten_time: str | None             = None  # e.g. "15:00"

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Return data with an added integer 'signal' column: 1=buy, -1=sell, 0=hold."""
        ...
