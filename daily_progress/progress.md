# 19/5
"The technical analyst believes that anything that can possibly affect the price — fundamentally, politically, psychologically — is reflected in the price."
— John Murphy

Universe: Nifty 100
Pull the Nifty 100 constituent list from NSE's website. Before running any strategy, apply a basic liquidity filter — remove stocks with average daily traded value below a minimum threshold. This keeps execution clean and slippage manageable. Use Yahoo Finance's .NS suffix for data

Initial Plan:
SMA base → SMA crossover → EMA test → Volume → RSI → MACD → Breakout → ATR risk → Ranking → Portfolio → Walk-forward

Created the project structure and added basic back testing engine and metrics.

# 20/5

Filled in the rest of the backtesting foundation (most modules were empty stubs before today)
and got a full Nifty 100 backtest running end-to-end.

What was added:
- `portfolio.py` — Portfolio: cash, buy/sell (sell closes the full position), portfolio_value, trade log.
- `strategy_base.py` — StrategyBase ABC: the `generate_signals` contract every strategy implements.
- `data_loader.py` — `load_nifty100_symbols()` pulls the live constituent list from the NSE archives
  CSV (needs a User-Agent header or NSE hangs the request); `load_universe()` batch-downloads OHLCV
  for all symbols from Yahoo Finance (.NS suffix).
- `sma_crossover.py` — first strategy: 50/200 SMA crossover, signals only on the crossover bar.
- `metrics.py` — split into numeric (`compute_metrics_raw`) + formatted (`compute_metrics`).
- `main_backtest.py` — universe runner: equal-weight capital split across all stocks, a backtest per
  stock, plus a combined portfolio equity curve (sum of per-stock curves). No plotting.

How to run: from the repo root, `py -m trading_system.main_backtest`.

Outputs (two result sets, as required):
- Per-stock table → `results/sma_crossover_per_stock.csv` (gitignored; regenerated each run).
- Overall portfolio summary → printed to console.

Results (50/200 SMA, 2018-01-01 to 2024-01-01, 93 of ~100 stocks had full history):
  Overall portfolio: Total Return ~229%, Sharpe ~1.65, Max Drawdown ~-22%.
  Top per-stock: ADANIGREEN ~+5750%, CGPOWER ~+2740%, ADANIPOWER ~+1346%.

WHY THE RESULTS LOOK TOO GOOD (important — these numbers are NOT realistic):
1. Survivorship / look-ahead bias (biggest cause): we use TODAY's Nifty 100 list and apply it back
   to 2018. A stock is in today's index because it already grew, so we are effectively cherry-picking
   the biggest winners of the last 6 years. Stocks that fell out of the index (the losers) are never
   even in our sample. A correct backtest needs the index membership as it was on each historical
   date — which NSE doesn't hand out freely.
2. No transaction costs — zero brokerage, STT, slippage. Real Indian trading costs eat into returns,
   especially with frequent crossovers.
3. Same-bar execution (mild look-ahead): the engine buys/sells at the same day's Close that generated
   the signal; realistically you'd trade the next bar's open.
4. Idle cash isn't redeployed: each stock's sleeve only trades that one stock; cash sits idle when out
   of the market.

So: the pipeline is mechanically correct, but treat the headline numbers as inflated. Planned fixes
(in order of impact): next-bar execution, transaction costs, then addressing survivorship bias (or
documenting it as a known limitation at this stage).

Why ~93/100: the NSE list currently has 104 rows (includes DUMMYVEDL demerger placeholders and recent
additions). 11 had no 2018-2024 history on Yahoo — recent IPOs/demergers (TATACAP, HYUNDAI, the Tata
Motors TMPV/TMCV split, ENRIN, UNITDSPR ticker mismatch) plus the 4 dummies — so they're skipped.

# 21/5

Redesigned the whole system for plug-and-play: write a strategy file, everything else connects
automatically — backtesting AND paper trading, no wiring needed. Also added a paper-trading pipeline.

Architecture changes:
- Strategies are now self-describing. `StrategyBase` carries metadata: `name`, `interval` ("1d",
  "5m", etc.), `warmup` (bars needed), `universe` ("nifty100", an explicit list, or "file:path.csv"),
  `initial_capital`, `position_size`, and `data_source` (override global default or None).
- Auto-discovery: `strategies/__init__.py` scans the package for all `StrategyBase` subclasses.
  Adding a strategy = creating one .py file in `strategies/`. Both the backtester and the paper engine
  pick it up without any other code change.
- Pluggable data layer: `marketdata/` package replaces the old `backtester/data_loader.py`.
  `DataSource` ABC with two implementations: `YahooDataSource` (fully working, daily + intraday) and
  `CSVDataSource` (documented stub for when we have local CSVs). Global default source in settings,
  per-strategy override supported. Universe resolution is separate from the data source — both are
  pluggable independently.

Paper trading pipeline (new):
- `BrokerBase` abstract interface — swap in Zerodha/Alpaca later without engine changes.
- `PaperBroker` — simulated fills, JSON-persisted state per strategy at `state/<name>.json` (cash,
  positions, trades, last-bar timestamp per symbol for idempotency).
- `order_manager.py` — translates signal + current position into BUY/SELL/no-op.
- `risk_manager.py` — equal-weight position sizing (mirrors backtest logic).
- `PaperEngine.run_once()` — one generic cycle: resolve universe, fetch recent data, generate signals,
  check for new bars (skip already-processed = idempotent), decide + place orders, persist state.
- Self-scheduling loop (`scheduler.py`): EOD strategies run daily at 15:40 IST; intraday strategies
  run every `interval` during market hours (09:15-15:30 IST). Weekdays only, holiday list in settings.
  One failure doesn't kill the loop. Ctrl+C stops cleanly.

Logging: `utils/logger.py` — console + rotating file in `logs/`.

How to run:
  `py -m trading_system.main_backtest`              — backtest all discovered strategies
  `py -m trading_system.main_backtest sma_crossover` — backtest just one by name
  `py -m trading_system.main_live --once`            — paper: single cycle (for testing)
  `py -m trading_system.main_live`                   — paper: scheduled loop

Backtest results unchanged from 20/5 (229%, same caveats). Paper cycle verified: state file created,
idempotency confirmed (re-run produces 0 duplicate orders).

Workflow going forward:
To add a new strategy (e.g. EMA crossover), create trading_system/strategies/ema_crossover.py,
subclass StrategyBase, set the metadata, implement generate_signals — done. Both backtest and paper
pick it up. No other files need to change.

# 22/5

First real test of the plug-and-play promise: added a second strategy and a report tool, both with
zero wiring beyond their own files.

What was added:
- `strategies/ema_rsi.py` — second strategy: trend + momentum. Go long only when BOTH agree:
  fast EMA (20) above slow EMA (50) = uptrend, AND RSI(14) >= 50 = momentum confirms. Exit when
  either fails. RSI uses Wilder's smoothing (the standard). The EMA pair sets the trend (faster to
  react than plain SMAs); RSI acts as a filter that weeds out weak EMA crossovers with no momentum
  behind them.
- `paper_report.py` — read-only report: loads state/<name>.json, fetches latest prices for held
  symbols, prints portfolio summary (initial/cash/holdings/P&L), open positions (qty, avg cost,
  price, unrealized P&L), and the last 20 trades. Runs anytime WITHOUT stopping the live loop, since
  it only reads the state file.
  How to run: `py -m trading_system.paper_report` (or `... paper_report sma_crossover` for one).

Why RSI 50 and not the textbook 30/70: those are two opposite uses of RSI. 30/70 is mean-reversion
(buy oversold, sell overbought = counter-trend). The 50 centerline is momentum confirmation (>50 =
bulls in control = pro-trend). Since this strategy is trend-following (EMA crossover), 50 reinforces
the trend; 30/70 would contradict it (enter as momentum collapses) and the two filters would cancel
out. A 30/70 mean-reversion RSI is a separate strategy for later.

Results (EMA 20/50 + RSI, 2018-01-01 to 2024-01-01, 93 of ~104 stocks with full history):
  Overall portfolio: Total Return ~168%, Sharpe ~2.13, Max Drawdown ~-8.6%.
Versus the SMA 50/200 baseline (~229%, Sharpe ~1.65, DD ~-22%): lower headline return but much better
risk-adjusted — higher Sharpe, drawdown a third of the SMA's. Expected: faster EMAs exit sooner and
the RSI filter avoids whipsaw entries. Same survivorship-bias caveat as 20/5 applies — treat the
absolute numbers as inflated; the relative comparison (trend-following vs filtered) is the useful bit.

Plug-and-play confirmed: both files were picked up by the backtester (and the paper engine on next
restart) with no other code changes — exactly the workflow we set up on 21/5.

# 25/5

Reached the MACD + ATR-risk steps of the plan in one strategy: a filtered MACD crossover with
volatility-based stops. One new file, no other wiring (plug-and-play held again).

What was added:
- `strategies/macd_trend.py` — third strategy. Standard MACD (12/26 EMAs, 9-EMA signal line, histogram)
  but a crossover alone doesn't trade. A long fires only when ALL agree: MACD crosses above its signal
  line, price is above the 200 EMA (long-term uptrend), the histogram is rising (momentum building),
  ADX(14) >= 20 (trend strong enough to ride), and volume is above its 20-bar average (real
  participation). Exit fires on the FIRST of: an ATR chandelier stop (sits `atr_mult`=3 ATRs below the
  best close since entry, ratchets up only — this is both the initial stop loss AND the trailing exit,
  so risk adapts to volatility instead of a fixed %), or a bearish MACD cross while price is below the
  200 EMA with the histogram falling.

Indicators: ADX and ATR both use Wilder's smoothing (same convention as the RSI in ema_rsi). Entry
filters are vectorised; the exit side is a single stateful pass over the bars because the chandelier
stop is path-dependent (it ratchets off the running peak).

One design note — the engine is long-only (Portfolio.sell only closes a long; there's no shorting).
So the bearish "sell / short" condition closes the long rather than opening a short, matching how
sma_crossover and ema_rsi already treat a -1. A true short book would mean changing Portfolio and the
engine — deferred. decide_order already no-ops a sell-when-flat, so the ATR-stop exits are safe in the
paper engine too.

How to run: `py -m trading_system.main_backtest macd_trend`.

Results (MACD trend + ATR, 2018-01-01 to 2024-01-01, 93 of ~104 stocks with full history):
  Overall portfolio: Total Return ~43%, Sharpe ~1.62, Max Drawdown ~-3.98%.
  1296 trades (~14/stock), 72 of 93 names profitable. Top: ADANIENT ~+971%, TATAPOWER ~+281%,
  CGPOWER ~+202%. Bottom: UNIONBANK ~-47%, CHOLAFIN ~-34%, BOSCHLTD ~-34%.

Three strategies side by side (same universe/window, same survivorship caveat):
  SMA 50/200          : ~229%  Sharpe ~1.65  DD ~-22%
  EMA 20/50 + RSI     : ~168%  Sharpe ~2.13  DD ~-8.6%
  MACD trend + ATR    : ~43%   Sharpe ~1.62  DD ~-3.98%
The MACD strategy trades the headline return for capital protection: lowest return of the three but a
drawdown a fifth of the SMA's. The five-filter entry is deliberately selective (it sits out a lot of
moves) and the ATR stop cuts losers fast — individual names still draw down 30-50%, but equal-weight
diversification plus the stops keep the portfolio drawdown under 4%. Sharpe is middle of the pack;
EMA+RSI is still the best risk-adjusted so far. The same survivorship/look-ahead, no-cost, same-bar
caveats from 20/5 apply — treat absolute numbers as inflated, the relative comparison is the signal.

Env note: on this macOS Python the data fetch failed with an SSL CERTIFICATE_VERIFY_FAILED until
SSL_CERT_FILE / REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE were pointed at certifi's bundle (or run the
"Install Certificates.command" that ships with the python.org installer once).

# 26/5

Big session — fixed a hidden execution bias in the backtest engine (which retroactively shifted
every existing strategy's numbers down), ran a thorough filter-tuning study on top of ema_rsi
(learned the hard way that more filters mostly hurts), then added a genuinely different category
of strategy — Bollinger-pullback mean-reversion — using Murphy's multi-timeframe doctrine. Closed
by building a single composite strategy file that runs a real 80/20 trend-+-mean-reversion blend
in one auto-discovered unit.

## Part 1 — Realism fix + filter experiments on ema_rsi

### Fix: no more same-bar execution
The 20/5 caveat list flagged this but we hadn't fixed it: signals were generated FROM today's
close and also executed AT today's close — a small but real form of look-ahead (you can't trade
at a close that hasn't happened yet). Fixed `backtester/engine.py`: signal at bar T is now acted
on at bar T+1's Open. Equity is still marked-to-market at each bar's Close (standard convention).

The fix is asymmetric — the faster a strategy trades, the more it was benefiting from the bias.
*Every previous backtest number on this project is now slightly different*:

| Strategy            | Pre-fix | Post-fix | Delta  | Why                              |
|--------------------|---------|----------|--------|----------------------------------|
| sma_crossover       | 229%    | 224%     | -5 pp  | ~1 trade/yr -> negligible bias   |
| macd_trend          | 43%     | 38.6%    | -4.4 pp| moderate trade rate              |
| ema_rsi             | 168%    | 144%     | -24 pp | ~18 trades/yr -> large bias      |

The Sharpe ranking didn't change (ema_rsi still tops it post-fix at 2.00), but absolute returns
deflated by exactly the amount of the lie. This is the diagnostic lesson — strategies that look
amazing in a naive backtest often deflate the most under realistic execution.

### Filter sweep on ema_rsi — what worked, what didn't
Tried four variants to either reduce false signals or extend the strategy. The baseline is hard
to beat on Sharpe.

| Strategy file              | Idea                                      | Return | Sharpe | Max DD  | Trades/yr |
|---------------------------|-------------------------------------------|--------|--------|---------|-----------|
| `ema_rsi.py` (baseline)    | trend (EMA) + momentum (RSI > 50)         | 144%   | 2.00 * | -7.1%   | 18        |
| `ema_rsi_adx.py`           | + ADX > 20 trend-strength filter          | 83%    | 1.72   | -7.1%   | 12        |
| `ema_rsi_adx_sized.py`     | ADX continuous *sizer* instead of filter  | 21%    | 1.61   | -2.4% * | 14        |
| `ema_rsi_vol.py`           | + 10-vol-MA > 30-vol-MA confirmation      | 56%    | 1.91   | -5.3%   | 16        |
| `ema_rsi_ls.py`            | extend to short side (fast<slow & RSI<50) | 90%    | 1.20   | -13%    | 32        |

### The over-filtering trap (the real lesson)
Every filter reduced trades. Every filter also reduced Sharpe. Why? Each "smart" filter
introduces more *out-of-market* time, and in a bull market every hour in cash is opportunity
cost. Filters reduce noise AND reduce signal — in this setup they removed more signal than
noise. The baseline ema_rsi is already well-balanced; stacking more conditions on top is mostly
cash drag in disguise.

A specific note on RSI 50 vs the textbook 30/70: those are *opposite* uses of RSI. 30/70 is
mean-reversion (counter-trend); 50 is the centerline (pro-trend momentum). Since our strategy
is trend-following, 50 reinforces the EMA crossover; 30/70 would contradict it. Mixing a trend
filter with a 30/70 RSI gate would have signals cancel each other out.

A specific note on the long/short experiment: shorts *underperformed* meaningfully. Three
reasons: (1) Indian equities trend up over 2018-2024, so shorts fight the drift; (2) survivorship
bias — we use today's Nifty 100 list, which by definition contains stocks that survived every
drawdown, so we're systematically shorting future winners; (3) trade count nearly doubles, so
any future transaction cost will hammer this version hardest. (Side note: adding short()/cover()
to Portfolio in this session means the 25/5 "engine is long-only" comment is now outdated — the
engine has a state-based path that supports both sides.)

ADX deserves a longer note: at threshold 25 (textbook), it killed returns brutally because ADX
is a *lagging* confirmation — by the time it crosses 25 we've already missed 30-50% of the
move. Loosening to 20 helped, but it never beat the baseline. As a *sizer* (continuous scaling
of position size by ADX), it produced the smallest drawdown of any strategy (-2.4%) but at much
smaller absolute return — chronically underweight when ADX is moderate. Useful if you wanted
to run with leverage; not as-is.

## Part 2 — Mean reversion (Murphy-style) + the 80/20 blend

After the filter sweep made clear that "more filters on the trend follower" was a dead end, the
right move was to add a *different category* of strategy: mean reversion. I have a validated
intraday mean-reversion bot in a separate Intraday-Trading-Bot repo (BB Bounce + RSI Reversal +
VWAP-EMA Confluence, 63% win rate, walk-forward 70.8% pass). We ported the single
highest-priority signal (BB Bounce, ~60% of bot's trades) into this framework and bound it to
the daily trend follower with Murphy's classic doctrine: *"take oscillator signals only in the
direction of the higher-timeframe trend."*

### Engine extensions for ATR-based exits
Mean-reversion needs proper stops to work — without tight TPs and wide SLs, the asymmetric
risk/reward that makes the strategy run is gone. Added a third engine path:
- `backtester/indicators.py` (NEW): shared helpers `rsi`, `ema`, `atr`, `bollinger` — DRYs up
  the indicator duplication across strategies.
- `backtester/strategy_base.py`: optional stop-config metadata fields (`atr_sl_mult`,
  `atr_tp_mult`, trail params, `min_stop_pct`, `entry_window`, `eod_flatten_time`, `start_date`,
  `end_date`). Defaults are all `None` -> existing strategies fall through unchanged.
- `backtester/portfolio.py`: `position_meta` dict + helpers (entry price, ATR, SL/TP, trail
  state); also added `short()` and `cover()` so the long/short variant could work.
- `backtester/engine.py`: new `_run_with_stops()` path, activated when a strategy sets
  `atr_sl_mult`. Per bar: check open positions' SL/TP/trail/EOD-time against bar High/Low first,
  then enter on signal==1 if inside the entry window. Bar High/Low is the standard backtest
  convention for stop-fill simulation.
- `main_backtest.py`: honour `strategy.start_date / end_date` overrides (intraday strategies
  need ~720d for yfinance's hourly cap).

All additions are opt-in via metadata — existing strategies (sma_crossover, ema_rsi, macd_trend)
keep their old code paths and old behaviour (except for the same-bar fix).

### The mean-reversion strategies
- `strategies/bb_pullback.py` (NEW, hourly, 1h interval): the proper Murphy-style mean-reverter.
  Lazy-fetches daily ema_rsi regime per stock and only takes hourly entries when the daily
  trend is up. BB lower-band touch + bounce + RSI<50 + EMA50 slope >= -2.5% + Volume > 0.6× avg
  + bullish candle. ATR-based exits (SL = entry - 3×ATR, TP = entry + 0.4×ATR). Time-stop at
  15:00 IST, entry window 10:15-14:00. Result over the 2024-06 -> 2026-05 window (the ~720d
  hourly cap): +1.63% return, Sharpe 1.39, drawdown -0.11%. Per-stock 25 trades/yr, 100/100
  stocks fired. Small standalone return because it's in cash ~90% of the time (tight TPs, fast
  in/out), but per-stock drawdowns are 1-3% — mean-reversion working as designed.
- `strategies/bb_pullback_daily.py` (NEW): daily-bar variant of the same idea, *no* higher-TF
  gate (the equivalent for a daily strategy would be a weekly filter — separate decision).
  Built so we could backtest on the same 2018-2024 window as the trend follower.
  Result: -0.11% return, Sharpe 0.005, drawdown -9.05%. *Doesn't really work* on daily bars —
  the strategy needs intraday noise to revert from, which a daily timeframe doesn't offer.

### Diversification analysis
Built `analyze_blend.py` to compute return correlation + blended-portfolio metrics across the
trend follower and the mean reverter at several weights.

On the proper training window (2018-2024, daily data):
- Daily-return correlation: +0.296   (mildly correlated; some diversification)
- Standalone: ema_rsi 144% / Sharpe 2.00 / DD -7.1% ; bb_pullback_daily -0.11% / Sharpe 0.005 / DD -9.1%

Blended portfolio sweep:
| Mix (trend/MR) | Return  | Sharpe | Max DD  |
|---------------|---------|--------|---------|
| 100/0         | 144.06% | 2.001 *| -7.06%  |
| 90/10         | 129.64% | 1.983  | -6.71%  |
| 80/20         | 115.22% | 1.959  | -6.33%  |
| 70/30         | 100.81% | 1.927  | -5.91% *|
| 50/50         |  71.97% | 1.823  | -6.63%  |
| 30/70         |  43.14% | 1.585  | -7.54%  |
| 0/100         |  -0.11% | 0.005  | -9.05%  |

Findings:
1. Pure ema_rsi still wins on Sharpe — every blend dilutes it because the daily MR is too
   close to zero return.
2. Drawdown bottoms out at 70/30 (-5.91% vs the pure trend's -7.06%) — about 15-20% less pain
   for a 30% MR allocation.
3. 80/20 is the most defensible practical compromise: ~80% of the return with ~10% less
   drawdown. Defensible if smoothness matters as much as return; pure ema_rsi defensible if
   only the Sharpe number matters.

The deeper finding: mean-reversion *genuinely needs a higher-frequency timeframe* to work. The
hourly version makes conceptual sense; the daily version doesn't. We can't backtest the hourly
version on 2018-2024 due to yfinance's 730-day hourly cap. Future remediation options: save
hourly data ourselves going forward, or switch to a paid feed.

### The blend strategy (single file)
`strategies/blend_trend_mr.py` (NEW): a true composite strategy. Declares `is_composite = True`.
`main_backtest.py` detects this and routes to `run_blend()` instead of the standard per-stock
loop. `run_blend()` finds `ema_rsi` and `bb_pullback_daily` via the existing auto-discovery
registry, runs each with its proportional capital (80 lakh + 20 lakh), and sums their equity
curves into one portfolio equity series.

Result: 115.15% / Sharpe 1.961 / DD -6.31% — matches the analytical blend within rounding
(physical-split vs normalised-blend math). Single file, auto-discovered, runnable as
`py -m trading_system.main_backtest blend_trend_mr`. To try a different mix, edit the
`ALLOCATIONS` dict at the top of the file.

## Final scoreboard

All strategies, training window 2018-2024 (or the hourly window noted), after the same-bar
execution fix:

| Strategy            | Window      | Return  | Sharpe | Max DD  | Notes                              |
|--------------------|-------------|---------|--------|---------|------------------------------------|
| sma_crossover       | 2018-2024   | 224%    | 1.64   | -22%    | slow baseline                      |
| ema_rsi             | 2018-2024   | 144%    | 2.00   | -7.1%   | the keeper                         |
| ema_rsi_adx         | 2018-2024   | 83%     | 1.72   | -7.1%   | over-filtered                      |
| ema_rsi_adx_sized   | 2018-2024   | 21%     | 1.61   | -2.4%   | tiny but smoothest                 |
| ema_rsi_vol         | 2018-2024   | 56%     | 1.91   | -5.3%   | over-filtered (different angle)    |
| ema_rsi_ls          | 2018-2024   | 90%     | 1.20   | -13%    | shorts hurt in bull market         |
| macd_trend          | 2018-2024   | 39%     | 1.48   | -4.2%   | post same-bar fix                  |
| bb_pullback_daily   | 2018-2024   | -0.1%   | 0.005  | -9.1%   | wrong timeframe for MR             |
| bb_pullback (hourly)| 2024-06+    | 1.6%    | 1.39   | -0.1%   | works as MR — small but smooth     |
| **blend_trend_mr**  | 2018-2024   | 115%    | 1.96   | -6.3%   | **80/20 trend+MR composite**       |

Known realism debt still outstanding (unchanged from 20/5 list, except same-bar execution is
now fixed): survivorship bias (today's Nifty 100 applied to 2018), no transaction costs, no
idle-cash redeployment between sub-strategies.

