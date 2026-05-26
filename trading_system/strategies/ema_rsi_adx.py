import numpy as np
import pandas as pd
from ..backtester.strategy_base import StrategyBase


class EMARSIADX(StrategyBase):
    """EMA+RSI with an ADX strength filter.

    Go long only when ALL THREE agree:
      - Trend direction : fast EMA > slow EMA
      - Momentum        : RSI >= rsi_threshold (default 50)
      - Trend strength  : ADX > adx_threshold (default 25)

    ADX measures trend strength regardless of direction. Below ~20 the market
    is sideways/choppy and EMA crossovers misfire. Requiring ADX > 25 keeps us
    out of chop and only trades when a real trend is in place.
    """

    name            = "ema_rsi_adx"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0

    def __init__(self, fast: int = 20, slow: int = 50,
                 rsi_period: int = 14, rsi_threshold: float = 50.0,
                 adx_period: int = 14, adx_threshold: float = 20.0):
        if fast >= slow:
            raise ValueError("fast EMA window must be less than slow EMA window")
        self.fast = fast
        self.slow = slow
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        # ADX needs a long warmup: two layers of Wilder smoothing.
        self.warmup = max(slow * 3, rsi_period * 3, adx_period * 5)

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

    @staticmethod
    def _adx(high: pd.Series, low: pd.Series, close: pd.Series,
             period: int) -> pd.Series:
        """Wilder's ADX (0-100). Measures trend strength, not direction."""
        prev_close = close.shift(1)
        # True Range: today's range plus any gap from yesterday's close.
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Directional movement: which side won today's expansion?
        up_move   = high.diff()
        down_move = -low.diff()
        plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_dm  = pd.Series(plus_dm,  index=high.index)
        minus_dm = pd.Series(minus_dm, index=high.index)

        # Wilder smoothing (EMA with alpha = 1/period) for TR, +DM, -DM.
        atr      = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period,
                                     adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period,
                                      adjust=False).mean() / atr

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        # ADX = Wilder-smoothed DX.
        return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        ema_fast = df['Close'].ewm(span=self.fast, adjust=False,
                                   min_periods=self.fast).mean()
        ema_slow = df['Close'].ewm(span=self.slow, adjust=False,
                                   min_periods=self.slow).mean()
        rsi = self._rsi(df['Close'], self.rsi_period)
        adx = self._adx(df['High'], df['Low'], df['Close'], self.adx_period)

        # All three must agree.
        long_cond = (
            (ema_fast > ema_slow)
            & (rsi >= self.rsi_threshold)
            & (adx >  self.adx_threshold)
        )
        regime = long_cond.astype(int)
        regime[ema_slow.isna() | rsi.isna() | adx.isna()] = 0

        # Transition-based signal (matches the legacy ema_rsi contract).
        df['signal']   = regime.diff().fillna(0).clip(-1, 1).astype(int)
        df['ema_fast'] = ema_fast
        df['ema_slow'] = ema_slow
        df['rsi']      = rsi
        df['adx']      = adx
        return df
