import pandas as pd
from ..backtester.strategy_base import StrategyBase


class EMARSIVol(StrategyBase):
    """EMA+RSI with a volume-confirmation filter.

    Long when ALL THREE agree:
      - Trend    : fast EMA > slow EMA
      - Momentum : RSI >= rsi_threshold (default 50)
      - Volume   : short-window volume average > long-window volume average,
                   i.e. recent participation is above the longer baseline.

    The volume filter is a completely different *kind* of signal -- not derived
    from price -- so it adds genuinely orthogonal information. The intuition:
    real, sustainable trends attract participation; chop and weak breakouts
    don't.
    """

    name            = "ema_rsi_vol"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0

    def __init__(self, fast: int = 20, slow: int = 50,
                 rsi_period: int = 14, rsi_threshold: float = 50.0,
                 vol_short: int = 10, vol_long: int = 30):
        if fast >= slow:
            raise ValueError("fast EMA window must be less than slow EMA window")
        if vol_short >= vol_long:
            raise ValueError("vol_short must be less than vol_long")
        self.fast = fast
        self.slow = slow
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold
        self.vol_short = vol_short
        self.vol_long = vol_long
        self.warmup = max(slow * 3, rsi_period * 3, vol_long * 2)

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
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

        vol_short_ma = df['Volume'].rolling(self.vol_short).mean()
        vol_long_ma  = df['Volume'].rolling(self.vol_long).mean()
        volume_ok = vol_short_ma > vol_long_ma

        long_cond = (
            (ema_fast > ema_slow)
            & (rsi >= self.rsi_threshold)
            & volume_ok
        )
        regime = long_cond.astype(int)
        regime[ema_slow.isna() | rsi.isna() | vol_long_ma.isna()] = 0

        df['signal']       = regime.diff().fillna(0).clip(-1, 1).astype(int)
        df['ema_fast']     = ema_fast
        df['ema_slow']     = ema_slow
        df['rsi']          = rsi
        df['vol_short_ma'] = vol_short_ma
        df['vol_long_ma']  = vol_long_ma
        return df
