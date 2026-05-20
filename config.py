BINANCE_API_KEY = "PASTE_NEW_BINANCE_API_KEY_HERE"
BINANCE_API_SECRET = "PASTE_NEW_BINANCE_API_SECRET_HERE"

TELEGRAM_BOT_TOKEN = "PASTE_NEW_TELEGRAM_BOT_TOKEN_HERE"
TELEGRAM_CHAT_ID = "@GlobalSignalHub1"

SYMBOLS = ["RUNEUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT"]

FUTURE_TYPE = "USDT"
INTERVAL = "1h"
CANDLE_LIMIT = 250

RISK_PERCENT = 3.0

# Use only one leverage per signal to avoid duplicate signals
LEVERAGE_LEVELS = [10]

TARGET_MULTIPLIERS = {
    "scalping": [0.75, 1.5, 2.5],
    "day": [3.5, 5.0, 6.5],
    "swing": [8.5, 10.5],
}

# Maximum Telegram signals per day
MAX_SIGNALS_PER_DAY = 2

# Only post strong setups
MIN_CONFIDENCE_SCORE = 80

MORNING_SIGNAL_WINDOW = ("06:00", "10:00")
EVENING_SIGNAL_WINDOW = ("16:00", "20:00")

STATE_FILE = "signal_state.json"
MANUAL_OVERRIDE_FLAG = "--manual"
MANUAL_CHECK_FLAG = "--check"

# Percent distance from TP/SL to trigger a proximity alert (in percent)
PROXIMITY_PERCENT = 1.0

OPEN_TRADES_FILE = "open_trades.json"
CLOSED_TRADES_FILE = "closed_trades.json"

# Bot scan settings
BOT_TIMEZONE = "Africa/Nairobi"
SCAN_INTERVAL_SECONDS = 900  # 15 minutes

# Binance futures endpoint
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"

MAX_DAILY_SIGNALS = 2
MIN_CONFIDENCE_SCORE = 80   # only post trades scoring 80/100 or higher

RESULT_MONITOR_ENABLED = True
RESULT_CHECK_INTERVAL_SECONDS = 60
POST_EACH_TP_HIT = True
CLOSE_TRADE_ON_FINAL_TP = True 