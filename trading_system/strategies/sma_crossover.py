import pandas as pd
from ..backtester.strategy_base import StrategyBase


class SMACrossover(StrategyBase):
    """Buy when the short SMA crosses above the long SMA, sell on the cross below."""

    def __init__(self, short_window: int = 50, long_window: int = 200):
        if short_window >= long_window:
            raise ValueError("short_window must be less than long_window")
        self.short_window = short_window
        self.long_window = long_window

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        df = data.copy()
        short_sma = df['Close'].rolling(self.short_window).mean()
        long_sma = df['Close'].rolling(self.long_window).mean()

        # +1 while short is above long, -1 while below (after both SMAs exist).
        regime = (short_sma > long_sma).astype(int) - (short_sma < long_sma).astype(int)
        regime[short_sma.isna() | long_sma.isna()] = 0

        # Emit a signal only on the bar where the regime flips, so the engine
        # acts once per crossover instead of every bar.
        df['signal'] = regime.diff().fillna(0).clip(-1, 1).astype(int)
        df['sma_short'] = short_sma
        df['sma_long'] = long_sma
        return df
