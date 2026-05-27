"""Walk-forward paper trading simulator.

What this is
------------
A walk-forward "out-of-sample" test that replays the paper-trading pipeline
bar by bar over a window the strategy never saw during development.

  Training (backtest baseline) window : settings.START_DATE  -> TRAIN_END_DATE
  Paper-trading (walk-forward) window  : PAPER_START_DATE    -> today

For each bar in the paper window we do EXACTLY what live paper trading would
do at that moment in time:

  1. Look at history up to (and including) that bar.
  2. Run the strategy's generate_signals() on that view.
  3. Translate the last signal into an order via decide_order().
  4. Fill the order through the same PaperBroker that the live loop uses.
  5. Mark the portfolio's equity at the close, and append a signal-log row.

So the resulting state file (state/<name>_paper.json) is what the broker
*would have* held if it had been live throughout the paper window.

Why not just call live_engine.run_once() in a loop?
---------------------------------------------------
run_once() always asks the data source for the *most recent* slice. For a
walk-forward we need to feed historical slices as if they were "the latest
bar at time T". So we fetch the whole paper window up-front and slice in
Python — one yfinance call per strategy, then a fast in-memory replay.

Composite (blend) strategies
----------------------------
Composites declare is_composite = True and orchestrate sub-strategies. We
run the walk-forward on each sub-strategy independently with its allocated
capital, then sum their equity curves into a combined paper-trading equity
series (mirrors how main_backtest.run_composite() works).

Run
---
  py -m trading_system.paper_walkforward                      # all strategies
  py -m trading_system.paper_walkforward ema_rsi              # just one
  py -m trading_system.paper_walkforward --reset              # wipe state first
  py -m trading_system.paper_walkforward --regime             # apply regime filter
  py -m trading_system.paper_walkforward --rolling 6          # 6-month OOS slices
  py -m trading_system.paper_walkforward --rolling 6 --regime # both at once
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .backtester.metrics import compute_metrics, compute_metrics_raw
from .backtester.portfolio import Portfolio
from .config import settings
from .live_trader.order_manager import decide_order
from .live_trader.paper_broker import PaperBroker
from .marketdata import get_data_source, resolve_universe
from .strategies import discover_strategies
from .utils.logger import get_logger

log = get_logger("paper_walkforward")

# Module-level regime filter cache. Built lazily once per run when the
# --regime flag is on, then queried by each strategy. None means "no filter".
_REGIME = None


def _get_regime():
    """Lazily build the regime filter for the current paper window."""
    global _REGIME
    if _REGIME is None:
        from .regime_filter import RegimeFilter
        _REGIME = RegimeFilter.for_paper_window()
    return _REGIME


def _entries_blocked_by_regime(strategy, ts) -> bool:
    """True if the strategy should skip a NEW entry at ts because the regime
    filter is off. Exits / position management are always allowed."""
    if _REGIME is None:
        return False
    if not getattr(strategy, 'respect_regime_filter', True):
        return False
    return not _REGIME.is_on(ts)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _paper_window() -> tuple[str, str]:
    """Resolve (start, end) for the paper-trading window."""
    start = settings.PAPER_START_DATE
    end = settings.PAPER_END_DATE or date.today().isoformat()
    return start, end


def _fetch_window(strategy, paper_start: str, paper_end: str):
    """Fetch all data needed for the walk-forward: warmup + paper window.

    Returns a dict {symbol -> DataFrame} with the full date range. Each
    bar-step of the simulation slices into this in-memory frame.
    """
    symbols = resolve_universe(strategy.universe)
    source = get_data_source(strategy.data_source)

    # We need `warmup` bars before paper_start so indicators are settled by
    # the time we start trading. For dailies a calendar-day padding is enough.
    if strategy.interval == "1d":
        pad_days = int(strategy.warmup * 1.6) + 30
    else:
        # Intraday: yfinance caps history at ~730d. Always use the full window
        # and skip warmup bars at the start of the loop.
        pad_days = 0
    fetch_start = (pd.Timestamp(paper_start) - pd.Timedelta(days=pad_days)).strftime(
        "%Y-%m-%d") if pad_days else paper_start

    log.info("[%s] Fetching %s data %s -> %s (universe=%d symbols) ...",
             strategy.name, strategy.interval, fetch_start, paper_end, len(symbols))
    data = source.get_history(symbols, start=fetch_start, end=paper_end,
                              interval=strategy.interval)
    log.info("[%s] Got %d / %d symbols.", strategy.name, len(data), len(symbols))
    return data, symbols


def _build_master_timeline(data: dict) -> list[pd.Timestamp]:
    """Union of all symbol indices, sorted. The simulation iterates this."""
    idx = pd.Index([])
    for df in data.values():
        idx = idx.union(df.index)
    return list(idx.sort_values())


# ----------------------------------------------------------------------------
# core: per-strategy walk-forward
# ----------------------------------------------------------------------------

def run_walkforward(strategy, *, reset: bool = False,
                    state_suffix: str = "_paper") -> PaperBroker:
    """Replay paper trading bar by bar over the paper-trading window.

    state_suffix lets composites isolate per-sub-strategy state under their
    own filename (e.g. blend_trend_mr_sub_ema_rsi.json).
    """
    paper_start, paper_end = _paper_window()

    state_name = f"{strategy.name}{state_suffix}"
    state_path = settings.STATE_DIR / f"{state_name}.json"
    if reset and state_path.exists():
        log.info("[%s] --reset: removing %s", strategy.name, state_path)
        state_path.unlink()

    broker = PaperBroker(
        strategy_name=state_name,
        initial_capital=strategy.initial_capital,
        state_dir=settings.STATE_DIR,
    )

    # Portfolio strategies have their own execution path -- they emit target
    # weights for the whole universe rather than per-symbol signals.
    if getattr(strategy, "is_portfolio_strategy", False):
        return _run_portfolio_walkforward(strategy, broker, paper_start, paper_end)

    data, symbols = _fetch_window(strategy, paper_start, paper_end)
    if not data:
        log.warning("[%s] No data -- skipping.", strategy.name)
        return broker

    # Stash symbol on each frame for multi-resolution strategies.
    for sym, df in data.items():
        df.attrs['symbol'] = sym

    # Cache full signal frames once per symbol. This is correct because the
    # backtester's no-look-ahead rule (signal at T executed at T+1's open) is
    # implemented inside the engine — and we mirror it here by reading the
    # signal at bar T but acting at T+1's open. Recomputing per bar would
    # waste time without changing the results for deterministic strategies.
    log.info("[%s] Pre-computing signals across full window ...", strategy.name)
    signal_frames: dict[str, pd.DataFrame] = {}
    for sym, df in data.items():
        try:
            signal_frames[sym] = strategy.generate_signals(df)
        except Exception:
            log.exception("[%s] generate_signals failed for %s -- excluding.",
                          strategy.name, sym)
    if not signal_frames:
        log.warning("[%s] No usable symbols after signal pass.", strategy.name)
        return broker

    timeline = _build_master_timeline(signal_frames)
    paper_start_ts = pd.Timestamp(paper_start)
    if timeline[0].tzinfo is not None:
        paper_start_ts = paper_start_ts.tz_localize(timeline[0].tzinfo)
    test_bars = [t for t in timeline if t >= paper_start_ts]

    log.info("[%s] Walk-forward: %d bars from %s to %s, %d symbols.",
             strategy.name, len(test_bars),
             test_bars[0] if test_bars else "n/a",
             test_bars[-1] if test_bars else "n/a",
             len(signal_frames))

    use_stops = getattr(strategy, "atr_sl_mult", None) is not None
    # Inspect one signal frame to detect which path to use.
    sample_df = next(iter(signal_frames.values()))
    use_state = (not use_stops) and ('position' in sample_df.columns)
    capital_per_symbol = strategy.initial_capital / len(signal_frames)

    # Running price cache so equity marks include positions that didn't trade
    # on this bar. We update it whenever we see a symbol's bar.
    price_cache: dict[str, float] = {}

    # Day-fraction per bar for interest accrual (daily=1 day, hourly=1/6.25).
    if strategy.interval == "1d":
        bar_days = 1.0
    elif strategy.interval == "1h":
        bar_days = 1.0 / 6.25      # NSE trading day is ~6.25 hours (09:15-15:30)
    elif strategy.interval.endswith("m"):
        try:
            mins = int(strategy.interval[:-1])
            bar_days = mins / (6.25 * 60)
        except ValueError:
            bar_days = 1.0
    else:
        bar_days = 1.0

    for i, ts in enumerate(test_bars):
        # Accrue interest on cash held over this bar.
        broker.accrue_interest(days=bar_days)

        for sym, sig_df in signal_frames.items():
            if ts not in sig_df.index:
                continue
            row = sig_df.loc[ts]
            # Update running price cache with this bar's close.
            price_cache[sym] = float(row['Close'])

            # Idempotency: don't re-process a timestamp we already saw.
            ts_str = str(ts)
            if broker.last_bar(sym) and ts_str <= broker.last_bar(sym):
                continue

            if use_stops:
                _step_with_stops(strategy, broker, sym, sig_df, ts,
                                 capital_per_symbol)
            elif use_state:
                _step_state_based(strategy, broker, sym, sig_df, ts,
                                  capital_per_symbol)
            else:
                _step_signal_based(strategy, broker, sym, sig_df, ts,
                                   capital_per_symbol)

            broker.set_last_bar(sym, ts_str)

        # Mark portfolio equity at this bar's close using the running price
        # cache (which includes earlier prices for symbols not in this bar).
        if price_cache:
            broker.mark_equity(ts, price_cache)

        # Periodic state checkpoint (every 100 bars) so a crash mid-run
        # doesn't lose the whole replay.
        if i and i % 100 == 0:
            broker.save()
            log.info("[%s] %d/%d bars processed, cash=%.0f, positions=%d, "
                     "trades=%d, signals_logged=%d",
                     strategy.name, i, len(test_bars), broker.get_cash(),
                     len(broker.get_positions()), len(broker.portfolio.trades),
                     len(broker.signal_log))

    broker.save()
    log.info("[%s] Walk-forward done.", strategy.name)
    return broker


def _is_rebalance_bar(prev_ts, curr_ts, freq: str) -> bool:
    """True when curr_ts crosses into a new rebalance period."""
    if prev_ts is None:
        return True
    p = pd.Timestamp(prev_ts)
    c = pd.Timestamp(curr_ts)
    if freq.upper() in ("1D", "D", "DAILY"):
        return c.date() != p.date()
    if freq.upper() in ("1W", "W", "WEEKLY"):
        return c.isocalendar().week != p.isocalendar().week
    if freq.upper() in ("1M", "M", "MONTHLY"):
        return (c.year, c.month) != (p.year, p.month)
    if freq.upper() in ("1Q", "Q", "QUARTERLY"):
        return (c.year, c.quarter) != (p.year, p.quarter)
    return False


def _run_portfolio_walkforward(strategy, broker: PaperBroker,
                                paper_start: str, paper_end: str) -> PaperBroker:
    """Walk-forward for portfolio strategies (e.g. cross-sectional momentum).

    Pattern per rebalance bar:
      1. Ask strategy for target {symbol: weight}.
      2. Compute target value per symbol = total_equity * weight.
      3. SELL anything held that's no longer in the target, or trim overweights.
      4. BUY new positions / top up underweights with remaining cash.
    Between rebalance bars, just mark equity and accrue cash interest.

    NOTE: The regime filter is applied at the portfolio level by short-
    circuiting the target to {} (full cash) when regime is off.
    """
    data, symbols = _fetch_window(strategy, paper_start, paper_end)
    if not data:
        log.warning("[%s] No data -- skipping.", strategy.name)
        return broker
    for sym, df in data.items():
        df.attrs['symbol'] = sym

    timeline = _build_master_timeline(data)
    paper_start_ts = pd.Timestamp(paper_start)
    if timeline and timeline[0].tzinfo is not None:
        paper_start_ts = paper_start_ts.tz_localize(timeline[0].tzinfo)
    test_bars = [t for t in timeline if t >= paper_start_ts]

    freq = getattr(strategy, "rebalance_freq", "1M")
    log.info("[%s] Portfolio walk-forward: %d bars, rebalance=%s, %d symbols",
             strategy.name, len(test_bars), freq, len(data))

    close_cache: dict[str, float] = {}      # for end-of-bar mark-to-market
    prev_ts = None

    bar_days = 1.0 if strategy.interval == "1d" else 1.0 / 6.25

    for i, ts in enumerate(test_bars):
        broker.accrue_interest(days=bar_days)

        # NO-LOOK-AHEAD: decisions for THIS bar were made at the CLOSE of the
        # *previous* bar (prev_ts) using data through prev_ts. We fill at
        # THIS bar's OPEN. Then we update close_cache with THIS bar's close
        # at the end of the loop (only used for mark-to-market and the next
        # rebalance's decision).

        # Rebalance: decide using prev_ts data, fill at ts open.
        if _is_rebalance_bar(prev_ts, ts, freq) and prev_ts is not None:
            # Strategy's view: data up through prev_ts (close).
            sliced = {sym: df.loc[df.index <= prev_ts] for sym, df in data.items()}
            sliced = {s: d for s, d in sliced.items() if not d.empty}
            try:
                target = strategy.generate_target_portfolio(sliced, prev_ts)
            except Exception:
                log.exception("[%s] generate_target_portfolio failed @ %s",
                              strategy.name, prev_ts)
                target = {}

            # Regime is also queried at prev_ts (the decision moment).
            if _entries_blocked_by_regime(strategy, prev_ts):
                target = {}

            # Fill prices = this bar's OPEN where available, else last close.
            fill_prices = dict(close_cache)
            for sym, df in data.items():
                if ts in df.index:
                    op = float(df.loc[ts, 'Open'])
                    if not pd.isna(op):
                        fill_prices[sym] = op

            _rebalance_portfolio(strategy, broker, target, fill_prices, ts)

        # NOW update close_cache with this bar's close for marking + next decision.
        for sym, df in data.items():
            if ts in df.index:
                close_cache[sym] = float(df.loc[ts, 'Close'])

        # Mark equity using close prices.
        if close_cache:
            broker.mark_equity(ts, close_cache)

        if i and i % 100 == 0:
            broker.save()
            log.info("[%s] %d/%d bars, cash=%.0f, positions=%d, trades=%d",
                     strategy.name, i, len(test_bars), broker.get_cash(),
                     len(broker.get_positions()), len(broker.portfolio.trades))
        prev_ts = ts

    broker.save()
    log.info("[%s] Portfolio walk-forward done.", strategy.name)
    return broker


def _rebalance_portfolio(strategy, broker: PaperBroker,
                          target: dict[str, float],
                          prices: dict[str, float],
                          ts) -> None:
    """Bring the broker's portfolio to match `target` (weights of total equity).

    Sells before buys (to free up cash). Skips trades smaller than 0.5% of
    portfolio value to keep turnover under control.
    """
    total_equity = broker.portfolio.portfolio_value(prices)
    min_trade_value = total_equity * 0.005  # 0.5% min trade size

    held = dict(broker.portfolio.positions)
    # Normalize target weights so they sum to at most 1.0 (rest in cash).
    total_weight = sum(max(w, 0) for w in target.values())
    if total_weight > 1.0:
        scale = 1.0 / total_weight
        target = {s: w * scale for s, w in target.items()}

    # 1. SELL anything not in target, or trim overweights.
    for sym, qty in list(held.items()):
        if qty <= 0:
            continue
        price = prices.get(sym)
        if price is None or price <= 0:
            continue
        target_value = total_equity * target.get(sym, 0)
        current_value = qty * price
        delta_value = current_value - target_value
        if delta_value >= min_trade_value:
            sell_qty = int(delta_value // price)
            if sell_qty > 0 and sell_qty <= qty:
                broker.portfolio.sell(sym, price, ts, qty=sell_qty)
                broker.log_signal(ts, sym, -1, price, "REBAL_SELL")
            elif sell_qty >= qty:
                broker.portfolio.sell(sym, price, ts)
                broker.log_signal(ts, sym, -1, price, "REBAL_EXIT")

    # 2. BUY missing positions / top up underweights.
    for sym, weight in target.items():
        if weight <= 0:
            continue
        price = prices.get(sym)
        if price is None or price <= 0:
            continue
        target_value = total_equity * weight
        current_qty = broker.portfolio.positions.get(sym, 0)
        current_value = current_qty * price
        delta_value = target_value - current_value
        if delta_value < min_trade_value:
            continue
        cash_left = broker.get_cash()
        # Don't spend more than 99% of available cash on a single buy.
        spend = min(delta_value, cash_left * 0.99)
        if spend <= 0:
            continue
        qty = int(spend // price)
        if qty > 0:
            broker.portfolio.buy(sym, price, qty, ts)
            broker.log_signal(ts, sym, +1, price, "REBAL_BUY")


def _step_signal_based(strategy, broker: PaperBroker, sym: str,
                        sig_df: pd.DataFrame, ts: pd.Timestamp,
                        capital_per_symbol: float) -> None:
    """One bar for legacy long-only / transition-signal strategies.

    Mirrors engine._run_transition_based: a signal at bar T executes at
    bar T+1's Open. We approximate that here by acting on the previous
    bar's signal at THIS bar's Open.
    """
    # Find the previous bar (the one whose signal we execute now).
    loc = sig_df.index.get_loc(ts)
    if loc == 0:
        return
    prev_row = sig_df.iloc[loc - 1]
    this_row = sig_df.loc[ts]

    signal = int(prev_row.get('signal', 0))
    if signal == 0:
        return

    fill_price = float(this_row.get('Open', this_row['Close']))
    if pd.isna(fill_price):
        fill_price = float(this_row['Close'])

    # Regime gate: query at the SIGNAL bar (prev_row's timestamp), not at ts.
    # The regime as-of ts uses ts's close in its 200-SMA, but we don't have
    # that information when the signal fires at the previous bar's close.
    signal_ts = sig_df.index[loc - 1]
    if signal == 1 and _entries_blocked_by_regime(strategy, signal_ts):
        broker.log_signal(ts, sym, signal, fill_price, "blocked_regime")
        return

    positions = broker.get_positions()

    # Sized-entry strategies (e.g. ema_rsi_adx_sized) emit a size_factor in
    # [0,1] alongside the signal. Mirror the backtester's behaviour: scale
    # the position size by that factor on entry. For exits use full size.
    size_factor = 1.0
    if 'size_factor' in sig_df.columns and signal == 1:
        sf = prev_row.get('size_factor')
        try:
            size_factor = float(sf) if sf is not None else 1.0
        except (TypeError, ValueError):
            size_factor = 1.0
        size_factor = max(0.0, min(1.0, size_factor))

    effective_pos_size = strategy.position_size * size_factor
    if signal == 1 and effective_pos_size <= 0:
        # Sized to zero -- still log so we can see the would-be entry.
        broker.log_signal(ts, sym, signal, fill_price, "noop_zero_size")
        return

    order = decide_order(
        signal=signal,
        symbol=sym,
        price=fill_price,
        positions=positions,
        cash=broker.get_cash(),
        position_size=effective_pos_size,
        capital_per_symbol=capital_per_symbol,
    )

    action = "noop"
    if order:
        broker.place_order(sym, order["side"], order["qty"], fill_price, ts)
        action = order["side"]
    broker.log_signal(ts, sym, signal, fill_price, action)


def _step_state_based(strategy, broker: PaperBroker, sym: str,
                       sig_df: pd.DataFrame, ts: pd.Timestamp,
                       capital_per_symbol: float) -> None:
    """One bar for long/flat/short state-based strategies (e.g. ema_rsi_ls).

    Mirrors engine._run_state_based: target generated at T is reached at
    T+1's Open. Position transitions:
      -1 -> short, +1 -> long, 0 -> flat. The Portfolio supports short/cover.
    """
    loc = sig_df.index.get_loc(ts)
    if loc == 0:
        return
    prev_row = sig_df.iloc[loc - 1]
    this_row = sig_df.loc[ts]

    target = int(prev_row.get('position', 0))
    fill_price = float(this_row.get('Open', this_row['Close']))
    if pd.isna(fill_price):
        fill_price = float(this_row['Close'])

    held = broker.portfolio.positions.get(sym, 0)
    current = 1 if held > 0 else (-1 if held < 0 else 0)
    if target == current:
        return

    # Regime gate: query at the SIGNAL bar (prev_row), not the exec bar.
    signal_ts = sig_df.index[loc - 1]
    if target == 1 and current == 0 and _entries_blocked_by_regime(strategy, signal_ts):
        broker.log_signal(ts, sym, target, fill_price, "blocked_regime")
        return

    action = "noop"
    # Close whatever is open.
    if current == 1:
        broker.portfolio.sell(sym, fill_price, ts)
        action = "SELL"
    elif current == -1:
        broker.portfolio.cover(sym, fill_price, ts)
        action = "COVER"
    # Open the new side.
    if target == 1:
        cap = min(capital_per_symbol, broker.get_cash())
        qty = int((cap * strategy.position_size) // fill_price)
        if qty > 0:
            broker.portfolio.buy(sym, fill_price, qty, ts)
            action = "BUY"
    elif target == -1:
        cap = min(capital_per_symbol, broker.get_cash())
        qty = int((cap * strategy.position_size) // fill_price)
        if qty > 0:
            broker.portfolio.short(sym, fill_price, qty, ts)
            action = "SHORT"

    broker.log_signal(ts, sym, target, fill_price, action)


def _step_with_stops(strategy, broker: PaperBroker, sym: str,
                     sig_df: pd.DataFrame, ts: pd.Timestamp,
                     capital_per_symbol: float) -> None:
    """One bar for ATR-stop strategies (bb_pullback, bb_pullback_daily).

    Mirrors engine._run_with_stops but operates on the broker's persistent
    portfolio. SL/TP/trail/EOD checks against High/Low, then entry on
    previous-bar signal at this bar's Open (no look-ahead).
    """
    from datetime import time as dtime
    loc = sig_df.index.get_loc(ts)
    if loc == 0:
        return
    prev_row = sig_df.iloc[loc - 1]
    this_row = sig_df.loc[ts]

    open_p = float(this_row.get('Open', this_row['Close']))
    high_p = float(this_row.get('High', this_row['Close']))
    low_p  = float(this_row.get('Low',  this_row['Close']))
    close_p = float(this_row['Close'])
    if pd.isna(open_p):
        open_p = close_p

    # IST time-of-day for entry-window / EOD-flatten checks.
    bar_ts = pd.Timestamp(ts)
    if bar_ts.tz is not None:
        bar_ts = bar_ts.tz_convert("Asia/Kolkata")
    bar_time = bar_ts.time() if hasattr(bar_ts, 'time') else None

    sl_mult = float(strategy.atr_sl_mult)
    tp_mult = float(strategy.atr_tp_mult) if strategy.atr_tp_mult is not None else None
    trail_act = float(strategy.trail_activation_atr)
    trail_dist = float(strategy.trail_distance_atr)
    min_stop_pct = float(strategy.min_stop_pct)

    entry_window = getattr(strategy, "entry_window", None)
    if entry_window:
        h_s, m_s = entry_window[0].split(":")
        h_e, m_e = entry_window[1].split(":")
        w_start = dtime(int(h_s), int(m_s))
        w_end   = dtime(int(h_e), int(m_e))
    else:
        w_start = w_end = None
    eod_str = getattr(strategy, "eod_flatten_time", None)
    if eod_str:
        h, m = eod_str.split(":")
        eod_time = dtime(int(h), int(m))
    else:
        eod_time = None

    portfolio: Portfolio = broker.portfolio
    held = portfolio.positions.get(sym, 0)

    # -- 1. Exit checks for any open position --
    if held > 0:
        meta = portfolio.get_meta(sym)
        sl = meta.get('sl')
        tp = meta.get('tp')
        exit_price = None
        if sl is None:
            # Position carried over from a non-stop path: treat as plain long.
            sl = -1.0  # sentinel: never triggers

        # High-water close (for trailing stop).
        if close_p > meta.get('highest_close', meta.get('entry_price', close_p)):
            portfolio.update_meta(sym, highest_close=close_p)
            meta = portfolio.get_meta(sym)

        if not pd.isna(low_p) and sl is not None and low_p <= sl:
            exit_price = min(sl, open_p) if open_p < sl else sl
        elif tp is not None and not pd.isna(high_p) and high_p >= tp:
            exit_price = max(tp, open_p) if open_p > tp else tp
        else:
            entry_price = meta.get('entry_price', close_p)
            entry_atr   = meta.get('entry_atr', 0.0)
            if entry_atr and not meta.get('trail_active') and \
               (close_p - entry_price) >= trail_act * entry_atr:
                portfolio.update_meta(
                    sym, trail_active=True,
                    trail_stop=meta['highest_close'] - trail_dist * entry_atr)
                meta = portfolio.get_meta(sym)
            if entry_atr and meta.get('trail_active'):
                new_trail = meta['highest_close'] - trail_dist * entry_atr
                if new_trail > meta.get('trail_stop', new_trail):
                    portfolio.update_meta(sym, trail_stop=new_trail)
                    meta = portfolio.get_meta(sym)
                if close_p <= meta['trail_stop']:
                    exit_price = close_p
            if exit_price is None and eod_time and bar_time and bar_time >= eod_time:
                exit_price = close_p

        if exit_price is not None:
            portfolio.sell(sym, exit_price, ts)
            portfolio.clear_meta(sym)
            broker.log_signal(ts, sym, -1, exit_price, "EXIT")
            held = 0

    # -- 2. Entry: previous-bar signal acted on this bar's Open. --
    if held == 0 and int(prev_row.get('signal', 0)) == 1:
        # Regime gate on new entries only -- exits above are unaffected.
        # Query at the SIGNAL bar (prev_row), not the execution bar.
        signal_ts = sig_df.index[loc - 1]
        if _entries_blocked_by_regime(strategy, signal_ts):
            broker.log_signal(ts, sym, 1, open_p, "blocked_regime")
            return
        in_window = True
        if w_start and w_end and bar_time:
            in_window = w_start <= bar_time <= w_end
        entry_atr = prev_row.get('atr')
        try:
            entry_atr_f = float(entry_atr) if entry_atr is not None else 0.0
        except (TypeError, ValueError):
            entry_atr_f = 0.0

        if in_window and entry_atr_f > 0 and not pd.isna(open_p):
            cap = min(capital_per_symbol, broker.get_cash())
            qty = int((cap * strategy.position_size) // open_p)
            if qty > 0:
                portfolio.buy(sym, open_p, qty, ts)
                sl_dist = max(sl_mult * entry_atr_f, open_p * min_stop_pct)
                sl_val = open_p - sl_dist
                tp_val = open_p + tp_mult * entry_atr_f if tp_mult is not None else None
                portfolio.set_meta(
                    sym,
                    entry_price=open_p, entry_ts=str(ts), entry_atr=entry_atr_f,
                    sl=sl_val, tp=tp_val,
                    highest_close=open_p,
                    trail_active=False, trail_stop=None,
                )
                broker.log_signal(ts, sym, 1, open_p, "BUY")


# ----------------------------------------------------------------------------
# composites
# ----------------------------------------------------------------------------

def run_walkforward_composite(strategy, *, reset: bool = False,
                              state_suffix: str = "_paper") -> dict:
    """Run a composite strategy by walk-forwarding each sub-strategy under
    its allocated capital and summing the resulting equity curves.

    state_suffix is propagated to sub-broker filenames AND to the combined
    state file (so rolling slices / regime variants don't clobber each other).
    """
    from .strategies import discover_strategies
    registry = {}
    for cls in discover_strategies():
        if getattr(cls, "is_composite", False):
            continue
        registry[cls().name] = cls

    sub_brokers: dict[str, PaperBroker] = {}
    sub_equity: dict[str, pd.Series] = {}

    for sub_name, weight in strategy.ALLOCATIONS.items():
        if sub_name not in registry:
            log.warning("[%s] sub-strategy %r not found -- skipping.",
                        strategy.name, sub_name)
            continue
        sub_strat = registry[sub_name]()
        sub_strat.initial_capital = strategy.initial_capital * weight
        log.info("[%s] sub %s weight=%.0f%% capital=%.0f",
                 strategy.name, sub_name, weight * 100, sub_strat.initial_capital)
        sub_suffix = f"{state_suffix}_under_{strategy.name}"
        broker = run_walkforward(sub_strat, reset=reset, state_suffix=sub_suffix)
        sub_brokers[sub_name] = broker

        eq = pd.Series(broker.equity_history, dtype=float)
        if not eq.empty:
            eq.index = pd.to_datetime(eq.index, utc=True, errors='coerce').tz_convert(None)
            eq = eq.sort_index()
            sub_equity[sub_name] = eq

    # Combine: align on union of timestamps, ffill, fillna with sub-initial.
    if sub_equity:
        frame = pd.DataFrame(sub_equity).sort_index().ffill()
        for name in frame.columns:
            sub_initial = strategy.initial_capital * strategy.ALLOCATIONS[name]
            frame[name] = frame[name].fillna(sub_initial)
        combined = frame.sum(axis=1)

        # Persist a "combined" pseudo-broker state for the report.
        out_path = settings.STATE_DIR / f"{strategy.name}{state_suffix}_combined.json"
        import json
        out = {
            "initial_capital": strategy.initial_capital,
            "allocations":     strategy.ALLOCATIONS,
            "equity_history":  {str(k): float(v) for k, v in combined.items()},
            "sub_strategies":  list(sub_equity.keys()),
            "n_trades": sum(len(b.portfolio.trades) for b in sub_brokers.values()),
            "n_signals": sum(len(b.signal_log) for b in sub_brokers.values()),
            # Aggregate turnover + fees across sub-strategies for the report.
            "total_traded_notional":   sum(b.portfolio.total_traded_notional
                                           for b in sub_brokers.values()),
            "total_transaction_costs": sum(b.portfolio.total_transaction_costs
                                           for b in sub_brokers.values()),
        }
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(out, indent=2, default=str))
        log.info("[%s] Combined paper equity persisted to %s",
                 strategy.name, out_path)

    return sub_brokers


# ----------------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------------

def _print_report(strategy_name: str, broker: PaperBroker):
    eq = pd.Series(broker.equity_history, dtype=float)
    if eq.empty:
        print(f"\n[{strategy_name}] No equity history recorded.")
        return
    eq.index = pd.to_datetime(eq.index, utc=True, errors='coerce').tz_convert(None)
    eq = eq.sort_index()
    metrics = compute_metrics(eq, broker.initial_capital)
    print(f"\n===== PAPER-TRADING RESULT [{strategy_name}] =====")
    print(f"  Window           : {eq.index[0].date()} -> {eq.index[-1].date()}")
    print(f"  Bars marked      : {len(eq)}")
    print(f"  Trades executed  : {len(broker.portfolio.trades)}")
    print(f"  Signal log rows  : {len(broker.signal_log)}")
    for k, v in metrics.items():
        print(f"  {k:<17}: {v}")
    # Show a sample of the most recent signals so the user can SEE signals firing.
    recent = broker.signal_log[-10:]
    if recent:
        print("  --- last 10 signals ---")
        for s in recent:
            print(f"   {s['ts']:<32} {s['symbol']:<14} sig={s['signal']:>+d} "
                  f"@ {s['price']:>10.2f}  -> {s['action']}")
    print("=" * 60)


def _slice_window(start: str, end: str, months: int) -> list[tuple[str, str]]:
    """Split [start, end] into contiguous slices of `months` calendar months.

    Returns list of (slice_start, slice_end) ISO date strings. The final
    slice may be shorter than `months` if the window doesn't divide evenly.
    """
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    slices: list[tuple[str, str]] = []
    while s < e:
        nxt = s + pd.DateOffset(months=months)
        slice_end = min(nxt, e)
        slices.append((s.strftime("%Y-%m-%d"), slice_end.strftime("%Y-%m-%d")))
        s = nxt
    return slices


def _run_one_slice(strategy_classes, name_filter: str | None,
                   reset: bool, use_regime: bool,
                   suffix: str) -> dict[str, dict]:
    """Run all (filtered) strategies once for the current paper window.

    Returns {strategy_name: {return_pct, sharpe, max_dd, trades, signals}}.
    """
    # Force fresh regime cache for this slice (so each slice gets one for
    # its own window).
    global _REGIME
    _REGIME = None
    if use_regime:
        try:
            _get_regime()
            log.info("Regime filter active: %s", _REGIME.summary())
        except Exception:
            log.exception("Failed to build regime filter; running without it.")
            _REGIME = None
    else:
        _REGIME = None

    results: dict[str, dict] = {}
    for cls in strategy_classes:
        strat = cls()
        if name_filter and strat.name != name_filter:
            continue
        try:
            if getattr(strat, "is_composite", False):
                sub_brokers = run_walkforward_composite(strat, reset=reset,
                                                       state_suffix=suffix)
                for n, b in sub_brokers.items():
                    _print_report(f"{strat.name} :: {n}", b)
                _print_composite_summary(strat.name, suffix=suffix)
                results[strat.name] = _load_composite_metrics(strat.name, suffix)
            else:
                broker = run_walkforward(strat, reset=reset, state_suffix=suffix)
                _print_report(strat.name, broker)
                results[strat.name] = _broker_metrics(broker)
        except Exception:
            log.exception("Walk-forward failed for [%s]", strat.name)
    return results


def _broker_metrics(broker: PaperBroker) -> dict:
    eq = pd.Series(broker.equity_history, dtype=float)
    if eq.empty:
        return {"return_pct": 0.0, "sharpe": 0.0, "max_dd": 0.0,
                "trades": 0, "signals": 0}
    eq.index = pd.to_datetime(eq.index, utc=True, errors='coerce').tz_convert(None)
    eq = eq.sort_index()
    m = compute_metrics_raw(eq, broker.initial_capital)
    return {
        "return_pct": m["total_return_pct"],
        "sharpe":     m["sharpe"],
        "max_dd":     m["max_drawdown_pct"],
        "trades":     len(broker.portfolio.trades),
        "signals":    len(broker.signal_log),
        "blocked_regime": sum(1 for s in broker.signal_log
                              if s.get('action') == 'blocked_regime'),
    }


def _load_composite_metrics(name: str, suffix: str) -> dict:
    import json
    path = settings.STATE_DIR / f"{name}{suffix}_combined.json"
    if not path.exists():
        return {}
    blob = json.loads(path.read_text())
    eq = pd.Series({pd.Timestamp(k): float(v)
                    for k, v in blob['equity_history'].items()}).sort_index()
    m = compute_metrics_raw(eq, blob['initial_capital'])
    return {
        "return_pct": m["total_return_pct"],
        "sharpe":     m["sharpe"],
        "max_dd":     m["max_drawdown_pct"],
        "trades":     blob.get("n_trades", 0),
        "signals":    blob.get("n_signals", 0),
    }


def _print_rolling_summary(slice_results: list[tuple[tuple[str, str], dict]],
                            use_regime: bool):
    """Aggregate per-slice metrics into a robustness summary table."""
    # Gather all strategy names across slices.
    names = sorted({n for _, res in slice_results for n in res.keys()})

    print("\n" + "=" * 90)
    print(f"  ROLLING WALK-FORWARD SUMMARY  ({len(slice_results)} slices, "
          f"regime_filter={'ON' if use_regime else 'OFF'})")
    print("=" * 90)

    header_slices = " ".join(f"S{i+1:>4}" for i in range(len(slice_results)))
    print(f"  Slice windows:")
    for i, ((s, e), _) in enumerate(slice_results, 1):
        print(f"    S{i}: {s} -> {e}")
    print()

    # Per-strategy stats across slices.
    print(f"  {'Strategy':<22} {'med Sharpe':>11} {'mean Ret%':>11} "
          f"{'% pos':>7} {'best Ret%':>11} {'worst Ret%':>11}")
    print(f"  {'-'*22} {'-'*11} {'-'*11} {'-'*7} {'-'*11} {'-'*11}")
    for n in names:
        rets = [res.get(n, {}).get('return_pct', 0.0) for _, res in slice_results]
        sharps = [res.get(n, {}).get('sharpe', 0.0) for _, res in slice_results]
        rets_arr = pd.Series(rets)
        sharps_arr = pd.Series(sharps)
        pct_pos = (rets_arr > 0).mean() * 100
        print(f"  {n:<22} {sharps_arr.median():>11.3f} {rets_arr.mean():>11.2f} "
              f"{pct_pos:>6.0f}% {rets_arr.max():>11.2f} {rets_arr.min():>11.2f}")

    # Per-slice returns matrix for visual inspection.
    print(f"\n  Per-slice returns (%):")
    print(f"  {'Strategy':<22} " + " ".join(f"{'S'+str(i+1):>9}"
                                            for i in range(len(slice_results))))
    for n in names:
        cells = " ".join(f"{res.get(n, {}).get('return_pct', 0):>9.2f}"
                          for _, res in slice_results)
        print(f"  {n:<22} {cells}")
    print("=" * 90)


def main():
    argv = sys.argv[1:]
    reset = "--reset" in argv
    argv = [a for a in argv if a != "--reset"]

    use_regime = "--regime" in argv
    argv = [a for a in argv if a != "--regime"]

    rolling_months = 0
    if "--rolling" in argv:
        i = argv.index("--rolling")
        try:
            rolling_months = int(argv[i + 1])
            del argv[i:i + 2]
        except (ValueError, IndexError):
            log.error("--rolling needs an integer N (months per slice).")
            return

    name_filter = argv[0] if argv else None

    paper_start = settings.PAPER_START_DATE
    paper_end = settings.PAPER_END_DATE or date.today().isoformat()
    log.info("Walk-forward window: %s -> %s | regime=%s | rolling=%s",
             paper_start, paper_end,
             "ON" if use_regime else "OFF",
             f"{rolling_months}mo" if rolling_months else "single")

    strategy_classes = discover_strategies()
    if not strategy_classes:
        log.error("No strategies discovered.")
        return

    if rolling_months <= 0:
        # Single-slice run (the original behaviour).
        suffix = "_paper" + ("_regime" if use_regime else "")
        _run_one_slice(strategy_classes, name_filter, reset, use_regime, suffix)
        return

    # Rolling slices.
    slices = _slice_window(paper_start, paper_end, rolling_months)
    log.info("Rolling: %d slices of %d months each.", len(slices), rolling_months)

    # We temporarily override settings.PAPER_START_DATE / END_DATE per slice.
    orig_start = settings.PAPER_START_DATE
    orig_end = settings.PAPER_END_DATE
    slice_results: list[tuple[tuple[str, str], dict]] = []
    try:
        for i, (s_start, s_end) in enumerate(slices, 1):
            log.info("=" * 70)
            log.info("Slice %d/%d : %s -> %s", i, len(slices), s_start, s_end)
            log.info("=" * 70)
            settings.PAPER_START_DATE = s_start
            settings.PAPER_END_DATE = s_end
            suffix = f"_paper_S{i}" + ("_regime" if use_regime else "")
            res = _run_one_slice(strategy_classes, name_filter, reset=True,
                                  use_regime=use_regime, suffix=suffix)
            slice_results.append(((s_start, s_end), res))
    finally:
        settings.PAPER_START_DATE = orig_start
        settings.PAPER_END_DATE = orig_end

    _print_rolling_summary(slice_results, use_regime)


def _print_composite_summary(name: str, suffix: str = "_paper"):
    """Print metrics from the saved combined equity series for a composite."""
    import json
    path = settings.STATE_DIR / f"{name}{suffix}_combined.json"
    if not path.exists():
        return
    blob = json.loads(path.read_text())
    eq = pd.Series({pd.Timestamp(k): float(v)
                    for k, v in blob['equity_history'].items()}).sort_index()
    metrics = compute_metrics(eq, blob['initial_capital'])
    print(f"\n===== PAPER-TRADING RESULT [{name}] (composite combined) =====")
    print(f"  Window         : {eq.index[0].date()} -> {eq.index[-1].date()}")
    print(f"  Sub-strategies : {blob['sub_strategies']}")
    print(f"  Total trades   : {blob['n_trades']}")
    print(f"  Total signals  : {blob['n_signals']}")
    for k, v in metrics.items():
        print(f"  {k:<15}: {v}")
    print("=" * 60)


if __name__ == "__main__":
    main()
