import json
from pathlib import Path

from ..backtester.portfolio import Portfolio
from .broker_base import BrokerBase


class PaperBroker(BrokerBase):
    """Simulated broker with JSON-persisted state for paper trading.

    Each strategy gets its own state file at  state/<strategy_name>.json
    containing cash, positions, trades, an equity history (timestamp ->
    portfolio value, populated by mark_equity()) and the last-processed
    bar timestamp per symbol (used for idempotency — prevents acting on
    the same bar twice).
    """

    def __init__(self, strategy_name: str, initial_capital: float,
                 state_dir: Path | str = "state"):
        self.strategy_name = strategy_name
        self._state_path = Path(state_dir) / f"{strategy_name}.json"
        self._initial_capital = initial_capital
        self._portfolio = Portfolio(initial_capital)
        self._last_bars: dict[str, str] = {}   # symbol -> last bar timestamp (ISO)
        self._equity_history: dict[str, float] = {}  # timestamp (ISO) -> portfolio value
        self._signal_log: list[dict] = []      # per-bar signal records (diagnostics)
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
            "equity_history":  self._equity_history,
            "signal_log":      self._signal_log,
            # New: cumulative traded notional and fees paid (for turnover metric).
            "total_traded_notional":    getattr(self._portfolio, "total_traded_notional", 0.0),
            "total_transaction_costs":  getattr(self._portfolio, "total_transaction_costs", 0.0),
        }
        self._state_path.write_text(json.dumps(data, indent=2, default=str))

    # -- idempotency helpers --

    def last_bar(self, symbol: str) -> str | None:
        return self._last_bars.get(symbol)

    def set_last_bar(self, symbol: str, ts):
        self._last_bars[symbol] = str(ts)

    # -- walk-forward / live diagnostics --

    def mark_equity(self, ts, prices: dict):
        """Record portfolio value at this timestamp for the equity history."""
        value = self._portfolio.portfolio_value(prices)
        self._equity_history[str(ts)] = value
        return value

    def accrue_interest(self, days: float = 1.0):
        """Accrue daily-compounded interest on idle cash at the configured rate.

        Called once per bar by the walk-forward simulator (and could be called
        per cycle by the live engine too). Skipped when cash is non-positive
        or when CASH_INTEREST_RATE_ANNUAL is zero.
        """
        from ..config import settings
        rate = getattr(settings, "CASH_INTEREST_RATE_ANNUAL", 0.0)
        if rate <= 0 or self._portfolio.cash <= 0:
            return 0.0
        # Daily compounding: (1+r)^(1/252) - 1 per trading day.
        daily_factor = (1.0 + rate) ** (days / 252.0) - 1.0
        interest = self._portfolio.cash * daily_factor
        self._portfolio.cash += interest
        return interest

    def log_signal(self, ts, symbol: str, signal: int, price: float,
                   action: str | None = None):
        """Append a signal-generation record to the in-memory log."""
        self._signal_log.append({
            "ts": str(ts), "symbol": symbol, "signal": int(signal),
            "price": float(price),
            "action": action or "noop",
        })

    @property
    def equity_history(self) -> dict[str, float]:
        return self._equity_history

    @property
    def signal_log(self) -> list[dict]:
        return self._signal_log

    @property
    def portfolio(self) -> Portfolio:
        return self._portfolio

    @property
    def initial_capital(self) -> float:
        return self._initial_capital

    # -- per-position metadata passthrough (for ATR-stop strategies) --

    def get_meta(self, symbol: str) -> dict:
        return self._portfolio.get_meta(symbol)

    def set_meta(self, symbol: str, **kwargs):
        self._portfolio.set_meta(symbol, **kwargs)

    def update_meta(self, symbol: str, **kwargs):
        self._portfolio.update_meta(symbol, **kwargs)

    def clear_meta(self, symbol: str):
        self._portfolio.clear_meta(symbol)

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
        self._equity_history = raw.get("equity_history", {})
        self._signal_log = raw.get("signal_log", [])
        self._portfolio.total_traded_notional = raw.get("total_traded_notional", 0.0)
        self._portfolio.total_transaction_costs = raw.get("total_transaction_costs", 0.0)


def _serialise_trade(t: dict) -> dict:
    """Ensure every value in a trade record is JSON-safe."""
    return {k: str(v) if not isinstance(v, (int, float, str, bool, type(None))) else v
            for k, v in t.items()}
