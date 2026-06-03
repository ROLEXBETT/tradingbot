# ============================================
# API KEYS
# IMPORTANT:
# You exposed your Binance keys and Telegram bot token in chat.
# Rotate/revoke them, then paste the new ones here.
# ============================================

BINANCE_API_KEY = "2LCBmcV1m6WoQw8qKrFUSxswMWCXKBW0R36FZbGtxLLWdCx3fqM6G3IARXlptVBq"
BINANCE_API_SECRET = "OsNhCK2p4M46CjbWEzyIe0LoI8oRsETluMOrd271KtSoxZ6N3uBOoEYbVBu4obVg"

TELEGRAM_BOT_TOKEN = "8719720883:AAGl5LeaHToZDlNr-c-RK6MtTvu8aSSz3QA"
TELEGRAM_CHAT_ID = "@GlobalSignalHub1"


# ============================================
# TRADING PAIRS
# ============================================

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "RUNEUSDT",
]


# ============================================
# MARKET / TIMEFRAME SETTINGS
# ============================================

FUTURE_TYPE = "USDT"

# 1h is better than 15m/5m for fewer, stronger signals
INTERVAL = "1h"

# Needs enough candles for EMA200 and indicators
CANDLE_LIMIT = 250

# Binance futures endpoint
BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"


# ============================================
# RISK SETTINGS
# ============================================

RISK_PERCENT = 3.0

# Use only one leverage per signal to avoid duplicate signals
LEVERAGE_LEVELS = [10]

TARGET_MULTIPLIERS = {
    "scalping": [0.75, 1.5, 2.5],
    "day": [3.5, 5.0, 6.5],
    "swing": [8.5, 10.5],
}


# ============================================
# STRICT SIGNAL LIMITS
# These settings reduce the number of Telegram signals.
# ============================================

# Maximum Telegram entry signals per day.
# Use 1 or 2 for very few strong signals.
MAX_SIGNALS_PER_DAY = 2

# Maximum signals per symbol per day.
# Keeps the same coin from spamming.
MAX_SIGNALS_PER_SYMBOL = 1

# Maximum signals allowed from one market scan.
# Even if 5 coins qualify, only the best one will be posted.
MAX_SIGNALS_PER_SCAN = 1

# Cooldown between new entry signals.
# 14400 = 4 hours
# 21600 = 6 hours
SIGNAL_COOLDOWN_SECONDS = 14400

# Only post very strong setups.
# 80 = moderate
# 85 = strong
# 88-90 = very strict
MIN_CONFIDENCE_SCORE = 88

# LONG score must beat SHORT score by this gap, or SHORT must beat LONG by this gap.
# Prevents unclear direction signals.
MIN_SCORE_GAP = 20

# Require stronger higher-timeframe confirmation.
REQUIRE_STRONG_HTF_ALIGNMENT = True

# Minimum volume strength.
# 1.5 means current volume must be at least 1.5x the 20-candle average.
MIN_VOLUME_MULTIPLIER = 1.5

# Volatility filter.
# Too low = dead market.
# Too high = dangerous/choppy market.
MIN_ATR_PERCENT = 0.45
MAX_ATR_PERCENT = 3.5


# ============================================
# SIGNAL WINDOWS
# Bot will only post during these times unless --manual is used.
# Timezone is set below.
# ============================================

MORNING_SIGNAL_WINDOW = ("06:00", "10:00")
EVENING_SIGNAL_WINDOW = ("16:00", "20:00")


# ============================================
# FILES
# ============================================

STATE_FILE = "signal_state.json"
OPEN_TRADES_FILE = "open_trades.json"
CLOSED_TRADES_FILE = "closed_trades.json"


# ============================================
# COMMAND FLAGS
# ============================================

MANUAL_OVERRIDE_FLAG = "--manual"
MANUAL_CHECK_FLAG = "--check"


# ============================================
# TELEGRAM RESULT / MONITOR SETTINGS
# These reduce TP/SL message spam.
# ============================================

RESULT_MONITOR_ENABLED = True

# Check open trades every 60 seconds.
RESULT_CHECK_INTERVAL_SECONDS = 60

# Turn off near-TP and near-SL alerts.
# Set to 0 to disable proximity alerts.
PROXIMITY_PERCENT = 0

# False = do not send a Telegram message for every TP hit.
# The bot will still track the trade internally.
POST_EACH_TP_HIT = False

# True = send final completion message when all targets are hit.
CLOSE_TRADE_ON_FINAL_TP = True


# ============================================
# BOT LOOP SETTINGS
# ============================================

BOT_TIMEZONE = "Africa/Nairobi"

# Scan market every 15 minutes.
SCAN_INTERVAL_SECONDS = 900


# ============================================
# BACKWARD COMPATIBILITY
# ============================================

# MAX_DAILY_SIGNALS is deprecated; use MAX_SIGNALS_PER_DAY instead.
MAX_DAILY_SIGNALS = MAX_SIGNALS_PER_DAY