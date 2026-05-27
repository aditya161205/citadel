import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# -- Data source --
DEFAULT_DATA_SOURCE = "yahoo"

# -- Backtest defaults --
INITIAL_CAPITAL = 10_000_000.0   # ₹1 crore
DEFAULT_SYMBOL = "RELIANCE.NS"
START_DATE = "2018-01-01"
END_DATE = "2024-01-01"
POSITION_SIZE = 0.95
SHORT_WINDOW = 50
LONG_WINDOW = 200

# -- Walk-forward / paper-trading split --
# Training (backtest baseline) window: START_DATE -> TRAIN_END_DATE
# Paper-trading (walk-forward, unseen data) window: PAPER_START_DATE -> PAPER_END_DATE
# PAPER_END_DATE = None means "today" (the simulator fills it in at run time).
TRAIN_END_DATE   = "2024-01-01"
PAPER_START_DATE = "2024-01-01"
PAPER_END_DATE: str | None = None    # None => use today's date at run time

# -- Regime filter (applied by paper_walkforward when --regime flag is on) --
# When the regime is "off" trend-style strategies stop taking new long
# entries (their existing positions exit normally). Mean-reverters opt
# out via the respect_regime_filter class flag on the strategy.
REGIME_SMA_WINDOW    = 200          # Nifty 50 200-day SMA gate
REGIME_VOL_WINDOW    = 20           # rolling realized vol window
REGIME_VOL_THRESHOLD = 0.18         # annualized vol ceiling (18%)

# -- Risk-free rate on idle cash --
# In real Indian paper trading, cash sweeps into liquid funds yielding ~6%/yr.
# Treating idle cash as 0% under-states return — this matches reality.
# 0.0 disables the accrual entirely.
CASH_INTEREST_RATE_ANNUAL = 0.06    # 6% per year, compounded daily

# -- Transaction costs --
# Per-side cost applied to every BUY/SELL/SHORT/COVER, charged out of cash.
# 10 bps per side = 20 bps round-trip, which is realistic for Indian retail
# delivery trading once brokerage + STT + stamp duty + GST + a small slippage
# allowance are summed. Set to 0.0 to disable. Sharpe is now also computed
# against this RFR so that "free" 6% on cash doesn't inflate the ratio.
TRANSACTION_COST_RATE = 0.001       # 10 bps per fill (i.e. 20 bps round-trip)

# -- Market schedule (NSE) --
TIMEZONE      = "Asia/Kolkata"
MARKET_OPEN   = "09:15"
MARKET_CLOSE  = "15:30"
EOD_RUN_TIME  = "15:40"          # when the daily paper-trade cycle fires
NSE_HOLIDAYS  = []               # add "YYYY-MM-DD" strings as needed

# -- Directories (repo root relative) --
RESULTS_DIR = _PROJECT_ROOT / "results"
STATE_DIR   = _PROJECT_ROOT / "state"
LOGS_DIR    = _PROJECT_ROOT / "logs"

# -- Broker credentials (live phase, deferred) --
ALPACA_API_KEY    = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_BASE_URL   = os.getenv("ALPACA_BASE_URL", "")
ZERODHA_API_KEY   = os.getenv("ZERODHA_API_KEY", "")
ZERODHA_API_SECRET = os.getenv("ZERODHA_API_SECRET", "")
