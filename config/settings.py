APP_NAME = "ATHENA"

VERSION = "0.2.0"

ENVIRONMENT = "Development"

EXCHANGE = "Bitget"

MARKET = "USDT Perpetual Futures"

DEBUG = True

TIMEZONE = "Asia/Kolkata"

MOTTO = "Protect. Grow. Evolve."

# Trading
TRADING_MODE = "SIMULATION"
PAPER_DB_PATH = "data/paper_trading.db"
RUNTIME_MODE = "ONE_SHOT"  # "ONE_SHOT" or "CONTINUOUS"
POSITION_MONITOR_INTERVAL = 1  # Seconds between active position price checks
MAX_RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0

# Scanner
SCAN_INTERVAL = 5
MAX_CONCURRENT_PAIRS = 50
SHORTLIST_ANALYSIS_LIMIT = 5

# Features
ENABLE_LOGGING = True
ENABLE_CACHE = True

# Dashboard
DASHBOARD_ENABLED = False