import json
from pathlib import Path

from ..backtester.portfolio import Portfolio
from .broker_base import BrokerBase


class PaperBroker(BrokerBase):
    """Simulated broker with JSON-persisted state for paper trading.

    Each strategy gets its own state file at  state/<strategy_name>.json
    containing cash, positions, trades, and the last-processed bar timestamp
    per symbol (used for idempotency — prevents acting on the same bar twice).
    """

    def __init__(self, strategy_name: str, initial_capital: float,
                 state_dir: Path | str = "state"):
        self.strategy_name = strategy_name
        self._state_path = Path(state_dir) / f"{strategy_name}.json"
        self._initial_capital = initial_capital
        self._portfolio = Portfolio(initial_capital)
        self._last_bars: dict[str, str] = {}   # symbol -> last bar timestamp (ISO)
        self.load()

    # -- BrokerBase interface --

    def place_order(self, symbol, side, qty, price, ts):
        if side == "BUY":
            self._portfolio.buy(symbol, price, qty, ts)
        elif side == "SELL":
            self._portfolio.sell(symbol, price, ts, qty=None)  # close full
        return {"symbol": symbol, "side": side, "qty": qty,
                "price": price, "ts": str(ts)}

    def get_positions(self):
        return {s: q for s, q in self._portfolio.positions.items() if q > 0}

    def get_cash(self):
        return self._portfolio.cash

    def portfolio_value(self, prices):
        return self._portfolio.portfolio_value(prices)

    def save(self):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "initial_capital": self._initial_capital,
            "cash":            self._portfolio.cash,
            "positions":       self._portfolio.positions,
            "trades":          [_serialise_trade(t) for t in self._portfolio.trades],
            "last_bars":       self._last_bars,
        }
        self._state_path.write_text(json.dumps(data, indent=2, default=str))

    # -- idempotency helpers --

    def last_bar(self, symbol: str) -> str | None:
        return self._last_bars.get(symbol)

    def set_last_bar(self, symbol: str, ts):
        self._last_bars[symbol] = str(ts)

    # -- persistence --

    def load(self):
        if not self._state_path.exists():
            return
        raw = json.loads(self._state_path.read_text())
        self._portfolio.cash = raw["cash"]
        self._portfolio.positions = raw.get("positions", {})
        # Trades are kept as-is (already serialised); only new trades get appended.
        self._portfolio.trades = raw.get("trades", [])
        self._last_bars = raw.get("last_bars", {})


def _serialise_trade(t: dict) -> dict:
    """Ensure every value in a trade record is JSON-safe."""
    return {k: str(v) if not isinstance(v, (int, float, str, bool, type(None))) else v
            for k, v in t.items()}
