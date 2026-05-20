class Portfolio:
    def __init__(self, initial_capital: float = 100_000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions = {}   # symbol -> quantity
        self.trades = []

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

    def sell(self, symbol: str, price: float, date):
        qty = self.positions.get(symbol, 0)
        if qty <= 0:
            return
        self.cash += price * qty
        self.positions[symbol] = 0
        self.trades.append({
            'date': date, 'symbol': symbol, 'side': 'SELL',
            'price': price, 'qty': qty, 'cash': self.cash,
        })

    def portfolio_value(self, prices: dict) -> float:
        holdings = sum(qty * prices.get(sym, 0) for sym, qty in self.positions.items())
        return self.cash + holdings
