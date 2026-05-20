import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

# Backtest defaults
# Capital is split equally across the universe, so it must be large enough that
# each per-stock slice can buy shares of higher-priced Nifty names.
INITIAL_CAPITAL = 10_000_000.0   # ₹1 crore total
DEFAULT_SYMBOL = "RELIANCE.NS"
START_DATE = "2018-01-01"
END_DATE = "2024-01-01"
POSITION_SIZE = 0.95

# Where per-stock result CSVs are written (repo root / results)
RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"

# SMA crossover parameters
SHORT_WINDOW = 50
LONG_WINDOW = 200

# Broker credentials (used later, in the live phase)
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "")
ZERODHA_API_KEY = os.getenv("ZERODHA_API_KEY", "")
ZERODHA_API_SECRET = os.getenv("ZERODHA_API_SECRET", "")
