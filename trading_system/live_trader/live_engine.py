from ..marketdata import get_data_source, resolve_universe
from ..utils.logger import get_logger
from .order_manager import decide_order
from .paper_broker import PaperBroker

log = get_logger("paper_engine")


def run_once(strategy, broker: PaperBroker):
    """Execute one cycle of the paper-trading loop for a single strategy.

    Fetches recent data, generates signals, and places orders for any symbol
    with a new bar and a non-zero signal.  Safe to call repeatedly -- the
    last-bar guard prevents acting on the same bar twice.
    """
    # Composite strategies don't have a per-symbol signal stream -- they're
    # portfolios of sub-strategies. The walk-forward simulator handles them
    # via run_blend(); the live loop just skips them so it doesn't crash.
    if getattr(strategy, "is_composite", False):
        log.info("[%s] composite strategy -- skipped in live loop (use walk-forward).",
                 strategy.name)
        return

    symbols = resolve_universe(strategy.universe)
    source = get_data_source(strategy.data_source)
    data = source.get_recent(symbols, interval=strategy.interval, warmup=strategy.warmup)

    if not data:
        log.warning("[%s] No data returned -- skipping cycle.", strategy.name)
        return

    n_universe = len(symbols)
    capital_per_symbol = strategy.initial_capital / n_universe
    positions = broker.get_positions()
    orders_placed = 0
    last_close_prices: dict[str, float] = {}
    cycle_ts = None

    for sym, df in data.items():
        # Stash the symbol BEFORE generate_signals so multi-resolution
        # strategies (e.g. bb_pullback) can look it up inside the call.
        df.attrs['symbol'] = sym
        sig_df = strategy.generate_signals(df)
        if sig_df.empty:
            continue

        last_ts = str(sig_df.index[-1])
        cycle_ts = last_ts

        # Idempotency: skip if we already acted on this bar.
        if broker.last_bar(sym) and last_ts <= broker.last_bar(sym):
            continue

        signal = int(sig_df['signal'].iloc[-1])
        price = float(sig_df['Close'].iloc[-1])
        last_close_prices[sym] = price

        order = decide_order(
            signal=signal,
            symbol=sym,
            price=price,
            positions=positions,
            cash=broker.get_cash(),
            position_size=strategy.position_size,
            capital_per_symbol=capital_per_symbol,
        )

        action = "noop"
        if order:
            broker.place_order(sym, order["side"], order["qty"], price, last_ts)
            action = order["side"]
            log.info("[%s] %s %d × %s @ %.2f", strategy.name,
                     order["side"], order["qty"], sym, price)
            positions = broker.get_positions()  # refresh after trade
            orders_placed += 1

        # Always record the signal so we can see what fired even when no trade
        # was placed (e.g. signal=1 but already in position).
        if signal != 0 or action != "noop":
            broker.log_signal(last_ts, sym, signal, price, action)

        broker.set_last_bar(sym, last_ts)

    # Mark portfolio value with the latest prices we saw this cycle so the
    # equity history grows by one point per cycle.
    if cycle_ts and last_close_prices:
        broker.mark_equity(cycle_ts, last_close_prices)

    broker.save()
    log.info("[%s] Cycle done -- %d orders, cash=%.2f, positions=%d symbols.",
             strategy.name, orders_placed, broker.get_cash(), len(broker.get_positions()))
