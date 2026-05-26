import pandas as pd
from ..backtester.strategy_base import StrategyBase


class EMARSILongShort(StrategyBase):
    """Long/short version of ema_rsi.

    Target position by bar:
        +1 (LONG)  when fast EMA > slow EMA AND RSI >= rsi_long_threshold (50)
        -1 (SHORT) when fast EMA < slow EMA AND RSI <  rsi_short_threshold (50)
         0 (FLAT)  otherwise (indicators disagree, or warmup not done)

    Same trend + momentum philosophy, applied symmetrically: trade with the
    trend on both sides. The flat state covers disagreement zones (e.g. EMAs
    say downtrend but RSI > 50) -- we only act when both indicators agree.
    """

    name            = "ema_rsi_ls"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0

    def __init__(self, fast: int = 20, slow: int = 50,
                 rsi_period: int = 14,
                 rsi_long_threshold: float = 50.0,
                 rsi_short_threshold: float = 50.0):
        if fast >= slow:
            raise ValueError("fast EMA window must be less than slow EMA window")
        self.fast = fast
        self.slow = slow
        self.rsi_period = rsi_period
        self.rsi_long_threshold = rsi_long_threshold
        self.rsi_short_threshold = rsi_short_threshold
        self.warmup = max(slow * 3, rsi_period * 3)

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        """Wilder's RSI (0-100)."""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        ema_fast = df['Close'].ewm(span=self.fast, adjust=False,
                                   min_periods=self.fast).mean()
        ema_slow = df['Close'].ewm(span=self.slow, adjust=False,
                                   min_periods=self.slow).mean()
        rsi = self._rsi(df['Close'], self.rsi_period)

        long_cond  = (ema_fast > ema_slow) & (rsi >= self.rsi_long_threshold)
        short_cond = (ema_fast < ema_slow) & (rsi <  self.rsi_short_threshold)

        # Target position state: +1 long, -1 short, 0 flat.
        position = pd.Series(0, index=df.index, dtype=int)
        position[long_cond]  =  1
        position[short_cond] = -1
        # Force flat until every indicator is valid.
        position[ema_slow.isna() | rsi.isna()] = 0

        df['position'] = position
        df['ema_fast'] = ema_fast
        df['ema_slow'] = ema_slow
        df['rsi']      = rsi
        return df
