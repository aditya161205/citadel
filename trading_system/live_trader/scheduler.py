import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import settings
from ..utils.logger import get_logger

log = get_logger("scheduler")

IST = ZoneInfo(settings.TIMEZONE)

# Parse market times once.
_MARKET_OPEN  = datetime.strptime(settings.MARKET_OPEN, "%H:%M").time()
_MARKET_CLOSE = datetime.strptime(settings.MARKET_CLOSE, "%H:%M").time()
_EOD_RUN      = datetime.strptime(settings.EOD_RUN_TIME, "%H:%M").time()

# Interval string → minutes.
_INTERVAL_MINS = {
    "1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "90m": 90,
}


def is_trading_day(dt: datetime) -> bool:
    """Weekday and not in the holiday list."""
    if dt.weekday() >= 5:
        return False
    return dt.strftime("%Y-%m-%d") not in settings.NSE_HOLIDAYS


def next_run_time(interval: str, now: datetime) -> datetime:
    """Compute the next appropriate run time for a given interval.

    EOD ("1d"): next trading day at EOD_RUN_TIME (or today if not yet past).
    Intraday:   next interval boundary within market hours on a trading day.
    """
    now_ist = now.astimezone(IST)

    if interval == "1d":
        return _next_eod(now_ist)

    mins = _INTERVAL_MINS.get(interval)
    if mins is None:
        raise ValueError(f"Unsupported interval for scheduling: {interval}")
    return _next_intraday(now_ist, mins)


def _next_eod(now: datetime) -> datetime:
    candidate = now.replace(hour=_EOD_RUN.hour, minute=_EOD_RUN.minute,
                            second=0, microsecond=0)
    if now.time() >= _EOD_RUN or not is_trading_day(now):
        candidate += timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def _next_intraday(now: datetime, interval_mins: int) -> datetime:
    """Next interval-aligned time within market hours."""
    # If currently in market hours, align to next interval boundary.
    if is_trading_day(now) and _MARKET_OPEN <= now.time() < _MARKET_CLOSE:
        minute = now.minute
        next_min = (minute // interval_mins + 1) * interval_mins
        candidate = now.replace(second=0, microsecond=0) + timedelta(
            minutes=next_min - minute)
        if candidate.time() <= _MARKET_CLOSE:
            return candidate

    # Otherwise, find next trading day's market open.
    candidate = now.replace(hour=_MARKET_OPEN.hour, minute=_MARKET_OPEN.minute,
                            second=0, microsecond=0)
    if now.time() >= _MARKET_OPEN or not is_trading_day(now):
        candidate += timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def run_loop(jobs: list):
    """Infinite scheduling loop.

    jobs: list of (strategy_instance, broker, interval_str) tuples.
    Sleeps until the next due job, runs it, reschedules, repeat.
    """
    from .live_engine import run_once  # deferred to avoid circular import

    # Build schedule: {id: (strategy, broker, next_dt)}
    schedule = {}
    for i, (strat, broker, interval) in enumerate(jobs):
        nxt = next_run_time(interval, datetime.now(IST))
        schedule[i] = (strat, broker, interval, nxt)
        log.info("Scheduled [%s] (%s) -- next run at %s IST",
                 strat.name, interval, nxt.strftime("%Y-%m-%d %H:%M"))

    try:
        while True:
            # Find the soonest job.
            soonest_id = min(schedule, key=lambda k: schedule[k][3])
            strat, broker, interval, run_at = schedule[soonest_id]

            now = datetime.now(IST)
            wait = (run_at - now).total_seconds()
            if wait > 0:
                log.info("Sleeping %.0fs until [%s] at %s",
                         wait, strat.name, run_at.strftime("%H:%M"))
                time.sleep(wait)

            log.info("Running [%s] ...", strat.name)
            try:
                run_once(strat, broker)
            except Exception:
                log.exception("Error running [%s]", strat.name)

            # Reschedule.
            nxt = next_run_time(interval, datetime.now(IST))
            schedule[soonest_id] = (strat, broker, interval, nxt)
            log.info("Next [%s] at %s IST", strat.name, nxt.strftime("%Y-%m-%d %H:%M"))

    except KeyboardInterrupt:
        log.info("Scheduler stopped (Ctrl+C).")
