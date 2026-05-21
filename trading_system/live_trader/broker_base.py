from abc import ABC, abstractmethod


class BrokerBase(ABC):
    """Interface for all brokers (paper, Zerodha, Alpaca…).

    Implement this to add a new broker.  The paper engine and scheduler only
    talk through this interface, so swapping brokers requires no engine changes.
    """

    @abstractmethod
    def place_order(self, symbol: str, side: str, qty: int, price: float, ts) -> dict:
        """Execute an order. Returns an order-receipt dict."""
        ...

    @abstractmethod
    def get_positions(self) -> dict[str, int]:
        """Current holdings: {symbol: qty}."""
        ...

    @abstractmethod
    def get_cash(self) -> float:
        ...

    @abstractmethod
    def portfolio_value(self, prices: dict[str, float]) -> float:
        ...

    @abstractmethod
    def save(self):
        """Persist state to disk (for paper) / no-op for real brokers."""
        ...
