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

    for sym, df in data.items():
        sig_df = strategy.generate_signals(df)
        if sig_df.empty:
            continue

        last_ts = str(sig_df.index[-1])

        # Idempotency: skip if we already acted on this bar.
        if broker.last_bar(sym) and last_ts <= broker.last_bar(sym):
            continue

        signal = int(sig_df['signal'].iloc[-1])
        price = float(sig_df['Close'].iloc[-1])

        order = decide_order(
            signal=signal,
            symbol=sym,
            price=price,
            positions=positions,
            cash=broker.get_cash(),
            position_size=strategy.position_size,
            capital_per_symbol=capital_per_symbol,
        )

        if order:
            receipt = broker.place_order(sym, order["side"], order["qty"], price, last_ts)
            log.info("[%s] %s %d × %s @ %.2f", strategy.name,
                     order["side"], order["qty"], sym, price)
            positions = broker.get_positions()  # refresh after trade
            orders_placed += 1

        broker.set_last_bar(sym, last_ts)

    broker.save()
    log.info("[%s] Cycle done -- %d orders, cash=%.2f, positions=%d symbols.",
             strategy.name, orders_placed, broker.get_cash(), len(broker.get_positions()))
