# Add your Binance API credentials here.
# Do NOT commit your real API keys to source control.

BINANCE_API_KEY = "Yt2f9rTGdw5irKjtKGwbgIjZqHskYNmrucnRolH0CUnbJAxGlx6PEQCAOLcWfDZg"
BINANCE_API_SECRET = "g06Acs5MturB1cnGEmVUef74T19rT586VEBoqpRWrRVHcRONi18ZIZ9EKjm3DE65"

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
LEVERAGE = 50

# Trading style levels for target generation
TARGET_MULTIPLIERS = {
    "scalping": [0.75, 1.5, 2.5],
    "day": [3.5, 5.0, 6.5],
    "swing": [8.5, 10.5],
}
