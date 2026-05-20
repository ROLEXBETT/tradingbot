import sys
import json
import time
import requests
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from io import BytesIO
import numpy as np

from pathlib import Path
from datetime import datetime, timedelta  # Fixed: timeDelta -> timedelta
from zoneinfo import ZoneInfo

# PREVENT MULTIPLE INSTANCES (Simple file method - works on all platforms)
import os
import time  # ADD THIS - missing time import

LOCK_FILE = "bot.lock"

if os.path.exists(LOCK_FILE):
    file_age = time.time() - os.path.getmtime(LOCK_FILE)
    if file_age < 3600:  # 1 hour
        print("❌ Another instance of the bot is already running! Exiting.")  # Fixed: X -> ❌
        print("   If you're sure no other instance is running, delete bot.lock file")
        sys.exit(1)
    else:
        os.remove(LOCK_FILE)

with open(LOCK_FILE, 'w') as f:  # Fixed: （ -> (, 'w') as f -> 'w') as f
    f.write(str(os.getpid()))

from config import (  # Fixed: ( -> (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,  # Fixed: BINANCE API SECRET -> BINANCE_API_SECRET
    TELEGRAM_BOT_TOKEN,  # Fixed: TEIEGRAM BOT TOKEN -> TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID,
    SYMBOLS,
    FUTURE_TYPE,
    INTERVAL,
    CANDLE_LIMIT,
    RISK_PERCENT,
    LEVERAGE_LEVELS,
    TARGET_MULTIPLIERS,
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
MAX_SIGNALS_PER_SYMBOL = getattr(__import__('config'), 'MAX_SIGNALS_PER_SYMBOL', 1)
SIGNAL_COOLDOWN_SECONDS = getattr(__import__('config'), 'SIGNAL_COOLDOWN_SECONDS', 3600)


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
# Strategy logic
# -----------------------------

def get_trade_style():
    if INTERVAL in ["1m", "3m", "5m", "15m"]:
        return "scalping"

    if INTERVAL in ["30m", "1h", "2h", "4h"]:
        return "day"

    return "swing"


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


def build_signal(symbol, df):
    df = add_indicators(df)

    if len(df) < 220:
        return None

    row = df.iloc[-1]
    previous_row = df.iloc[-2]

    long_score = calculate_confidence_score(row, previous_row, "LONG")
    short_score = calculate_confidence_score(row, previous_row, "SHORT")

    if long_score >= short_score:
        side = "LONG"
        confidence_score = long_score
    else:
        side = "SHORT"
        confidence_score = short_score

    if confidence_score < MIN_CONFIDENCE_SCORE:
        return None

    entry = float(row["close"])
    atr = float(row["atr"])

    if atr <= 0:
        return None

    leverage = LEVERAGE_LEVELS[0]
    trade_style = get_trade_style()
    multipliers = TARGET_MULTIPLIERS.get(trade_style, TARGET_MULTIPLIERS["day"])

    stop_distance = atr * 1.25

    if side == "LONG":
        stop_loss = entry - stop_distance
        targets = [entry + (stop_distance * m) for m in multipliers]
    else:
        stop_loss = entry + stop_distance
        targets = [entry - (stop_distance * m) for m in multipliers]

    signal_key = f"{today_key()}:{symbol}:{side}"

    return {
        "signal_key": signal_key,
        "date": today_key(),
        "time": now_local().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "side": side,
        "direction": side,
        "entry": entry,
        "entry_price": entry,
        "stop_loss": stop_loss,
        "targets": targets,
        "targets_hit": [],
        "confidence_score": confidence_score,
        "leverage": leverage,
        "risk_percent": RISK_PERCENT,
        "trade_style": trade_style,
        "interval": INTERVAL,
        "rsi": float(row["rsi"]),
        "atr": atr,
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
    fig, ax = plt.subplots(figsize=(10, 8))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#16213e')
    
    ax.axis('off')
    
    entry = get_trade_entry(trade)
    side = get_trade_side(trade)
    stop_loss = trade["stop_loss"]
    targets_hit = len(trade.get("targets_hit", []))
    total_targets = len(trade["targets"])
    
    if "STOP" in event_label or "LOSS" in event_label:
        result_color = '#e94560'
        result_text = "STOP LOSS"
        result_emoji = "❌"
        bg_color = '#2a1a2e'
    elif "FINAL" in event_label or "ALL" in event_label:
        result_color = '#4ecdc4'
        result_text = "ALL TARGETS HIT"
        result_emoji = "🏁"
        bg_color = '#1a2e2a'
    else:
        result_color = '#0f3460'
        result_text = f"{event_label} HIT"
        result_emoji = "✅"
        bg_color = '#1a2a3e'
    
    pnl_sign = '+' if pnl_percent >= 0 else ''
    exchange_text = "BINANCE FUTURES"
    
    info_lines = [
        f"{result_emoji} {result_text} {result_emoji}",
        "",
        f"{trade['symbol']} • {side} • {trade.get('leverage', 10)}x",
        f"{exchange_text}",
        "",
        f"📊 P&L: {pnl_sign}{pnl_percent:.2f}%",
        f"🎯 Targets: {targets_hit}/{total_targets}",
        "",
        f"💰 Entry: {format_price(entry)}",
        f"💵 Current: {format_price(current_price)}",
        f"🛑 Stop Loss: {format_price(stop_loss)}",
        "",
        f"📈 RSI: {trade.get('rsi', 0):.1f}",
        f"⚡ Confidence: {trade.get('confidence_score', 0)}/100",
        "",
        f"🕐 {now_local().strftime('%Y-%m-%d %H:%M:%S')}"
    ]
    
    info_text = "\n".join(info_lines)
    
    ax.text(0.5, 0.5, info_text, 
            transform=ax.transAxes,
            fontsize=12,
            verticalalignment='center',
            horizontalalignment='center',
            fontfamily='monospace',
            color='white',
            bbox=dict(boxstyle='round,pad=0.8', facecolor=bg_color, alpha=0.9, edgecolor=result_color, linewidth=3))
    
    ax.text(0.5, 0.92, f"🚨 {trade['symbol']} TRADE RESULT 🚨",
            transform=ax.transAxes,
            fontsize=16,
            fontweight='bold',
            horizontalalignment='center',
            color=result_color)
    
    ax.text(0.5, 0.08, "⚡ Automated Trading Signal Bot",
            transform=ax.transAxes,
            fontsize=8,
            horizontalalignment='center',
            color='#666666',
            alpha=0.7)
    
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='#1a1a2e')
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


def format_signal_message(signal):
    targets_text = ""

    for index, target in enumerate(signal["targets"], start=1):
        targets_text += f"TP{index}: {format_price(target)}\n"

    side = signal.get("side") or signal.get("direction")
    entry = signal.get("entry") if signal.get("entry") is not None else signal.get("entry_price")

    message = f"""
🚨 FUTURES TRADE SIGNAL 🚨

Pair: {signal["symbol"]}
Direction: {side}
Timeframe: {signal["interval"]}
Style: {signal["trade_style"].upper()}

Entry: {format_price(entry)}
Stop Loss: {format_price(signal["stop_loss"])}

Targets:
{targets_text}
Leverage: {signal["leverage"]}x
Risk: {signal["risk_percent"]}%

Confidence Score: {signal["confidence_score"]}/100
RSI: {signal["rsi"]:.2f}

Signals Today: {load_signal_state()["signals_sent_today"] + 1}/{MAX_SIGNALS_PER_DAY}

⚠️ Trade carefully. No signal is guaranteed.
""".strip()

    return message


def format_tp_hit_message(trade, target_index, target_price, current_price):
    side = trade.get("side") or trade.get("direction")
    entry = get_trade_entry(trade)

    total_targets = len(trade["targets"])
    targets_hit = len(trade.get("targets_hit", []))

    message = f"""
✅ TARGET {target_index} HIT ✅

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


def format_stop_loss_message(trade, current_price):
    side = trade.get("side") or trade.get("direction")
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
    side = trade.get("side") or trade.get("direction")
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

    if side == "SHORT":
        return current_price <= target_price

    return False


def is_stop_loss_hit(side, current_price, stop_loss):
    if side == "LONG":
        return current_price <= stop_loss

    if side == "SHORT":
        return current_price >= stop_loss

    return False


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

            # Proximity alerts
            try:
                proximity_list = trade.get("proximity_alerts", [])

                for idx, target_price in enumerate(targets, start=1):
                    if idx in proximity_list:
                        continue
                    if abs(current_price - target_price) / target_price <= (PROXIMITY_PERCENT / 100.0):
                        pnl_now = calculate_pnl_percent(trade, current_price)
                        
                        msg = format_tp_hit_message(trade, target_index=idx, target_price=target_price, current_price=current_price)
                        msg = msg.replace("TARGET", "NEAR TARGET")
                        send_telegram_message(msg)
                        
                        try:
                            card = create_result_card(trade, pnl_now, current_price, f"NEAR_TP_{idx}")
                            send_photo_to_telegram(card, f"⚡ Near TP{idx} • {trade['symbol']} • {pnl_now:+.2f}%")
                        except Exception as e:
                            print(f"Failed to send proximity image: {e}")
                        
                        proximity_list.append(idx)

                if not trade.get("stop_proximity_notified", False):
                    if abs(current_price - stop_loss) / stop_loss <= (PROXIMITY_PERCENT / 100.0):
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

            # Stop loss check
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

            # Target checks
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

            # Close trade if all targets are hit
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

    # Check cooldown
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
            print(
                f'{symbol}: candidate found '
                f'{signal["side"]} '
                f'with score {signal["confidence_score"]}/100.'
            )

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

    signals_to_post = candidates[:slots]

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

            print(
                f'Posted {signal["symbol"]} {signal["side"]} '
                f'with score {signal["confidence_score"]}/100.'
            )

            time.sleep(2)

        except Exception as e:
            print(f'Failed to post {signal["symbol"]}: {e}')


def main():
    print("Futures trading signal bot started.")
    print(f"Max signals per day: {MAX_SIGNALS_PER_DAY}")
    print(f"Max signals per symbol: {MAX_SIGNALS_PER_SYMBOL}")
    print(f"Signal cooldown: {SIGNAL_COOLDOWN_SECONDS // 60} minutes")
    print(f"Minimum confidence score: {MIN_CONFIDENCE_SCORE}")
    print(f"Timezone: {BOT_TIMEZONE}")

    if is_manual_mode():
        print("Manual mode enabled. Signal windows will be ignored.")

    # One-shot open-trades check when requested
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