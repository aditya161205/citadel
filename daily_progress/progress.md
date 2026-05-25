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

