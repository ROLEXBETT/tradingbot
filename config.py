# Add your Binance API credentials here.
# Do NOT commit your real API keys to source control.

BINANCE_API_KEY = "Yt2f9rTGdw5irKjtKGwbgIjZqHskYNmrucnRolH0CUnbJAxGlx6PEQCAOLcWfDZg"
BINANCE_API_SECRET = "g06Acs5MturB1cnGEmVUef74T19rT586VEBoqpRWrRVHcRONi18ZIZ9EKjm3DE65"

# Telegram bot configuration
TELEGRAM_BOT_TOKEN = "8719720883:AAGl5LeaHToZDlNr-c-RK6MtTvu8aSSz3QA"
TELEGRAM_CHAT_ID = "@GlobalSignalHub1"

# Symbols to evaluate
SYMBOLS = ["RUNEUSDT", "BTCUSDT", "ETHUSDT", "BNBUSDT"]

# Futures type: "USDT" for perpetual futures
FUTURE_TYPE = "USDT"

# Kline interval
INTERVAL = "1h"

# Number of candles to fetch for analysis
CANDLE_LIMIT = 200

# Risk settings
RISK_PERCENT = 3.0
LEVERAGE_LEVELS = [10, 25, 50]  # Multiple leverage levels to analyze

# Trading style levels for target generation
TARGET_MULTIPLIERS = {
    "scalping": [0.75, 1.5, 2.5],
    "day": [3.5, 5.0, 6.5],
    "swing": [8.5, 10.5],
}

# Limit total signals per day and define preferred signal windows.
MAX_SIGNALS_PER_DAY = 2
MORNING_SIGNAL_WINDOW = ("06:00", "10:00")
EVENING_SIGNAL_WINDOW = ("16:00", "20:00")

# Optional persistent state file for daily signal tracking.
STATE_FILE = "signal_state.json"

# Manual override when running the script yourself.
MANUAL_OVERRIDE_FLAG = "--manual"
