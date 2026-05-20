BINANCE_API_KEY = "2LCBmcV1m6WoQw8qKrFUSxswMWCXKBW0R36FZbGtxLLWdCx3fqM6G3IARXlptVBq"
BINANCE_API_SECRET = "OsNhCK2p4M46CjbWEzyIe0LoI8oRsETluMOrd271KtSoxZ6N3uBOoEYbVBu4obVg"

TELEGRAM_BOT_TOKEN = "8719720883:AAGl5LeaHToZDlNr-c-RK6MtTvu8aSSz3QA"
TELEGRAM_CHAT_ID = "@GlobalSignalHub1"

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "RUNEUSDT"]

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

# ============================================
# SIGNAL LIMITS - STRICT CONTROLS
# ============================================

# Maximum Telegram signals per day
MAX_SIGNALS_PER_DAY = 7

# Maximum signals per symbol per day (prevents same symbol from spamming)
MAX_SIGNALS_PER_SYMBOL = 1

# Cooldown between signals in seconds (3600 = 1 hour)
SIGNAL_COOLDOWN_SECONDS = 3600

# Only post strong setups
MIN_CONFIDENCE_SCORE = 80

# Signal windows (only post during these times)
MORNING_SIGNAL_WINDOW = ("06:00", "10:00")
EVENING_SIGNAL_WINDOW = ("16:00", "20:00")

# ============================================
# END OF SIGNAL LIMITS
# ============================================

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

# MAX_DAILY_SIGNALS is deprecated; use MAX_SIGNALS_PER_DAY instead.
MAX_DAILY_SIGNALS = MAX_SIGNALS_PER_DAY

RESULT_MONITOR_ENABLED = True
RESULT_CHECK_INTERVAL_SECONDS = 60
POST_EACH_TP_HIT = True
CLOSE_TRADE_ON_FINAL_TP = True

# Force single instance by checking for lock file
import os
import sys
LOCK_FILE = "bot.lock"

if os.path.exists(LOCK_FILE):
    print("Bot already running! Exiting.")
    sys.exit(1)
else:
    open(LOCK_FILE, "w").close()