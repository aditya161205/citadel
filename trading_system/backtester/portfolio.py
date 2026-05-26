class Portfolio:
    def __init__(self, initial_capital: float = 100_000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions = {}   # symbol -> quantity
        self.trades = []
        # Per-position bookkeeping for stop-based strategies (entry price/ATR,
        # current SL/TP/trail state). Empty for signal-only strategies.
        self.position_meta: dict[str, dict] = {}

    def buy(self, symbol: str, price: float, qty: int, date):
        cost = price * qty
        if qty <= 0 or cost > self.cash:
            return
        self.cash -= cost
        self.positions[symbol] = self.positions.get(symbol, 0) + qty
        self.trades.append({
            'date': date, 'symbol': symbol, 'side': 'BUY',
            'price': price, 'qty': qty, 'cash': self.cash,
        })

    def sell(self, symbol: str, price: float, date, qty: int | None = None):
        """Sell shares. qty=None (default) closes the full long position."""
        held = self.positions.get(symbol, 0)
        if held <= 0:
            return
        sell_qty = held if qty is None else min(qty, held)
        self.cash += price * sell_qty
        self.positions[symbol] = held - sell_qty
        self.trades.append({
            'date': date, 'symbol': symbol, 'side': 'SELL',
            'price': price, 'qty': sell_qty, 'cash': self.cash,
        })

    def short(self, symbol: str, price: float, qty: int, date):
        """Open a short: sell shares we don't own. Receive cash now; owe shares later."""
        if qty <= 0:
            return
        self.cash += price * qty                                # proceeds from short sale
        self.positions[symbol] = self.positions.get(symbol, 0) - qty
        self.trades.append({
            'date': date, 'symbol': symbol, 'side': 'SHORT',
            'price': price, 'qty': qty, 'cash': self.cash,
        })

    def cover(self, symbol: str, price: float, date, qty: int | None = None):
        """Buy back shorted shares. qty=None covers the full short."""
        short_qty = -self.positions.get(symbol, 0)              # positive if short
        if short_qty <= 0:
            return
        cover_qty = short_qty if qty is None else min(qty, short_qty)
        self.cash -= price * cover_qty                          # pay to close
        self.positions[symbol] = self.positions.get(symbol, 0) + cover_qty
        self.trades.append({
            'date': date, 'symbol': symbol, 'side': 'COVER',
            'price': price, 'qty': cover_qty, 'cash': self.cash,
        })

    def portfolio_value(self, prices: dict) -> float:
        """Cash + mark-to-market of all positions. Works for shorts (negative qty)."""
        holdings = sum(qty * prices.get(sym, 0) for sym, qty in self.positions.items())
        return self.cash + holdings

    # -- per-position metadata (used by ATR-stop engine path) --

    def set_meta(self, symbol: str, **kwargs):
        """Replace the meta dict for symbol entirely with kwargs."""
        self.position_meta[symbol] = dict(kwargs)

    def update_meta(self, symbol: str, **kwargs):
        """Merge kwargs into the existing meta dict for symbol."""
        self.position_meta.setdefault(symbol, {}).update(kwargs)

    def get_meta(self, symbol: str) -> dict:
        """Return meta for symbol, or empty dict if none."""
        return self.position_meta.get(symbol, {})

    def clear_meta(self, symbol: str):
        self.position_meta.pop(symbol, None)
