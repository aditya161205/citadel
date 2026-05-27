"""Daily-bar version of the Bollinger-pullback mean-reversion strategy.

Same conceptual setup as bb_pullback (lower-BB touch + bounce + oversold RSI
+ EMA50 not collapsing + volume + bullish candle), but on daily bars. This
trades far less often, but it can be backtested over the full 2018-2024
training window (yfinance gives unlimited daily history, only ~730d hourly).

Why no separate higher-TF gate here?
  bb_pullback (hourly) used the daily ema_rsi regime as a Murphy-style trend
  filter. The analogue for a daily strategy is a *weekly* filter, which is
  another design decision. To keep this strategy a clean baseline for the
  blend analysis, we omit the weekly gate; the same-TF EMA50-slope filter
  already provides a built-in 'trend not collapsing' check.

Exit logic (engine-managed): ATR-based SL/TP/trailing, no time-of-day filters.
"""

import pandas as pd

from ..backtester.strategy_base import StrategyBase
from ..backtester.indicators import atr, bollinger, ema, rsi


class BBPullbackDaily(StrategyBase):
    name            = "bb_pullback_daily"
    interval        = "1d"
    universe        = "nifty100"
    initial_capital = 10_000_000.0
    warmup          = 100
    # Mean-reverter: chop is where it works. Skip the trend-style regime gate.
    respect_regime_filter = False

    # Use the project-wide defaults (2018-01-01 -> 2024-01-01) by leaving
    # start_date / end_date unset (None falls back to settings).

    # ATR-stop config (same multipliers as the hourly version).
    atr_sl_mult           = 3.0
    atr_tp_mult           = 0.4
    trail_activation_atr  = 0.35
    trail_distance_atr    = 0.25
    min_stop_pct          = 0.005
    # No entry_window / eod_flatten on daily bars.

    BB_PERIOD       = 20
    BB_STD          = 2.0
    RSI_PERIOD      = 14
    RSI_OVERSOLD    = 50
    ATR_PERIOD      = 14
    EMA_LONG        = 50
    VOL_SMA         = 20
    VOL_MULTIPLIER  = 0.6
    EMA50_SLOPE_BARS      = 5
    EMA50_SLOPE_THRESHOLD = -0.025

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()

        ema50  = ema(df['Close'], self.EMA_LONG)
        rsi14  = rsi(df['Close'], self.RSI_PERIOD)
        atr14  = atr(df['High'], df['Low'], df['Close'], self.ATR_PERIOD)
        _mid, bb_upper, bb_lower = bollinger(df['Close'], self.BB_PERIOD, self.BB_STD)
        vol_sma = df['Volume'].rolling(self.VOL_SMA).mean()
        ema50_past   = ema50.shift(self.EMA50_SLOPE_BARS)
        ema50_slope  = (ema50 - ema50_past) / ema50_past

        cond = (
            (df['Low']   <= bb_lower) &
            (df['Close'] >  bb_lower) &
            (rsi14       <  self.RSI_OVERSOLD) &
            (ema50_slope >= self.EMA50_SLOPE_THRESHOLD) &
            (df['Volume'] > self.VOL_MULTIPLIER * vol_sma) &
            (df['Close'] >  df['Open'])
        ).fillna(False)

        df['signal']    = cond.astype(int)
        df['atr']       = atr14
        df['ema50']     = ema50
        df['rsi']       = rsi14
        df['bb_lower']  = bb_lower
        df['bb_upper']  = bb_upper
        df['vol_sma']   = vol_sma
        return df
