from datetime import time

import pandas as pd
import matplotlib.pyplot as plt
from .portfolio import Portfolio
from .metrics import compute_metrics


def _parse_hhmm(s: str | None) -> time | None:
    """Turn 'HH:MM' string into a datetime.time; None passes through."""
    if s is None:
        return None
    h, m = s.split(":")
    return time(int(h), int(m))


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
        # Stash the symbol on the data so multi-resolution strategies can look it up.
        self.data.attrs['symbol'] = self.symbol
        df = self.strategy.generate_signals(self.data)
        # Three contracts, in priority order:
        #   - atr_sl_mult set      → _run_with_stops    (ATR-based exits)
        #   - 'position' column    → _run_state_based   (long/flat/short)
        #   - 'signal' column      → _run_transition_based (legacy long-only)
        if getattr(self.strategy, 'atr_sl_mult', None) is not None:
            return self._run_with_stops(df)
        if 'position' in df.columns:
            return self._run_state_based(df)
        return self._run_transition_based(df)

    def _run_transition_based(self, df):
        """Legacy long-only path: 'signal' is a transition (+1 buy, -1 close).

        Optional 'size_factor' column (0..1) scales position_size per entry --
        lets a strategy dynamically size entries by, e.g., trend strength.
        """
        if 'signal' not in df.columns:
            df['signal'] = 0
        # No-look-ahead: signal at T is acted on at T+1's Open.
        exec_signal = df['signal'].shift(1).fillna(0).astype(int)

        if 'size_factor' in df.columns:
            size_at_entry = df['size_factor'].shift(1).fillna(0).clip(0, 1)
        else:
            size_at_entry = pd.Series(1.0, index=df.index)

        equity_curve = []
        for date, row in df.iterrows():
            signal = int(exec_signal.loc[date])
            fill_price = row['Open'] if pd.notna(row.get('Open')) else row['Close']
            close_price = row['Close']

            if signal == 1:
                effective_size = self.position_size * float(size_at_entry.loc[date])
                qty = int((self.portfolio.cash * effective_size) // fill_price)
                self.portfolio.buy(self.symbol, fill_price, qty, date)
            elif signal == -1:
                self.portfolio.sell(self.symbol, fill_price, date)

            equity_curve.append({
                'date': date,
                'equity': self.portfolio.portfolio_value({self.symbol: close_price})
            })

        self.equity_curve = pd.DataFrame(equity_curve).set_index('date')
        return self

    def _run_state_based(self, df):
        """Long/flat/short path: 'position' is the target state at each bar."""
        # No-look-ahead: target generated at T is reached at T+1's Open.
        target = df['position'].fillna(0).astype(int).shift(1).fillna(0).astype(int)

        equity_curve = []
        for date, row in df.iterrows():
            tgt = int(target.loc[date])
            fill_price = row['Open'] if pd.notna(row.get('Open')) else row['Close']
            close_price = row['Close']

            held = self.portfolio.positions.get(self.symbol, 0)
            current = 1 if held > 0 else (-1 if held < 0 else 0)

            if tgt != current:
                # Close whatever is open.
                if current == 1:
                    self.portfolio.sell(self.symbol, fill_price, date)
                elif current == -1:
                    self.portfolio.cover(self.symbol, fill_price, date)
                # Open the new side, if any.
                if tgt == 1:
                    qty = int((self.portfolio.cash * self.position_size) // fill_price)
                    self.portfolio.buy(self.symbol, fill_price, qty, date)
                elif tgt == -1:
                    qty = int((self.portfolio.cash * self.position_size) // fill_price)
                    self.portfolio.short(self.symbol, fill_price, qty, date)

            equity_curve.append({
                'date': date,
                'equity': self.portfolio.portfolio_value({self.symbol: close_price})
            })

        self.equity_curve = pd.DataFrame(equity_curve).set_index('date')
        return self

    def _run_with_stops(self, df):
        """ATR-stop path. Signal column is entry-only (1 = enter long); exits are
        managed by the engine via SL / TP / trailing stop / time-stop, checked
        against each bar's High and Low.
        """
        s = self.strategy
        sl_mult       = float(s.atr_sl_mult)
        tp_mult       = float(s.atr_tp_mult) if s.atr_tp_mult is not None else None
        trail_act     = float(s.trail_activation_atr)
        trail_dist    = float(s.trail_distance_atr)
        min_stop_pct  = float(s.min_stop_pct)

        entry_window = s.entry_window
        if entry_window:
            w_start = _parse_hhmm(entry_window[0])
            w_end   = _parse_hhmm(entry_window[1])
        else:
            w_start = w_end = None
        eod_time = _parse_hhmm(s.eod_flatten_time)

        if 'signal' not in df.columns:
            df['signal'] = 0
        # No-look-ahead: a signal at bar T is acted on at T+1.
        exec_signal = df['signal'].shift(1).fillna(0).astype(int)
        # ATR at the bar that fired the signal (so it's known when we execute next bar).
        entry_atr_series = df['atr'].shift(1) if 'atr' in df.columns else None

        equity_curve = []
        for date, row in df.iterrows():
            open_p  = row.get('Open')
            high_p  = row.get('High')
            low_p   = row.get('Low')
            close_p = row['Close']
            if pd.isna(open_p):
                open_p = close_p
            # Convert UTC-aware hourly timestamps to IST for the time-of-day checks.
            ts = pd.Timestamp(date)
            if ts.tz is not None:
                ts = ts.tz_convert("Asia/Kolkata")
            bar_time = ts.time() if hasattr(ts, 'time') else None

            held = self.portfolio.positions.get(self.symbol, 0)

            # -- 1. Check exits on any open position --
            if held > 0:
                meta = self.portfolio.get_meta(self.symbol)
                sl = meta['sl']
                tp = meta.get('tp')
                exit_price = None

                # Track high-water close for the trailing stop.
                if close_p > meta.get('highest_close', meta['entry_price']):
                    self.portfolio.update_meta(self.symbol, highest_close=close_p)
                    meta = self.portfolio.get_meta(self.symbol)

                # Stop loss: low pierced the stop. Fill at sl (or worse Open on gap-down).
                if not pd.isna(low_p) and low_p <= sl:
                    exit_price = min(sl, open_p) if open_p < sl else sl
                # Take profit: high reached the target.
                elif tp is not None and not pd.isna(high_p) and high_p >= tp:
                    exit_price = max(tp, open_p) if open_p > tp else tp
                else:
                    # Trailing stop maintenance.
                    entry_price = meta['entry_price']
                    entry_atr   = meta['entry_atr']
                    if not meta.get('trail_active') and \
                       (close_p - entry_price) >= trail_act * entry_atr:
                        self.portfolio.update_meta(
                            self.symbol, trail_active=True,
                            trail_stop=meta['highest_close'] - trail_dist * entry_atr)
                        meta = self.portfolio.get_meta(self.symbol)
                    if meta.get('trail_active'):
                        new_trail = meta['highest_close'] - trail_dist * entry_atr
                        if new_trail > meta.get('trail_stop', new_trail):
                            self.portfolio.update_meta(self.symbol, trail_stop=new_trail)
                            meta = self.portfolio.get_meta(self.symbol)
                        if close_p <= meta['trail_stop']:
                            exit_price = close_p

                    # End-of-session time-stop.
                    if exit_price is None and eod_time and bar_time and \
                       bar_time >= eod_time:
                        exit_price = close_p

                if exit_price is not None:
                    self.portfolio.sell(self.symbol, exit_price, date)
                    self.portfolio.clear_meta(self.symbol)
                    held = 0

            # -- 2. Process new entries on this bar (post-exit, so flipping is allowed) --
            if held == 0 and int(exec_signal.loc[date]) == 1:
                # Entry-window filter (intraday).
                in_window = True
                if w_start and w_end and bar_time:
                    in_window = w_start <= bar_time <= w_end
                # Need a valid entry ATR.
                entry_atr = float(entry_atr_series.loc[date]) if entry_atr_series is not None else None
                if in_window and entry_atr and entry_atr > 0 and not pd.isna(open_p):
                    qty = int((self.portfolio.cash * self.position_size) // open_p)
                    if qty > 0:
                        self.portfolio.buy(self.symbol, open_p, qty, date)
                        sl_dist = max(sl_mult * entry_atr, open_p * min_stop_pct)
                        sl = open_p - sl_dist
                        tp = open_p + tp_mult * entry_atr if tp_mult is not None else None
                        self.portfolio.set_meta(
                            self.symbol,
                            entry_price=open_p, entry_ts=date, entry_atr=entry_atr,
                            sl=sl, tp=tp,
                            highest_close=open_p,
                            trail_active=False, trail_stop=None,
                        )

            equity_curve.append({
                'date': date,
                'equity': self.portfolio.portfolio_value({self.symbol: close_p})
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
