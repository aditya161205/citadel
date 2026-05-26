import numpy as np
import pandas as pd

from ..backtester.strategy_base import StrategyBase


class MACDTrend(StrategyBase):
    """Filtered MACD trend strategy with ATR risk management.

    A plain MACD crossover fires constantly and whipsaws in chop. This version
    only takes a crossover when several independent things agree, then hands
    risk control to volatility (ATR) rather than a fixed percentage.

    Long entry (signal = 1) requires ALL of:
      - MACD line crosses above its signal line (the trigger),
      - price above the 200 EMA              (long-term uptrend),
      - the histogram is rising               (momentum building, not fading),
      - ADX >= threshold                      (the trend is strong enough to ride),
      - volume above its rolling average      (real participation behind the move).

    Exit (signal = -1) fires on the FIRST of:
      - an ATR chandelier stop: price closes at/below a stop that sits
        `atr_mult` ATRs below the best close since entry and only ratchets up.
        This is both the initial stop loss and the trailing exit -- one band
        that adapts to volatility instead of a fixed percentage,
      - a bearish MACD cross while price is below the 200 EMA with the histogram
        falling (downtrend confirmed). The engine is long-only, so this "sell /
        short" condition closes the long rather than opening a short.

    The stop is path-dependent (it ratchets off the running peak), so the exit
    side is computed in a single stateful pass over the bars; entry filters are
    vectorised. Both the backtester and the paper engine recompute signals from
    the full window each call, so the last bar stays consistent across runs.
    """

    name            = "macd_trend"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0

    def __init__(self, fast: int = 12, slow: int = 26, signal_period: int = 9,
                 trend_period: int = 200, adx_period: int = 14,
                 adx_threshold: float = 20.0, atr_period: int = 14,
                 atr_mult: float = 3.0, vol_period: int = 20):
        if fast >= slow:
            raise ValueError("fast EMA window must be less than slow EMA window")
        self.fast = fast
        self.slow = slow
        self.signal_period = signal_period
        self.trend_period = trend_period
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        self.atr_mult = atr_mult
        self.vol_period = vol_period
        # The 200 EMA is the binding constraint; 3x its span lets it converge
        # (matches the convention the other EMA strategies use for warmup).
        self.warmup = max(trend_period * 3, slow * 3)

    @staticmethod
    def _wilder(series: pd.Series, period: int) -> pd.Series:
        """Wilder's smoothing == EMA with alpha = 1/period."""
        return series.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    def _atr(self, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        """Average True Range (Wilder)."""
        prev_close = close.shift(1)
        true_range = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return self._wilder(true_range, self.atr_period)

    def _adx(self, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        """Average Directional Index (Wilder) -- measures trend strength, 0-100."""
        up_move = high.diff()
        down_move = -low.diff()
        # Directional movement: only the larger of the two, and only if positive.
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

        atr = self._atr(high, low, close)
        plus_di = 100 * self._wilder(plus_dm, self.adx_period) / atr
        minus_di = 100 * self._wilder(minus_dm, self.adx_period) / atr

        denom = (plus_di + minus_di).replace(0.0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / denom
        return self._wilder(dx, self.adx_period)

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        close, high, low = df['Close'], df['High'], df['Low']
        volume = df['Volume']

        # -- MACD: 12/26 EMAs, 9-EMA signal line, and the histogram between them.
        ema_fast = close.ewm(span=self.fast, adjust=False, min_periods=self.fast).mean()
        ema_slow = close.ewm(span=self.slow, adjust=False, min_periods=self.slow).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=self.signal_period, adjust=False,
                               min_periods=self.signal_period).mean()
        hist = macd - signal_line

        # -- Confirmation filters.
        trend_ema = close.ewm(span=self.trend_period, adjust=False,
                              min_periods=self.trend_period).mean()
        adx = self._adx(high, low, close)
        atr = self._atr(high, low, close)
        vol_avg = volume.rolling(self.vol_period).mean()

        cross_up = (macd > signal_line) & (macd.shift(1) <= signal_line.shift(1))
        cross_dn = (macd < signal_line) & (macd.shift(1) >= signal_line.shift(1))

        long_entry = (
            cross_up
            & (close > trend_ema)          # established uptrend
            & (hist > hist.shift(1))       # rising momentum
            & (adx >= self.adx_threshold)  # trend strong enough to ride
            & (volume > vol_avg)           # above-average participation
        )
        bear_exit = cross_dn & (close < trend_ema) & (hist < hist.shift(1))

        # All indicators must be valid before we trust any signal.
        valid = ~(trend_ema.isna() | signal_line.isna() | adx.isna()
                  | atr.isna() | vol_avg.isna())

        df['signal'] = self._apply_stops(
            close.to_numpy(), atr.to_numpy(),
            long_entry.fillna(False).to_numpy(),
            bear_exit.fillna(False).to_numpy(),
            valid.to_numpy(),
        )

        df['macd'] = macd
        df['macd_signal'] = signal_line
        df['macd_hist'] = hist
        df['trend_ema'] = trend_ema
        df['adx'] = adx
        df['atr'] = atr
        return df

    def _apply_stops(self, close, atr, long_entry, bear_exit, valid) -> np.ndarray:
        """Stateful pass: turn entry/exit conditions + an ATR chandelier stop
        into a 1/-1/0 signal series. The stop is the initial loss cap and the
        trailing exit in one -- it sits `atr_mult` ATRs below the best close
        since entry and only ever ratchets upward."""
        signals = np.zeros(len(close), dtype=int)
        in_pos = False
        stop = 0.0

        for i in range(len(close)):
            if not valid[i]:
                continue
            price = close[i]

            if not in_pos:
                if long_entry[i]:
                    in_pos = True
                    signals[i] = 1
                    stop = price - self.atr_mult * atr[i]
                continue

            # In a position: ratchet the trailing stop up, never down.
            stop = max(stop, price - self.atr_mult * atr[i])
            if price <= stop or bear_exit[i]:
                in_pos = False
                signals[i] = -1
                stop = 0.0

        return signals
