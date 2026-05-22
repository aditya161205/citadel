import pandas as pd
from ..backtester.strategy_base import StrategyBase


class EMARSI(StrategyBase):
    """Trend + momentum strategy.

    Go long only when BOTH agree:
      - Trend  : the fast EMA is above the slow EMA (price in an uptrend).
      - Momentum: RSI is at or above a threshold (default 50, i.e. bulls in control).

    Exit when either condition fails. EMAs react faster than plain SMAs, and the
    RSI filter weeds out weak EMA crossovers that lack momentum behind them.
    """

    name            = "ema_rsi"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0

    def __init__(self, fast: int = 20, slow: int = 50,
                 rsi_period: int = 14, rsi_threshold: float = 50.0):
        if fast >= slow:
            raise ValueError("fast EMA window must be less than slow EMA window")
        self.fast = fast
        self.slow = slow
        self.rsi_period = rsi_period
        self.rsi_threshold = rsi_threshold
        # Enough history for the slow EMA to settle and RSI to be valid.
        self.warmup = max(slow * 3, rsi_period * 3)

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        """Wilder's RSI (0-100)."""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        # Wilder smoothing == EMA with alpha = 1/period.
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

        # In the market only when trend (EMA) and momentum (RSI) both agree.
        long_cond = (ema_fast > ema_slow) & (rsi >= self.rsi_threshold)
        regime = long_cond.astype(int)
        # Stay flat until every indicator is valid.
        regime[ema_slow.isna() | rsi.isna()] = 0

        # Emit a signal only on the bar where the regime flips (+1 enter, -1 exit).
        df['signal'] = regime.diff().fillna(0).clip(-1, 1).astype(int)
        df['ema_fast'] = ema_fast
        df['ema_slow'] = ema_slow
        df['rsi'] = rsi
        return df
