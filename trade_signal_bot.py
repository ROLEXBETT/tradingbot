import sys
import json
import time
import requests
import pandas as pd
import matplotlib.pyplot as plt
from io import BytesIO
import numpy as np

from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# PREVENT MULTIPLE INSTANCES (Simple file method - works on all platforms)
import os

LOCK_FILE = "bot.lock"

if os.path.exists(LOCK_FILE):
    file_age = time.time() - os.path.getmtime(LOCK_FILE)
    if file_age < 3600:  # 1 hour
        print("❌ Another instance of the bot is already running! Exiting.")
        print("   If you're sure no other instance is running, delete bot.lock file")
        sys.exit(1)
    else:
        os.remove(LOCK_FILE)

with open(LOCK_FILE, 'w') as f:
    f.write(str(os.getpid()))

from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    SYMBOLS,
    INTERVAL,
    CANDLE_LIMIT,
    RISK_PERCENT,
    LEVERAGE_LEVELS,
    MAX_SIGNALS_PER_DAY,
    MIN_CONFIDENCE_SCORE,
    MORNING_SIGNAL_WINDOW,
    EVENING_SIGNAL_WINDOW,
    STATE_FILE,
    MANUAL_OVERRIDE_FLAG,
    MANUAL_CHECK_FLAG,
    PROXIMITY_PERCENT,
    OPEN_TRADES_FILE,
    CLOSED_TRADES_FILE,
    BOT_TIMEZONE,
    SCAN_INTERVAL_SECONDS,
    BINANCE_FUTURES_BASE_URL,
    RESULT_MONITOR_ENABLED,
    RESULT_CHECK_INTERVAL_SECONDS,
    POST_EACH_TP_HIT,
    CLOSE_TRADE_ON_FINAL_TP,
)

TZ = ZoneInfo(BOT_TIMEZONE)

# Additional strict limits
config_module = __import__('config')

MAX_SIGNALS_PER_SYMBOL = getattr(config_module, 'MAX_SIGNALS_PER_SYMBOL', 1)
SIGNAL_COOLDOWN_SECONDS = getattr(config_module, 'SIGNAL_COOLDOWN_SECONDS', 14400)

MIN_SCORE_GAP = getattr(config_module, 'MIN_SCORE_GAP', 20)
REQUIRE_STRONG_HTF_ALIGNMENT = getattr(config_module, 'REQUIRE_STRONG_HTF_ALIGNMENT', True)
MIN_VOLUME_MULTIPLIER = getattr(config_module, 'MIN_VOLUME_MULTIPLIER', 1.5)
MIN_ATR_PERCENT = getattr(config_module, 'MIN_ATR_PERCENT', 0.45)
MAX_ATR_PERCENT = getattr(config_module, 'MAX_ATR_PERCENT', 3.5)
MAX_SIGNALS_PER_SCAN = getattr(config_module, 'MAX_SIGNALS_PER_SCAN', 1)


# -----------------------------
# Time and state helpers
# -----------------------------

def now_local():
    return datetime.now(TZ)


def today_key():
    return now_local().strftime("%Y-%m-%d")


def current_time_hhmm():
    return now_local().strftime("%H:%M")


def read_json_file(file_path, default_value):
    path = Path(file_path)

    if not path.exists():
        return default_value

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_value


def write_json_file(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def load_signal_state():
    state = read_json_file(
        STATE_FILE,
        {
            "date": today_key(),
            "signals_sent_today": 0,
            "posted_signal_keys": [],
            "last_signal_time": None,
            "symbol_signal_count": {}
        }
    )

    if state.get("date") != today_key():
        state = {
            "date": today_key(),
            "signals_sent_today": 0,
            "posted_signal_keys": [],
            "last_signal_time": None,
            "symbol_signal_count": {}
        }
        save_signal_state(state)

    if "posted_signal_keys" not in state:
        state["posted_signal_keys"] = []
    if "signals_sent_today" not in state:
        state["signals_sent_today"] = 0
    if "symbol_signal_count" not in state:
        state["symbol_signal_count"] = {}
    if "last_signal_time" not in state:
        state["last_signal_time"] = None

    return state


def save_signal_state(state):
    write_json_file(STATE_FILE, state)


def remaining_daily_slots():
    state = load_signal_state()
    remaining = MAX_SIGNALS_PER_DAY - state["signals_sent_today"]
    return max(0, remaining)


def already_posted_signal(signal_key):
    state = load_signal_state()
    return signal_key in state.get("posted_signal_keys", [])


def can_post_for_symbol(symbol):
    state = load_signal_state()
    symbol_count = state.get("symbol_signal_count", {}).get(symbol, 0)
    return symbol_count < MAX_SIGNALS_PER_SYMBOL


def is_cooldown_active():
    state = load_signal_state()
    last_time = state.get("last_signal_time")
    if last_time is None:
        return False
    
    last_dt = datetime.fromisoformat(last_time)
    cooldown_until = last_dt + timedelta(seconds=SIGNAL_COOLDOWN_SECONDS)
    return now_local() < cooldown_until


def get_cooldown_remaining():
    state = load_signal_state()
    last_time = state.get("last_signal_time")
    if last_time is None:
        return 0
    
    last_dt = datetime.fromisoformat(last_time)
    cooldown_until = last_dt + timedelta(seconds=SIGNAL_COOLDOWN_SECONDS)
    remaining = (cooldown_until - now_local()).total_seconds()
    return max(0, int(remaining / 60))


def mark_signal_posted(signal_key, symbol):
    state = load_signal_state()
    
    if signal_key in state.get("posted_signal_keys", []):
        return

    state["signals_sent_today"] += 1
    
    if "symbol_signal_count" not in state:
        state["symbol_signal_count"] = {}
    state["symbol_signal_count"][symbol] = state["symbol_signal_count"].get(symbol, 0) + 1
    
    state["last_signal_time"] = now_local().isoformat()
    
    if "posted_signal_keys" not in state:
        state["posted_signal_keys"] = []
    state["posted_signal_keys"].append(signal_key)
    
    save_signal_state(state)
    
    print(f"Signal posted. Daily total: {state['signals_sent_today']}/{MAX_SIGNALS_PER_DAY}")
    print(f"Cooldown active for {SIGNAL_COOLDOWN_SECONDS // 60} minutes")


# -----------------------------
# Signal window logic
# -----------------------------

def time_in_window(current, start, end):
    return start <= current <= end


def is_signal_window_open():
    current = current_time_hhmm()

    morning_open = time_in_window(
        current,
        MORNING_SIGNAL_WINDOW[0],
        MORNING_SIGNAL_WINDOW[1]
    )

    evening_open = time_in_window(
        current,
        EVENING_SIGNAL_WINDOW[0],
        EVENING_SIGNAL_WINDOW[1]
    )

    return morning_open or evening_open


def is_manual_mode():
    return MANUAL_OVERRIDE_FLAG in sys.argv


def is_once_mode():
    return "--once" in sys.argv


# -----------------------------
# Binance data
# -----------------------------

def fetch_futures_klines(symbol):
    url = f"{BINANCE_FUTURES_BASE_URL}/fapi/v1/klines"

    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": CANDLE_LIMIT
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    raw_data = response.json()

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_volume",
        "taker_buy_quote_volume",
        "ignore"
    ]

    df = pd.DataFrame(raw_data, columns=columns)

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_asset_volume",
        "taker_buy_base_volume",
        "taker_buy_quote_volume"
    ]

    for col in numeric_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def fetch_higher_timeframe_klines(symbol, higher_interval):
    """Fetch klines from higher timeframe for confirmation"""
    url = f"{BINANCE_FUTURES_BASE_URL}/fapi/v1/klines"
    
    params = {
        "symbol": symbol,
        "interval": higher_interval,
        "limit": 100
    }
    
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    raw_data = response.json()
    
    columns = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "number_of_trades",
        "taker_buy_base_volume", "taker_buy_quote_volume", "ignore"
    ]
    
    df = pd.DataFrame(raw_data, columns=columns)
    
    numeric_columns = ["open", "high", "low", "close", "volume"]
    for col in numeric_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    return df


def fetch_current_price(symbol):
    url = f"{BINANCE_FUTURES_BASE_URL}/fapi/v1/premiumIndex"

    params = {
        "symbol": symbol
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()

    data = response.json()
    return float(data["markPrice"])


# -----------------------------
# Indicators
# -----------------------------

def add_indicators(df):
    df = df.copy()

    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

    delta = df["close"].diff()

    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()

    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()

    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    df["atr"] = true_range.rolling(14).mean()
    df["volume_sma20"] = df["volume"].rolling(20).mean()

    df["recent_high_20"] = df["high"].rolling(20).max()
    df["recent_low_20"] = df["low"].rolling(20).min()

    return df


# -----------------------------
# Multi-Timeframe Analysis
# -----------------------------

def get_higher_timeframe_trend(symbol):
    """Get trend direction from higher timeframes (1h and 4h)"""
    trends = []
    
    # Check 1h trend
    try:
        df_1h = fetch_higher_timeframe_klines(symbol, "1h")
        if len(df_1h) >= 50:
            df_1h = add_indicators(df_1h)
            last_row = df_1h.iloc[-1]
            ema20 = last_row["ema20"]
            ema50 = last_row["ema50"]
            ema200 = last_row["ema200"]
            
            if ema20 > ema50 > ema200:
                trends.append("STRONG_BULLISH")
            elif ema20 > ema50:
                trends.append("BULLISH")
            elif ema20 < ema50 < ema200:
                trends.append("STRONG_BEARISH")
            elif ema20 < ema50:
                trends.append("BEARISH")
            else:
                trends.append("NEUTRAL")
    except Exception as e:
        print(f"Error fetching 1h data: {e}")
        trends.append("UNKNOWN")
    
    # Check 4h trend
    try:
        df_4h = fetch_higher_timeframe_klines(symbol, "4h")
        if len(df_4h) >= 50:
            df_4h = add_indicators(df_4h)
            last_row = df_4h.iloc[-1]
            ema20 = last_row["ema20"]
            ema50 = last_row["ema50"]
            ema200 = last_row["ema200"]
            
            if ema20 > ema50 > ema200:
                trends.append("STRONG_BULLISH")
            elif ema20 > ema50:
                trends.append("BULLISH")
            elif ema20 < ema50 < ema200:
                trends.append("STRONG_BEARISH")
            elif ema20 < ema50:
                trends.append("BEARISH")
            else:
                trends.append("NEUTRAL")
    except Exception as e:
        print(f"Error fetching 4h data: {e}")
        trends.append("UNKNOWN")
    
    return trends


def confirm_with_higher_timeframe(signal_side, higher_trends):
    """Strict higher timeframe confirmation."""
    bullish_trends = ["BULLISH", "STRONG_BULLISH"]
    bearish_trends = ["BEARISH", "STRONG_BEARISH"]

    bullish_count = sum(1 for t in higher_trends if t in bullish_trends)
    bearish_count = sum(1 for t in higher_trends if t in bearish_trends)

    strong_bullish_count = sum(1 for t in higher_trends if t == "STRONG_BULLISH")
    strong_bearish_count = sum(1 for t in higher_trends if t == "STRONG_BEARISH")

    if signal_side == "LONG":
        if REQUIRE_STRONG_HTF_ALIGNMENT:
            return bullish_count >= 2 or strong_bullish_count >= 1
        return bullish_count >= 1

    if signal_side == "SHORT":
        if REQUIRE_STRONG_HTF_ALIGNMENT:
            return bearish_count >= 2 or strong_bearish_count >= 1
        return bearish_count >= 1

    return False


# -----------------------------
# Market Regime Detection
# -----------------------------

def detect_market_regime(df):
    """
    Detect if market is trending or ranging
    Returns: "TRENDING", "RANGING", or "VOLATILE"
    """
    if len(df) < 50:
        return "UNKNOWN"
    
    # Calculate ADX for trend strength
    high = df["high"]
    low = df["low"]
    close = df["close"]
    
    # Calculate True Range
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    
    # Calculate +DM and -DM
    plus_dm = high.diff()
    minus_dm = low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    minus_dm = abs(minus_dm)
    
    # Smooth DM
    plus_di = 100 * (plus_dm.rolling(14).mean() / atr)
    minus_di = 100 * (minus_dm.rolling(14).mean() / atr)
    
    # Calculate DX and ADX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
    adx = dx.rolling(14).mean()
    
    current_adx = adx.iloc[-1]
    
    # Calculate Bollinger Bands width for volatility
    period = 20
    std = df["close"].rolling(period).std()
    bb_width = (std * 2) / df["close"].rolling(period).mean() * 100
    avg_bb_width = bb_width.rolling(50).mean().iloc[-1]
    current_bb_width = bb_width.iloc[-1]
    
    # Calculate price range percentage
    price_range = (df["high"].max() - df["low"].min()) / df["low"].min() * 100
    
    # Determine regime
    if pd.isna(current_adx):
        return "UNKNOWN"
    
    if current_adx > 25:
        if price_range > 15:
            return "STRONG_TRENDING"
        return "TRENDING"
    elif current_adx < 20:
        if current_bb_width > avg_bb_width * 1.5:
            return "VOLATILE_RANGING"
        return "RANGING"
    else:
        return "NEUTRAL"


def should_trade_in_regime(regime, confidence_score):
    """Determine if we should trade based on market regime"""
    if regime == "STRONG_TRENDING":
        return True, 1.2
    elif regime == "TRENDING":
        return True, 1.1
    elif regime == "RANGING":
        return False, 0.7
    elif regime == "VOLATILE_RANGING":
        return False, 0.5
    else:
        return True, 1.0


# -----------------------------
# Strategy logic
# -----------------------------

def get_trade_style():
    if INTERVAL in ["1m", "3m", "5m", "15m"]:
        return "scalping"
    elif INTERVAL in ["30m", "1h", "2h", "4h"]:
        return "day"
    else:
        return "swing"

def calculate_tp_levels(entry, stop_distance, side):
    """
    Calculate TP levels for different trading styles
    Returns dict with scalping (3), day_trading (3), swing_trading (2) = 8 total
    """
    
    # Different multiplier sets for each style
    style_multipliers = {
        "scalping": [0.3, 0.6, 1.0],      # 3 targets - quick profits
        "day_trading": [1.2, 1.8, 2.5],   # 3 targets - medium targets
        "swing_trading": [3.0, 4.5]       # 2 targets - longer term
    }
    
    tp_levels = {
        "scalping": [],
        "day_trading": [],
        "swing_trading": []
    }
    
    if side == "LONG":
        for style, multipliers in style_multipliers.items():
            for m in multipliers:
                tp = entry + (stop_distance * m)
                tp_levels[style].append(tp)
    else:  # SHORT
        for style, multipliers in style_multipliers.items():
            for m in multipliers:
                tp = entry - (stop_distance * m)
                tp_levels[style].append(tp)
    
    # Log target creation for debugging
    print(f"  TP Levels Created - Scalping: {len(tp_levels['scalping'])}, Day: {len(tp_levels['day_trading'])}, Swing: {len(tp_levels['swing_trading'])}")
    
    return tp_levels


def calculate_confidence_score(row, previous_row, side):
    score = 0

    close = row["close"]
    ema20 = row["ema20"]
    ema50 = row["ema50"]
    ema200 = row["ema200"]
    rsi = row["rsi"]
    macd = row["macd"]
    macd_signal = row["macd_signal"]
    volume = row["volume"]
    volume_sma20 = row["volume_sma20"]
    atr = row["atr"]

    recent_high = previous_row["recent_high_20"]
    recent_low = previous_row["recent_low_20"]

    if pd.isna(rsi) or pd.isna(atr) or pd.isna(volume_sma20):
        return 0

    if side == "LONG":
        if ema20 > ema50 > ema200:
            score += 30
        elif ema20 > ema50:
            score += 20
        elif close > ema200:
            score += 10
    else:
        if ema20 < ema50 < ema200:
            score += 30
        elif ema20 < ema50:
            score += 20
        elif close < ema200:
            score += 10

    if side == "LONG":
        if macd > macd_signal and macd > 0:
            score += 20
        elif macd > macd_signal:
            score += 12
    else:
        if macd < macd_signal and macd < 0:
            score += 20
        elif macd < macd_signal:
            score += 12

    if side == "LONG":
        if 50 <= rsi <= 68:
            score += 15
        elif 45 <= rsi < 50:
            score += 8
    else:
        if 32 <= rsi <= 50:
            score += 15
        elif 50 < rsi <= 55:
            score += 8

    if volume > volume_sma20 * 1.3:
        score += 15
    elif volume > volume_sma20:
        score += 8

    if side == "LONG":
        if close > recent_high:
            score += 10
        elif close > ema20:
            score += 5
    else:
        if close < recent_low:
            score += 10
        elif close < ema20:
            score += 5

    atr_percent = (atr / close) * 100

    if 0.3 <= atr_percent <= 4.0:
        score += 10
    elif 0.15 <= atr_percent < 0.3:
        score += 5

    return min(score, 100)


def passes_strong_signal_filters(row, previous_row, side):
    close = float(row["close"])
    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    volume = float(row["volume"])
    volume_sma20 = float(row["volume_sma20"])
    atr = float(row["atr"])
    rsi = float(row["rsi"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    ema200 = float(row["ema200"])
    macd = float(row["macd"])
    macd_signal = float(row["macd_signal"])

    if pd.isna(volume_sma20) or pd.isna(atr) or pd.isna(rsi):
        return False, "indicator data not ready"

    atr_percent = (atr / close) * 100

    if atr_percent < MIN_ATR_PERCENT:
        return False, f"ATR too low: {atr_percent:.2f}%"

    if atr_percent > MAX_ATR_PERCENT:
        return False, f"ATR too high: {atr_percent:.2f}%"

    if volume < volume_sma20 * MIN_VOLUME_MULTIPLIER:
        return False, f"volume too weak: {volume / volume_sma20:.2f}x"

    candle_range = high - low
    candle_body = abs(close - open_price)

    if candle_range <= 0:
        return False, "bad candle range"

    body_ratio = candle_body / candle_range

    if body_ratio < 0.45:
        return False, f"weak candle body: {body_ratio:.2f}"

    if side == "LONG":
        if close <= open_price:
            return False, "LONG rejected: candle is not bullish"

        if not (ema20 > ema50 and close > ema20 and close > ema200):
            return False, "LONG rejected: trend not clean"

        if not (macd > macd_signal and macd > 0):
            return False, "LONG rejected: MACD not strong"

        if not (52 <= rsi <= 68):
            return False, f"LONG rejected: RSI not ideal: {rsi:.1f}"

    else:
        if close >= open_price:
            return False, "SHORT rejected: candle is not bearish"

        if not (ema20 < ema50 and close < ema20 and close < ema200):
            return False, "SHORT rejected: trend not clean"

        if not (macd < macd_signal and macd < 0):
            return False, "SHORT rejected: MACD not strong"

        if not (32 <= rsi <= 48):
            return False, f"SHORT rejected: RSI not ideal: {rsi:.1f}"

    return True, "passed"


def build_signal(symbol, df):
    df = add_indicators(df)

    if len(df) < 220:
        return None

    row = df.iloc[-1]
    previous_row = df.iloc[-2]

    long_score = calculate_confidence_score(row, previous_row, "LONG")
    short_score = calculate_confidence_score(row, previous_row, "SHORT")

    score_gap = abs(long_score - short_score)

    if score_gap < MIN_SCORE_GAP:
        print(
            f"{symbol}: Skipping - unclear direction. "
            f"LONG {long_score}, SHORT {short_score}, gap {score_gap}, required {MIN_SCORE_GAP}"
        )
        return None

    if long_score > short_score:
        side = "LONG"
        confidence_score = long_score
    else:
        side = "SHORT"
        confidence_score = short_score

    passed_filters, filter_reason = passes_strong_signal_filters(row, previous_row, side)

    if not passed_filters:
        print(f"{symbol}: Skipping - {filter_reason}")
        return None

    # MARKET REGIME DETECTION
    regime = detect_market_regime(df)
    should_trade, regime_multiplier = should_trade_in_regime(regime, confidence_score)
    
    if not should_trade:
        print(f"{symbol}: Skipping - market is {regime}, not ideal for trading")
        return None

    if regime not in ["TRENDING", "STRONG_TRENDING"]:
        print(f"{symbol}: Skipping - only trending markets allowed. Current regime: {regime}")
        return None

    confidence_score = int(confidence_score * regime_multiplier)
    
    # MULTI-TIMEFRAME CONFIRMATION
    try:
        higher_trends = get_higher_timeframe_trend(symbol)
        mtf_confirmed = confirm_with_higher_timeframe(side, higher_trends)
        
        if not mtf_confirmed:
            print(f"{symbol}: Skipping - {side} signal conflicts with higher timeframe trends")
            return None
        
        bullish_count = sum(1 for t in higher_trends if t in ["BULLISH", "STRONG_BULLISH"])
        bearish_count = sum(1 for t in higher_trends if t in ["BEARISH", "STRONG_BEARISH"])
        
        if (side == "LONG" and bullish_count >= 2) or (side == "SHORT" and bearish_count >= 2):
            confidence_score = int(confidence_score * 1.15)
            print(f"{symbol}: Multi-timeframe alignment bonus +15% confidence")
            
    except Exception as e:
        print(f"{symbol}: Multi-timeframe check failed - {e}")

    if confidence_score < MIN_CONFIDENCE_SCORE:
        print(f"{symbol}: Confidence score {confidence_score} below minimum {MIN_CONFIDENCE_SCORE}")
        return None

    entry = float(row["close"])
    atr = float(row["atr"])

    if atr <= 0:
        return None

    # REDUCE LEVERAGE FOR SHORTS (safer)
    leverage = LEVERAGE_LEVELS[0]
    if side == "SHORT":
        leverage = min(leverage, 5)  # Max 5x for shorts
        print(f"{symbol}: Reduced leverage to {leverage}x for SHORT position")
    
    trade_style = get_trade_style()
    
    # ADJUST STOP DISTANCE BASED ON MARKET REGIME
    if regime == "STRONG_TRENDING":
        stop_multiplier = 1.0
    elif regime == "TRENDING":
        stop_multiplier = 1.25
    else:
        stop_multiplier = 1.5
    
    stop_distance = atr * stop_multiplier
    
    # ENSURE MINIMUM STOP DISTANCE (1.5% minimum)
    min_stop_percent = 0.015  # 1.5% minimum
    min_stop_distance = entry * min_stop_percent
    
    if stop_distance < min_stop_distance:
        old_stop = stop_distance
        stop_distance = min_stop_distance
        print(f"{symbol}: Adjusted stop from {old_stop:.6f} to {stop_distance:.6f} (minimum 1.5%)")
    
    # CHECK MINIMUM VOLATILITY - Don't trade when too quiet
    atr_percent = (atr / entry) * 100
    if atr_percent < 0.3:
        print(f"{symbol}: Skipping - volatility too low (ATR {atr_percent:.2f}%)")
        return None
    
    # WIDER STOP FOR SHORTS (extra safety)
    if side == "SHORT":
        stop_distance = stop_distance * 1.2
        print(f"{symbol}: Added 20% buffer to stop for SHORT position")

    if side == "LONG":
        stop_loss = entry - stop_distance
    else:
        stop_loss = entry + stop_distance
    
    # CALCULATE CATEGORIZED TP LEVELS (8 targets total)
    tp_levels = calculate_tp_levels(entry, stop_distance, side)
    
    # Verify we have all 8 targets
    total_targets = len(tp_levels["scalping"]) + len(tp_levels["day_trading"]) + len(tp_levels["swing_trading"])
    if total_targets != 8:
        print(f"{symbol}: WARNING - Expected 8 targets, got {total_targets}")
    
    # Flatten all targets for internal tracking
    all_targets = []
    all_targets.extend(tp_levels["scalping"])
    all_targets.extend(tp_levels["day_trading"])
    all_targets.extend(tp_levels["swing_trading"])

    # Calculate entry range (entry ± 1%)
    entry_range_percent = 0.01
    entry_low = entry * (1 - entry_range_percent)
    entry_high = entry * (1 + entry_range_percent)

    signal_key = f"{today_key()}:{symbol}:{side}"

    # Log the trade parameters for debugging
    print(f"{symbol}: {side} at {entry:.4f}")
    print(f"  Stop Loss: {stop_loss:.4f} ({abs(stop_loss - entry)/entry*100:.2f}%)")
    print(f"  Targets: {len(all_targets)} levels (Scalping:{len(tp_levels['scalping'])} Day:{len(tp_levels['day_trading'])} Swing:{len(tp_levels['swing_trading'])}")
    print(f"  Confidence: {confidence_score}/100 | Regime: {regime} | Leverage: {leverage}x")

    return {
        "signal_key": signal_key,
        "date": today_key(),
        "time": now_local().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "side": side,
        "direction": side,
        "entry": entry,
        "entry_price": entry,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "stop_loss": stop_loss,
        "targets": all_targets,
        "tp_levels": tp_levels,
        "targets_hit": [],
        "confidence_score": confidence_score,
        "leverage": leverage,
        "risk_percent": RISK_PERCENT,
        "trade_style": trade_style,
        "interval": INTERVAL,
        "rsi": float(row["rsi"]),
        "atr": atr,
        "market_regime": regime,
        "status": "OPEN",
        "opened_at": now_local().strftime("%Y-%m-%d %H:%M:%S")
    }


# -----------------------------
# Open and closed trades
# -----------------------------

def load_open_trades():
    return read_json_file(OPEN_TRADES_FILE, [])


def save_open_trades(open_trades):
    write_json_file(OPEN_TRADES_FILE, open_trades)


def load_closed_trades():
    return read_json_file(CLOSED_TRADES_FILE, [])


def save_closed_trades(closed_trades):
    write_json_file(CLOSED_TRADES_FILE, closed_trades)


def has_open_trade_for_symbol(symbol):
    open_trades = load_open_trades()

    for trade in open_trades:
        if trade.get("symbol") == symbol and trade.get("status", "OPEN") == "OPEN":
            return True

    return False


def add_open_trade(signal):
    open_trades = load_open_trades()

    signal = dict(signal)
    signal.setdefault("id", f"{signal.get('symbol', 'UNKNOWN')}_{int(time.time())}")
    signal.setdefault("targets_hit", [])
    signal.setdefault("status", "OPEN")
    signal.setdefault("opened_at", now_local().strftime("%Y-%m-%d %H:%M:%S"))

    open_trades.append(signal)
    save_open_trades(open_trades)


def close_trade(trade, result, close_price):
    closed_trades = load_closed_trades()

    trade = dict(trade)
    trade["status"] = "CLOSED"
    trade["result"] = result
    trade["closed_at"] = now_local().strftime("%Y-%m-%d %H:%M:%S")
    trade["close_price"] = close_price

    closed_trades.append(trade)
    save_closed_trades(closed_trades)


# -----------------------------
# Telegram - Result Cards (Images)
# -----------------------------

def calculate_pnl_percent(trade, current_price):
    entry = get_trade_entry(trade)
    side = get_trade_side(trade)
    
    if side == "LONG":
        pnl_percent = ((current_price - entry) / entry) * 100
    else:
        pnl_percent = ((entry - current_price) / entry) * 100
    
    leverage = trade.get("leverage", 10)
    return pnl_percent * leverage


def create_result_card(trade, pnl_percent, current_price, event_label):
    """Create a professional result card similar to the spoiler signal format"""
    
    fig, ax = plt.subplots(figsize=(12, 10))
    fig.patch.set_facecolor('#0d1117')
    ax.set_facecolor('#0d1117')
    
    ax.axis('off')
    
    entry = get_trade_entry(trade)
    side = get_trade_side(trade)
    stop_loss = trade["stop_loss"]
    targets_hit = len(trade.get("targets_hit", []))
    total_targets = len(trade["targets"])
    leverage = trade.get("leverage", 10)
    
    is_closed = "STOP" in event_label or "FINAL" in event_label or "LOSS" in event_label
    
    if pnl_percent >= 0:
        pnl_color = '#00ff88'
        pnl_bg_color = '#0a2a1a'
    else:
        pnl_color = '#ff4466'
        pnl_bg_color = '#2a0a1a'
    
    if "STOP" in event_label or "LOSS" in event_label:
        result_text = "STOP LOSS"
        result_emoji = "❌"
        border_color = '#ff4466'
    elif "FINAL" in event_label or "ALL" in event_label:
        result_text = "TRADE COMPLETED"
        result_emoji = "🏁"
        border_color = '#00ff88'
    elif "NEAR" in event_label:
        result_text = "NEAR TARGET"
        result_emoji = "⚠️"
        border_color = '#ffaa00'
    else:
        result_text = f"{event_label} HIT"
        result_emoji = "✅"
        border_color = '#00aaff'

    status_text = f"{result_emoji} {result_text} • SL {format_price(stop_loss)} • Hits {targets_hit}/{total_targets}"

    if is_closed:
        exit_price = current_price
        status = "CLOSED"
    else:
        exit_price = None
        status = "ACTIVE"
    
    margin = 10.00
    pnl_usd = (pnl_percent / 100) * margin * leverage
    
    header_text = "# Binance Futures SIGNALS\n\n@CRYPTOSIGNALS\nvia @Crypto_tradingbot"
    ax.text(0.05, 0.95, header_text, transform=ax.transAxes, fontsize=10, 
            color='#888888', verticalalignment='top', fontweight='bold')
    
    symbol_text = f"## {trade['symbol']}\n**PERPETUAL**"
    ax.text(0.05, 0.82, symbol_text, transform=ax.transAxes, fontsize=14, 
            color='white', verticalalignment='top', fontweight='bold')
    
    direction_text = f"- **{side}**\n  - {leverage}x LEVERAGE"
    ax.text(0.05, 0.72, direction_text, transform=ax.transAxes, fontsize=12, 
            color='#00aaff', verticalalignment='top', fontweight='bold')
    ax.text(0.05, 0.69, status_text, transform=ax.transAxes, fontsize=10,
            color='#ffffff', verticalalignment='top', fontweight='medium')
    
    pnl_sign = '+' if pnl_percent >= 0 else ''
    pnl_display = f"{pnl_sign}{pnl_percent:.2f}%"
    
    pnl_box = plt.Rectangle((0.5, 0.65), 0.45, 0.15, facecolor=pnl_bg_color, 
                            edgecolor=pnl_color, linewidth=2, alpha=0.8)
    ax.add_patch(pnl_box)
    
    ax.text(0.725, 0.72, pnl_display, transform=ax.transAxes, fontsize=28, 
            color=pnl_color, verticalalignment='center', horizontalalignment='center',
            fontweight='bold')
    
    ax.text(0.725, 0.68, "REALIZED PNL", transform=ax.transAxes, fontsize=8, 
            color='#888888', verticalalignment='center', horizontalalignment='center')
    
    chart_x = np.linspace(0, 1, 50)
    if pnl_percent >= 0:
        chart_y = 0.3 + 0.6 * np.sin(chart_x * np.pi) * (pnl_percent / 100)
    else:
        chart_y = 0.3 - 0.6 * np.sin(chart_x * np.pi) * (abs(pnl_percent) / 100)
    
    chart_ax = fig.add_axes([0.05, 0.45, 0.9, 0.15])
    chart_ax.set_facecolor('#0d1117')
    chart_ax.plot(chart_x, chart_y, color=pnl_color, linewidth=2)
    chart_ax.fill_between(chart_x, 0, chart_y, color=pnl_color, alpha=0.3)
    chart_ax.set_ylim(-0.5, 0.8)
    chart_ax.set_xlim(0, 1)
    chart_ax.axis('off')
    
    current_time = datetime.now().strftime("%d %b %Y • %H:%M:%S")
    ax.text(0.05, 0.40, f"{current_time} (UTC)", transform=ax.transAxes, fontsize=8, 
            color='#666666', verticalalignment='top')
    
    table_data = [
        ["ENTRY PRICE", "AVG. EXIT PRICE", "TOTAL PNL", "STATUS"],
        [f"{format_price(entry)} USD", 
         f"{format_price(exit_price) if exit_price else '--'} USD" if is_closed else "--- USD",
         f"{pnl_sign}{pnl_percent:.2f}% (+{pnl_usd:.2f} USD)" if pnl_percent >= 0 else f"{pnl_sign}{pnl_percent:.2f}% ({pnl_usd:.2f} USD)",
         f"{status} TRADE {'CLOSED' if is_closed else 'ACTIVE'}"]
    ]
    
    table = ax.table(cellText=table_data, loc='center', bbox=[0.05, 0.20, 0.9, 0.18])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    
    for i, cell in enumerate(table._cells):
        if i < len(table_data[0]):
            table._cells[cell].set_facecolor('#1a1a2e')
            table._cells[cell].set_text_props(weight='bold', color='#00aaff')
        else:
            table._cells[cell].set_facecolor('#0d1117')
            if (i - len(table_data[0])) % 4 == 2:
                table._cells[cell].set_text_props(color=pnl_color)
            else:
                table._cells[cell].set_text_props(color='white')
        table._cells[cell].set_edgecolor('#333333')
    
    info_data = [
        ["LEVERAGE", "MARGIN (USDT)", "RISK LEVEL", "RISK PER TRADE", "POSITION"],
        [f"{leverage}x", f"{margin:.2f}", "MEDIUM", f"{trade.get('risk_percent', 3.5)}%", side]
    ]
    
    info_table = ax.table(cellText=info_data, loc='center', bbox=[0.05, 0.05, 0.9, 0.12])
    info_table.auto_set_font_size(False)
    info_table.set_fontsize(8)
    
    for i, cell in enumerate(info_table._cells):
        if i < len(info_data[0]):
            info_table._cells[cell].set_facecolor('#1a1a2e')
            info_table._cells[cell].set_text_props(weight='bold', color='#888888')
        else:
            info_table._cells[cell].set_facecolor('#0d1117')
            info_table._cells[cell].set_text_props(color='white')
        info_table._cells[cell].set_edgecolor('#333333')
    
    brand_text = "AUTO EXECUTION • SIGNAL BASED • RISK RIGHT • MULTI EXCHANGE • 24/7 TRADING"
    ax.text(0.5, 0.01, brand_text, transform=ax.transAxes, fontsize=7, 
            color='#555555', verticalalignment='bottom', horizontalalignment='center')
    
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color(border_color)
        spine.set_linewidth(2)
    
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='#0d1117')
    buf.seek(0)
    plt.close()
    
    return buf


def send_photo_to_telegram(photo_bytes, caption=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    
    files = {'photo': photo_bytes}
    data = {'chat_id': TELEGRAM_CHAT_ID}
    
    if caption:
        data['caption'] = caption
    
    response = requests.post(url, data=data, files=files, timeout=15)
    response.raise_for_status()
    return response.json()


# -----------------------------
# Telegram - Text Messages
# -----------------------------

def format_price(value):
    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:.4f}"

    return f"{value:.8f}"


def get_trade_side(trade):
    side = trade.get("side") or trade.get("direction", "")
    side = str(side).upper()

    if side == "SHORT":
        return "SHORT"

    if side == "LONG":
        return "LONG"

    return None


def get_trade_entry(trade):
    if trade.get("entry") is not None:
        return float(trade["entry"])

    if trade.get("entry_price") is not None:
        return float(trade["entry_price"])

    return 0.0


def get_target_category(trade, target_index):
    tp_levels = trade.get("tp_levels", {})
    
    idx_offset = 0
    for cat_name in ["scalping", "day_trading", "swing_trading"]:
        targets = tp_levels.get(cat_name, [])
        if target_index - 1 < idx_offset + len(targets):
            category_display = cat_name.upper().replace("_", " ")
            category_num = target_index - idx_offset
            return category_display, category_num
        idx_offset += len(targets)
    
    return "TARGET", target_index


def format_signal_message(signal):
    side = signal.get("side") or signal.get("direction")
    entry_low = signal.get("entry_low", signal["entry"] * 0.99)
    entry_high = signal.get("entry_high", signal["entry"] * 1.01)
    
    tp_levels = signal.get("tp_levels", {})
    
    entry_range_text = f"{format_price(entry_low)}-{format_price(entry_high)}"
    
    scalping_tps = tp_levels.get("scalping", [])
    day_tps = tp_levels.get("day_trading", [])
    swing_tps = tp_levels.get("swing_trading", [])
    
    scalping_text = ""
    for tp in scalping_tps:
        scalping_text += f"⛳️ {format_price(tp)}\n"
    
    day_text = ""
    for tp in day_tps:
        day_text += f"⛳️ {format_price(tp)}\n"
    
    swing_text = ""
    for tp in swing_tps:
        swing_text += f"⛳️ {format_price(tp)}\n"
    
    regime = signal.get("market_regime", "UNKNOWN")
    regime_emoji = {
        "STRONG_TRENDING": "🔥",
        "TRENDING": "📈",
        "RANGING": "🔄",
        "VOLATILE_RANGING": "⚠️",
        "NEUTRAL": "⚖️"
    }.get(regime, "❓")
    
    message = f"""⭕️ COIN: {signal["symbol"]}
↔️ SIGNAL TYPE: {side}
🔰 LEVERAGE: {signal["leverage"]}x cross
👽 EXCHANGE: Binance
♻️ Entry Point:
{entry_range_text}

📈 Take profit targets
🔰 SCALPING 🔰
{scalping_text}☀️ DAY TRADING
{day_text}🌗 SWING TRADING
{swing_text}🚭 Stop loss: {format_price(signal["stop_loss"])}

📊 Confidence: {signal["confidence_score"]}/100
📈 RSI: {signal["rsi"]:.1f}
🌊 Market Regime: {regime_emoji} {regime}
⏱️ Timeframe: {signal["interval"]}
🎯 Style: {signal["trade_style"].upper()}

#Binance #Crypto #{signal["symbol"]} #{side}
""".strip()
    
    return message


def format_tp_hit_message(trade, target_index, target_price, current_price):
    side = get_trade_side(trade)
    entry = get_trade_entry(trade)

    total_targets = len(trade["targets"])
    targets_hit = len(trade.get("targets_hit", []))
    
    category, category_num = get_target_category(trade, target_index)

    message = f"""✅ {category} TARGET {category_num} HIT ✅

Pair: {trade["symbol"]}
Direction: {side}

TP{target_index} Hit: {format_price(target_price)}
Current Price: {format_price(current_price)}

Entry: {format_price(entry)}
Stop Loss: {format_price(trade["stop_loss"])}

Targets Hit: {targets_hit}/{total_targets}

Trade still active.
""".strip()

    return message


def format_near_target_message(trade, target_index, target_price, current_price):
    side = get_trade_side(trade)
    entry = get_trade_entry(trade)

    total_targets = len(trade["targets"])
    targets_hit = len(trade.get("targets_hit", []))
    
    category, category_num = get_target_category(trade, target_index)

    message = f"""✅ NEAR {category} TARGET {category_num} ✅

Pair: {trade["symbol"]}
Direction: {side}

Target: {format_price(target_price)}
Current: {format_price(current_price)}

Entry: {format_price(entry)}
Stop: {format_price(trade["stop_loss"])}

Hits: {targets_hit}/{total_targets}

Trade active.
""".strip()

    return message


def format_stop_loss_message(trade, current_price):
    side = get_trade_side(trade)
    entry = get_trade_entry(trade)

    targets_hit = trade.get("targets_hit", [])

    if targets_hit:
        partial_text = f"Targets hit before SL: {len(targets_hit)}/{len(trade['targets'])}"
    else:
        partial_text = "No targets were hit before SL."

    message = f"""
❌ STOP LOSS HIT ❌

Pair: {trade["symbol"]}
Direction: {side}

Entry: {format_price(entry)}
Stop Loss: {format_price(trade["stop_loss"])}
Current Price: {format_price(current_price)}

{partial_text}

Trade closed.
""".strip()

    return message


def format_final_target_message(trade, current_price):
    side = get_trade_side(trade)
    entry = get_trade_entry(trade)
    pnl = calculate_pnl_percent(trade, current_price)
    pnl_sign = '+' if pnl >= 0 else ''

    message = f"""
🏁 TRADE COMPLETED - ALL TARGETS HIT 🏁

Pair: {trade["symbol"]}
Direction: {side}

Entry: {format_price(entry)}
Final Price: {format_price(current_price)}

Total P&L: {pnl_sign}{pnl:.2f}% (with {trade.get('leverage', 10)}x leverage)

✅ Full TP completed successfully!
""".strip()

    return message


def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    response = requests.post(url, data=payload, timeout=15)
    response.raise_for_status()

    return response.json()


# -----------------------------
# Result monitoring
# -----------------------------

def is_target_hit(side, current_price, target_price):
    if side == "LONG":
        return current_price >= target_price
    else:
        return current_price <= target_price


def is_stop_loss_hit(side, current_price, stop_loss):
    if side == "LONG":
        return current_price <= stop_loss
    else:
        return current_price >= stop_loss


def monitor_open_trades():
    if not RESULT_MONITOR_ENABLED:
        return

    open_trades = load_open_trades()

    if not open_trades:
        return

    print("Checking open trades for TP/SL results...")

    updated_open_trades = []

    for trade in open_trades:
        try:
            if trade.get("status", "OPEN") != "OPEN":
                updated_open_trades.append(trade)
                continue

            symbol = trade["symbol"]
            side = get_trade_side(trade)

            if side is None:
                print(f"{symbol}: invalid trade direction.")
                updated_open_trades.append(trade)
                continue

            stop_loss = float(trade["stop_loss"])
            targets = [float(t) for t in trade["targets"]]
            current_price = fetch_current_price(symbol)

            try:
                proximity_list = trade.get("proximity_alerts", [])

                for idx, target_price in enumerate(targets, start=1):
                    if idx in proximity_list:
                        continue
                    if PROXIMITY_PERCENT > 0 and abs(current_price - target_price) / target_price <= (PROXIMITY_PERCENT / 100.0):
                        pnl_now = calculate_pnl_percent(trade, current_price)
                        
                        msg = format_near_target_message(trade, target_index=idx, target_price=target_price, current_price=current_price)
                        send_telegram_message(msg)
                        
                        try:
                            card = create_result_card(trade, pnl_now, current_price, f"NEAR_TP_{idx}")
                            send_photo_to_telegram(card, f"⚡ Near TP{idx} • {trade['symbol']} • {pnl_now:+.2f}%")
                        except Exception as e:
                            print(f"Failed to send proximity image: {e}")
                        
                        proximity_list.append(idx)

                if not trade.get("stop_proximity_notified", False):
                    if PROXIMITY_PERCENT > 0 and abs(current_price - stop_loss) / stop_loss <= (PROXIMITY_PERCENT / 100.0):
                        pnl_now = calculate_pnl_percent(trade, current_price)
                        
                        msg = format_stop_loss_message(trade, current_price)
                        msg = msg.replace("STOP LOSS", "NEAR STOP")
                        send_telegram_message(msg)
                        
                        try:
                            card = create_result_card(trade, pnl_now, current_price, "NEAR_STOP")
                            send_photo_to_telegram(card, f"⚠️ Near Stop • {trade['symbol']} • {pnl_now:+.2f}%")
                        except Exception as e:
                            print(f"Failed to send stop proximity image: {e}")
                        
                        trade["stop_proximity_notified"] = True

                trade["proximity_alerts"] = proximity_list
            except Exception as e:
                print(f"Proximity alert error: {e}")

            if "targets_hit" not in trade:
                trade["targets_hit"] = []

            targets_hit = set(int(x) for x in trade.get("targets_hit", []))

            if is_stop_loss_hit(side, current_price, stop_loss):
                pnl_percent = calculate_pnl_percent(trade, current_price)
                
                text_message = format_stop_loss_message(trade, current_price)
                send_telegram_message(text_message)
                
                try:
                    card = create_result_card(trade, pnl_percent, current_price, "STOP_LOSS")
                    send_photo_to_telegram(card, f"❌ {trade['symbol']} STOP LOSS • {pnl_percent:+.2f}%")
                except Exception as e:
                    print(f"Failed to send SL image: {e}")

                close_trade(
                    trade=trade,
                    result="STOP_LOSS_HIT",
                    close_price=current_price
                )

                print(f"{symbol}: stop loss hit at {current_price} (PnL: {pnl_percent:.2f}%). Trade closed.")
                time.sleep(2)
                continue

            new_target_hits = []

            for index, target_price in enumerate(targets, start=1):
                if index in targets_hit:
                    continue

                if is_target_hit(side, current_price, target_price):
                    targets_hit.add(index)
                    new_target_hits.append((index, target_price))

            trade["targets_hit"] = sorted(list(targets_hit))

            for target_index, target_price in new_target_hits:
                if POST_EACH_TP_HIT:
                    pnl_percent = calculate_pnl_percent(trade, current_price)
                    
                    text_message = format_tp_hit_message(
                        trade=trade,
                        target_index=target_index,
                        target_price=target_price,
                        current_price=current_price
                    )
                    send_telegram_message(text_message)
                    
                    try:
                        card = create_result_card(trade, pnl_percent, current_price, f"TP_{target_index}")
                        send_photo_to_telegram(card, f"✅ {trade['symbol']} TP{target_index} • {pnl_percent:+.2f}%")
                    except Exception as e:
                        print(f"Failed to send TP image: {e}")
                    
                    print(f"{symbol}: TP{target_index} hit at {current_price} (PnL: {pnl_percent:.2f}%).")
                    time.sleep(2)

            if CLOSE_TRADE_ON_FINAL_TP and len(trade["targets_hit"]) == len(targets):
                pnl_percent = calculate_pnl_percent(trade, current_price)
                
                text_message = format_final_target_message(trade, current_price)
                send_telegram_message(text_message)
                
                try:
                    card = create_result_card(trade, pnl_percent, current_price, "FINAL_TARGET")
                    send_photo_to_telegram(card, f"🏁 {trade['symbol']} COMPLETED • {pnl_percent:+.2f}%")
                except Exception as e:
                    print(f"Failed to send final target image: {e}")

                close_trade(
                    trade=trade,
                    result="ALL_TARGETS_HIT",
                    close_price=current_price
                )

                print(f"{symbol}: all targets hit. Final PnL: {pnl_percent:.2f}%. Trade closed.")
                time.sleep(2)
                continue

            updated_open_trades.append(trade)

        except Exception as e:
            print(f"Error monitoring trade {trade.get('symbol', 'UNKNOWN')}: {e}")
            updated_open_trades.append(trade)

    save_open_trades(updated_open_trades)


# -----------------------------
# Main scanning logic
# -----------------------------

def scan_market():
    print(f"\n[{now_local().strftime('%Y-%m-%d %H:%M:%S')}] Scanning market...")

    if not is_manual_mode() and not is_signal_window_open():
        print("Outside signal window. No scan posted.")
        return

    if remaining_daily_slots() <= 0:
        print("Daily signal limit reached. No more signals today.")
        return

    if is_cooldown_active():
        remaining_mins = get_cooldown_remaining()
        print(f"Cooldown active. Next signal allowed in {remaining_mins} minutes.")
        return

    candidates = []

    for symbol in SYMBOLS:
        try:
            if has_open_trade_for_symbol(symbol):
                print(f"{symbol}: skipped because an open trade already exists.")
                continue

            if not can_post_for_symbol(symbol):
                print(f"{symbol}: daily limit for this symbol reached.")
                continue

            df = fetch_futures_klines(symbol)
            signal = build_signal(symbol, df)

            if signal is None:
                print(f"{symbol}: no high-confidence setup.")
                continue

            if already_posted_signal(signal["signal_key"]):
                print(f'{symbol}: signal already posted today.')
                continue

            candidates.append(signal)
            print(f'{symbol}: candidate found {signal["side"]} with score {signal["confidence_score"]}/100.')

        except Exception as e:
            print(f"{symbol}: error while scanning - {e}")

    if not candidates:
        print("No strong signals found.")
        return

    candidates.sort(key=lambda x: x["confidence_score"], reverse=True)

    slots = remaining_daily_slots()
    if slots <= 0:
        print("Daily signal limit reached. No more signals today.")
        return

    if len(candidates) > slots:
        print(f"Found {len(candidates)} strong candidates, posting the top {slots} now.")

    max_post_now = min(slots, MAX_SIGNALS_PER_SCAN)
    signals_to_post = candidates[:max_post_now]

    for signal in signals_to_post:
        if remaining_daily_slots() <= 0:
            print("Daily signal limit reached during posting. Stopping.")
            break

        if is_cooldown_active():
            print("Cooldown active during posting. Stopping.")
            break

        try:
            message = format_signal_message(signal)
            send_telegram_message(message)

            mark_signal_posted(signal["signal_key"], signal["symbol"])
            add_open_trade(signal)

            print(f'Posted {signal["symbol"]} {signal["side"]} with score {signal["confidence_score"]}/100.')
            time.sleep(2)

        except Exception as e:
            print(f'Failed to post {signal["symbol"]}: {e}')


def main():
    print("Futures trading signal bot started.")
    print(f"Max signals per day: {MAX_SIGNALS_PER_DAY}")
    print(f"Max signals per symbol: {MAX_SIGNALS_PER_SYMBOL}")
    print(f"Max signals per scan: {MAX_SIGNALS_PER_SCAN}")
    print(f"Signal cooldown: {SIGNAL_COOLDOWN_SECONDS // 60} minutes")
    print(f"Minimum confidence score: {MIN_CONFIDENCE_SCORE}")
    print(f"Minimum score gap: {MIN_SCORE_GAP}")
    print(f"Minimum volume multiplier: {MIN_VOLUME_MULTIPLIER}x")
    print(f"ATR filter: {MIN_ATR_PERCENT}% - {MAX_ATR_PERCENT}%")
    print(f"Strong HTF alignment required: {REQUIRE_STRONG_HTF_ALIGNMENT}")
    print(f"Timezone: {BOT_TIMEZONE}")

    if is_manual_mode():
        print("Manual mode enabled. Signal windows will be ignored.")

    if MANUAL_CHECK_FLAG in sys.argv:
        print("Manual check flag detected. Running open-trades monitor once.")
        monitor_open_trades()
        return

    if is_once_mode():
        monitor_open_trades()
        scan_market()
        return

    last_market_scan = 0

    while True:
        try:
            monitor_open_trades()

            current_time = time.time()

            if current_time - last_market_scan >= SCAN_INTERVAL_SECONDS:
                scan_market()
                last_market_scan = current_time

        except Exception as e:
            print(f"Main loop error: {e}")

        print(f"Sleeping for {RESULT_CHECK_INTERVAL_SECONDS} seconds...")
        time.sleep(RESULT_CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()