from abc import ABC, abstractmethod
import pandas as pd


class StrategyBase(ABC):
    """Interface every strategy implements.

    Subclass and implement generate_signals to plug a new strategy into the
    backtest engine.
    """

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Return data with an added integer 'signal' column: 1=buy, -1=sell, 0=hold."""
        raise NotImplementedError
