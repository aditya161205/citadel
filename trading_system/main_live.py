import sys

from .config import settings
from .live_trader.live_engine import run_once
from .live_trader.paper_broker import PaperBroker
from .live_trader.scheduler import run_loop
from .strategies import discover_strategies
from .utils.logger import get_logger

log = get_logger("main_live")


def main():
    once = "--once" in sys.argv

    strategy_classes = discover_strategies()
    if not strategy_classes:
        log.error("No strategies found in strategies/. Nothing to run.")
        return

    # Instantiate each strategy with its own PaperBroker.
    jobs = []
    for cls in strategy_classes:
        strat = cls()
        broker = PaperBroker(
            strategy_name=strat.name,
            initial_capital=strat.initial_capital,
            state_dir=settings.STATE_DIR,
        )
        jobs.append((strat, broker, strat.interval))
        log.info("Loaded strategy [%s] interval=%s universe=%s",
                 strat.name, strat.interval, strat.universe)

    if once:
        log.info("Running a single cycle for all strategies (--once)...")
        for strat, broker, _ in jobs:
            try:
                run_once(strat, broker)
            except Exception:
                log.exception("Error running [%s]", strat.name)
    else:
        log.info("Starting scheduler loop (Ctrl+C to stop)...")
        run_loop(jobs)


if __name__ == "__main__":
    main()
