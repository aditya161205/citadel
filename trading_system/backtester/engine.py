import pandas as pd
import matplotlib.pyplot as plt
from .portfolio import Portfolio
from .metrics import compute_metrics

class BacktestEngine:
    def __init__(self, strategy, data: pd.DataFrame,
                 symbol: str, initial_capital: float = 100_000.0,
                 position_size: float = 0.95):
        self.strategy = strategy
        self.data = data
        self.symbol = symbol
        self.portfolio = Portfolio(initial_capital)
        self.position_size = position_size  # fraction of cash to deploy

    def run(self):
        df = self.strategy.generate_signals(self.data)
        equity_curve = []

        for date, row in df.iterrows():
            price = row['Close']
            signal = row.get('signal', 0)

            if signal == 1:   # BUY
                qty = int((self.portfolio.cash * self.position_size) // price)
                self.portfolio.buy(self.symbol, price, qty, date)

            elif signal == -1:  # SELL
                self.portfolio.sell(self.symbol, price, date)

            equity_curve.append({
                'date': date,
                'equity': self.portfolio.portfolio_value({self.symbol: price})
            })

        self.equity_curve = pd.DataFrame(equity_curve).set_index('date')
        return self

    def report(self):
        metrics = compute_metrics(self.equity_curve['equity'],
                                   self.portfolio.initial_capital)
        print("\n===== BACKTEST REPORT =====")
        for k, v in metrics.items():
            print(f"  {k:<25}: {v}")
        print("===========================\n")
        return metrics

    def plot(self):
        self.equity_curve['equity'].plot(title='Equity Curve', figsize=(12, 5))
        plt.ylabel('Portfolio Value ($)')
        plt.tight_layout()
        plt.show()
