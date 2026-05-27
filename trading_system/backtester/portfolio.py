from ..config import settings


def _cost(notional: float) -> float:
    """Transaction cost in cash, scaled by the per-side rate in settings."""
    rate = getattr(settings, "TRANSACTION_COST_RATE", 0.0)
    return abs(notional) * rate if rate > 0 else 0.0


class Portfolio:
    def __init__(self, initial_capital: float = 100_000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions = {}                # symbol -> quantity (negative = short)
        self.trades = []
        # Per-position bookkeeping for stop-based strategies.
        self.position_meta: dict[str, dict] = {}
        # Total absolute notional traded across all fills (used for turnover).
        self.total_traded_notional = 0.0
        # Total transaction costs paid (visible in the report).
        self.total_transaction_costs = 0.0

    # -- internal helpers --

    def _record_trade(self, *, date, symbol, side, price, qty, fee):
        self.trades.append({
            'date': date, 'symbol': symbol, 'side': side,
            'price': price, 'qty': qty, 'fee': fee, 'cash': self.cash,
        })
        self.total_traded_notional += abs(price * qty)
        self.total_transaction_costs += fee

    # -- long-side --

    def buy(self, symbol: str, price: float, qty: int, date):
        if qty <= 0:
            return
        gross = price * qty
        fee = _cost(gross)
        total = gross + fee
        if total > self.cash:
            return
        self.cash -= total
        self.positions[symbol] = self.positions.get(symbol, 0) + qty
        self._record_trade(date=date, symbol=symbol, side='BUY',
                           price=price, qty=qty, fee=fee)

    def sell(self, symbol: str, price: float, date, qty: int | None = None):
        """Sell shares. qty=None (default) closes the full long position."""
        held = self.positions.get(symbol, 0)
        if held <= 0:
            return
        sell_qty = held if qty is None else min(qty, held)
        gross = price * sell_qty
        fee = _cost(gross)
        proceeds = gross - fee
        self.cash += proceeds
        self.positions[symbol] = held - sell_qty
        self._record_trade(date=date, symbol=symbol, side='SELL',
                           price=price, qty=sell_qty, fee=fee)

    # -- short-side --

    def short(self, symbol: str, price: float, qty: int, date):
        """Open a short: sell shares we don't own. Receive cash now; owe shares later."""
        if qty <= 0:
            return
        gross = price * qty
        fee = _cost(gross)
        proceeds = gross - fee
        self.cash += proceeds
        self.positions[symbol] = self.positions.get(symbol, 0) - qty
        self._record_trade(date=date, symbol=symbol, side='SHORT',
                           price=price, qty=qty, fee=fee)

    def cover(self, symbol: str, price: float, date, qty: int | None = None):
        """Buy back shorted shares. qty=None covers the full short."""
        short_qty = -self.positions.get(symbol, 0)                # positive if short
        if short_qty <= 0:
            return
        cover_qty = short_qty if qty is None else min(qty, short_qty)
        gross = price * cover_qty
        fee = _cost(gross)
        total = gross + fee
        self.cash -= total                                         # pay to close
        self.positions[symbol] = self.positions.get(symbol, 0) + cover_qty
        self._record_trade(date=date, symbol=symbol, side='COVER',
                           price=price, qty=cover_qty, fee=fee)

    # -- valuation --

    def portfolio_value(self, prices: dict) -> float:
        """Cash + mark-to-market of all positions. Works for shorts (negative qty)."""
        holdings = sum(qty * prices.get(sym, 0) for sym, qty in self.positions.items())
        return self.cash + holdings

    # -- per-position metadata (used by ATR-stop engine path) --

    def set_meta(self, symbol: str, **kwargs):
        self.position_meta[symbol] = dict(kwargs)

    def update_meta(self, symbol: str, **kwargs):
        self.position_meta.setdefault(symbol, {}).update(kwargs)

    def get_meta(self, symbol: str) -> dict:
        return self.position_meta.get(symbol, {})

    def clear_meta(self, symbol: str):
        self.position_meta.pop(symbol, None)
