import numpy as np
import pandas as pd
from ..backtester.strategy_base import StrategyBase


class EMARSIADXSized(StrategyBase):
    """EMA+RSI entries, sized continuously by ADX (trend strength).

    Same entry/exit logic as ema_rsi (fast EMA > slow EMA AND RSI >= 50),
    but the position size at entry scales linearly with ADX:

        size_factor = clamp((ADX - 15) / 25, 0, 1)

      ADX <= 15  ->  0   (effectively no trade)
      ADX  = 20  ->  0.2
      ADX  = 27  ->  0.5
      ADX  = 35  ->  0.8
      ADX >= 40  ->  1.0 (full size)

    This keeps us in the game during quiet trends (where ADX > 25 filtering
    would skip the trade entirely) while loading up only when the trend is
    genuinely strong.
    """

    name            = "ema_rsi_adx_sized"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0

    def __init__(self, fast: int = 20, slow: int = 50,
                 rsi_period: int = 14, rsi_threshold: float = 50.0,
                 adx_period: int = 14,
                 adx_floor: float = 15.0, adx_ceiling: float = 40.0):
        if fast >= slow:
            raise ValueError("fast EMA window must be less than slow EMA window")
        if adx_floor >= adx_ceiling:
            raise ValueError("adx_floor must be less than adx_ceiling")
        self.fast = fast
        self.slow = slow
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold
        self.adx_period = adx_period
        self.adx_floor = adx_floor
        self.adx_ceiling = adx_ceiling
        self.warmup = max(slow * 3, rsi_period * 3, adx_period * 5)

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
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
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        up_move = high.diff()
        down_move = -low.diff()
        plus_dm  = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_dm  = pd.Series(plus_dm,  index=high.index)
        minus_dm = pd.Series(minus_dm, index=high.index)

        atr      = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        plus_di  = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period,
                                     adjust=False).mean() / atr
        minus_di = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period,
                                      adjust=False).mean() / atr

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        ema_fast = df['Close'].ewm(span=self.fast, adjust=False,
                                   min_periods=self.fast).mean()
        ema_slow = df['Close'].ewm(span=self.slow, adjust=False,
                                   min_periods=self.slow).mean()
        rsi = self._rsi(df['Close'], self.rsi_period)
        adx = self._adx(df['High'], df['Low'], df['Close'], self.adx_period)

        # Same long regime as plain ema_rsi.
        long_cond = (ema_fast > ema_slow) & (rsi >= self.rsi_threshold)
        regime = long_cond.astype(int)
        regime[ema_slow.isna() | rsi.isna()] = 0

        # Size factor from ADX (clamped 0..1).
        span = self.adx_ceiling - self.adx_floor
        size_factor = ((adx - self.adx_floor) / span).clip(0, 1).fillna(0)

        df['signal']      = regime.diff().fillna(0).clip(-1, 1).astype(int)
        df['size_factor'] = size_factor
        df['ema_fast']    = ema_fast
        df['ema_slow']    = ema_slow
        df['rsi']         = rsi
        df['adx']         = adx
        return df
