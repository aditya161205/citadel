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
