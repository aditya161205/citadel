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

    # -- portfolio-level regime filter --
    # When True (the default) the paper-walkforward / live simulator blocks
    # NEW long entries when the Nifty 50 regime filter is off (chop or down
    # market). Mean-reversion strategies should set this False on the class
    # because chop is where they make money.
    respect_regime_filter: bool = True

    # -- portfolio strategy flag --
    # Set True on cross-sectional strategies that operate on the whole
    # universe at once (e.g. momentum: rank all stocks, hold top N).
    # The walk-forward simulator routes these through generate_target_portfolio
    # instead of per-symbol generate_signals.
    is_portfolio_strategy: bool = False

    # Rebalance frequency for portfolio strategies. "1D" = every bar,
    # "1M" = first trading day of each month, "1W" = first of each week.
    rebalance_freq: str = "1M"

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Return data with an added integer 'signal' column: 1=buy, -1=sell, 0=hold.

        Portfolio strategies override generate_target_portfolio instead.
        """
        if self.is_portfolio_strategy:
            raise NotImplementedError(
                f"{type(self).__name__} is a portfolio strategy — "
                "implement generate_target_portfolio(data_dict, ts) instead.")
        raise NotImplementedError

    def generate_target_portfolio(self, data: dict, ts) -> dict[str, float]:
        """Portfolio strategies override this. Return {symbol: weight in [0,1]}.

        Weights need not sum to 1.0 (1 - sum(weights) stays in cash).
        Symbols not in the returned dict are exited (target weight 0).
        `data` is {symbol: DataFrame}, all sliced to <= ts when called.
        """
        raise NotImplementedError(
            "Portfolio strategies must implement generate_target_portfolio.")
