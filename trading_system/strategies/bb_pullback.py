"""Hourly Bollinger-Band pullback, gated by daily ema_rsi regime.

Murphy's multi-timeframe doctrine in code: take mean-reversion (oscillator)
entries only in the direction of the higher-timeframe trend. Ported from
the user's Intraday-Trading-Bot (BB Bounce signal, ~60% of its trades).
"""

from datetime import date

import pandas as pd

from ..backtester.strategy_base import StrategyBase
from ..backtester.indicators import atr, bollinger, ema, rsi
from ..marketdata import get_data_source, resolve_universe


class BBPullback(StrategyBase):
    """Buy hourly BB-lower bounces on stocks where the daily trend is up.

    Entry (long-only, transition signal):
      - Daily ema_rsi regime == 1 (fast EMA > slow EMA AND RSI >= 50)
      - Hourly Low <= BB_lower(20, 2σ) AND Close > BB_lower (touched + bounced)
      - Hourly RSI(14) < 50 (oversold confirmation)
      - Hourly EMA(50) not falling sharply (5-bar slope >= -2.5%)
      - Hourly Volume > 0.6 * 20-bar volume average
      - Bullish candle (Close > Open)

    Exit (engine-managed via ATR-stop path):
      - Take profit  : entry + 0.4 * ATR(14, at entry)
      - Stop loss    : entry - 3.0 * ATR(14, at entry), min floor 0.5%
      - Trailing     : activates at +0.35 * ATR profit, trails 0.25 * ATR below high
      - Time stop    : flatten at 15:00 IST (no overnight)
      - Entry window : 10:15 - 14:00 IST
    """

    name            = "bb_pullback"
    interval        = "1h"
    universe        = "nifty100"
    initial_capital = 10_000_000.0
    warmup          = 200             # hourly bars (~30 trading days)
    # Mean-reverter -- already has its own higher-TF (daily ema_rsi) gate.
    # The market-wide regime filter would double-gate it. Opt out.
    respect_regime_filter = False

    # yfinance caps hourly history at ~730 days. Pick a window that fits.
    start_date = "2024-06-01"
    end_date   = "2026-05-26"

    # ATR-stop config -> triggers engine._run_with_stops().
    atr_sl_mult           = 3.0
    atr_tp_mult           = 0.4
    trail_activation_atr  = 0.35
    trail_distance_atr    = 0.25
    min_stop_pct          = 0.005
    entry_window          = ("10:15", "14:00")
    eod_flatten_time      = "15:00"

    # Entry-condition parameters (from the bot's strategy_config.json).
    BB_PERIOD       = 20
    BB_STD          = 2.0
    RSI_PERIOD      = 14
    RSI_OVERSOLD    = 50
    ATR_PERIOD      = 14
    EMA_SHORT       = 20
    EMA_LONG        = 50
    VOL_SMA         = 20
    VOL_MULTIPLIER  = 0.6
    EMA50_SLOPE_BARS      = 5
    EMA50_SLOPE_THRESHOLD = -0.025

    # Daily-regime (higher-TF) parameters: match strategies/ema_rsi.py defaults.
    DAILY_EMA_FAST       = 20
    DAILY_EMA_SLOW       = 50
    DAILY_RSI_PERIOD     = 14
    DAILY_RSI_THRESHOLD  = 50.0

    def __init__(self):
        # Lazy cache of {symbol -> daily regime Series (1/0, date-indexed)}.
        self._daily_regime_cache: dict[str, pd.Series] | None = None

    # -- helpers --

    def _load_daily_regimes(self) -> dict[str, pd.Series]:
        """Fetch daily bars for the whole universe once and compute the trend regime."""
        if self._daily_regime_cache is not None:
            return self._daily_regime_cache

        symbols = resolve_universe(self.universe)
        source  = get_data_source(self.data_source)
        # Pull enough daily history that the slow EMA is settled at the start
        # of our hourly window. Three years comfortably covers that. For the
        # end date we use the later of self.end_date and today, so the cache
        # still works in live / walk-forward mode where today > end_date.
        daily_start = "2022-01-01"
        today_str   = date.today().isoformat()
        daily_end   = max(self.end_date or today_str, today_str)
        daily = source.get_history(symbols, start=daily_start,
                                   end=daily_end, interval="1d")

        regimes = {}
        for sym, df in daily.items():
            if df.empty:
                continue
            ema_fast = ema(df['Close'], self.DAILY_EMA_FAST)
            ema_slow = ema(df['Close'], self.DAILY_EMA_SLOW)
            r        = rsi(df['Close'], self.DAILY_RSI_PERIOD)
            regime = (
                (ema_fast > ema_slow) &
                (r >= self.DAILY_RSI_THRESHOLD)
            ).astype(int)
            regime[ema_slow.isna() | r.isna()] = 0
            # Index by date (drop time-of-day) so hourly bars can look it up.
            regime.index = regime.index.normalize()
            regimes[sym] = regime
        self._daily_regime_cache = regimes
        return regimes

    def _resolve_symbol(self, df: pd.DataFrame) -> str | None:
        """Best-effort lookup: the engine doesn't pass symbol into generate_signals,
        but our data frames carry it as an attribute via yfinance group_by. Fall
        back to scanning the regime cache by index-matching if that's missing.
        """
        sym = df.attrs.get('symbol')
        if sym:
            return sym
        return None

    # -- contract --

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()

        # Hourly indicators.
        ema20  = ema(df['Close'], self.EMA_SHORT)
        ema50  = ema(df['Close'], self.EMA_LONG)
        rsi14  = rsi(df['Close'], self.RSI_PERIOD)
        atr14  = atr(df['High'], df['Low'], df['Close'], self.ATR_PERIOD)
        _mid, bb_upper, bb_lower = bollinger(df['Close'], self.BB_PERIOD, self.BB_STD)
        vol_sma = df['Volume'].rolling(self.VOL_SMA).mean()
        ema50_past   = ema50.shift(self.EMA50_SLOPE_BARS)
        ema50_slope  = (ema50 - ema50_past) / ema50_past

        # Higher-TF gate: look up daily regime per bar.
        daily_regime_aligned = self._gate_series(df)

        # All BB-Bounce conditions (per the bot's strategy_v2.py).
        cond = (
            (df['Low']   <= bb_lower) &
            (df['Close'] >  bb_lower) &
            (rsi14       <  self.RSI_OVERSOLD) &
            (ema50_slope >= self.EMA50_SLOPE_THRESHOLD) &
            (df['Volume'] > self.VOL_MULTIPLIER * vol_sma) &
            (df['Close'] >  df['Open']) &
            (daily_regime_aligned == 1)
        )

        # Drop NaN -> False (so warmup rows don't spuriously trigger).
        cond = cond.fillna(False)
        # Entry-only signal: 1 on each qualifying bar. Engine's stop logic handles
        # the exit, and the engine skips entries while a position is already open,
        # so we don't need to dedupe here.
        df['signal'] = cond.astype(int)
        df['atr']    = atr14
        # Diagnostics.
        df['ema20']         = ema20
        df['ema50']         = ema50
        df['rsi']           = rsi14
        df['bb_upper']      = bb_upper
        df['bb_lower']      = bb_lower
        df['vol_sma']       = vol_sma
        df['daily_regime']  = daily_regime_aligned
        return df

    def _gate_series(self, df: pd.DataFrame) -> pd.Series:
        """Resolve a per-bar daily-regime series (1/0) aligned to df.index."""
        regimes = self._load_daily_regimes()
        sym = self._resolve_symbol(df)

        # Hourly bars from yfinance come back UTC-aware; daily bars come back
        # naive. Convert hourly UTC -> IST -> normalize to date -> strip TZ so
        # the reindex against naive daily dates matches cleanly.
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is not None:
            idx = idx.tz_convert("Asia/Kolkata")
        idx_dates = idx.normalize().tz_localize(None)

        if sym and sym in regimes:
            r = regimes[sym]
            aligned = r.reindex(idx_dates, method='ffill').to_numpy()
            return pd.Series(aligned, index=df.index).fillna(0).astype(int)

        # No symbol attribute -> stay flat (safe fallback rather than crash).
        return pd.Series(0, index=df.index, dtype=int)
