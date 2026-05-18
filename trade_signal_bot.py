import json
import sys
import time
import os
from datetime import datetime, timezone
from typing import Tuple, Optional

import pandas as pd
import requests
from binance.client import Client
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
from PIL import Image, ImageDraw, ImageFont

from config import (
    BINANCE_API_KEY,
    BINANCE_API_SECRET,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    SYMBOLS,
    INTERVAL,
    CANDLE_LIMIT,
    LEVERAGE_LEVELS,
    TARGET_MULTIPLIERS,
    MAX_SIGNALS_PER_DAY,
    MORNING_SIGNAL_WINDOW,
    EVENING_SIGNAL_WINDOW,
    STATE_FILE,
    MANUAL_OVERRIDE_FLAG,
    OPEN_TRADES_FILE,
    CLOSED_TRADES_FILE,
)


def load_json_file(path, default):
    if not os.path.exists(path):
        save_json_file(path, default)
        return default

    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception:
        return default


def save_json_file(path, data):
    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2)


def load_state():
    return load_json_file(STATE_FILE, {
        "last_reset_date": datetime.now(timezone.utc).date().isoformat(),
        "signals_sent_today": 0,
        "morning_signal_sent": False,
        "evening_signal_sent": False,
        "sent_symbols_today": [],
    })


def save_state(state):
    save_json_file(STATE_FILE, state)


def load_open_trades():
    return load_json_file(OPEN_TRADES_FILE, [])


def save_open_trades(trades):
    save_json_file(OPEN_TRADES_FILE, trades)


def load_closed_trades():
    return load_json_file(CLOSED_TRADES_FILE, [])


def save_closed_trades(trades):
    save_json_file(CLOSED_TRADES_FILE, trades)


def within_time_window(now: datetime, window: Tuple[str, str]) -> bool:
    start_hour, start_minute = map(int, window[0].split(":"))
    end_hour, end_minute = map(int, window[1].split(":"))

    start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)

    if start <= end:
        return start <= now <= end

    return now >= start or now <= end


def get_current_window(now: datetime, state: dict) -> Optional[str]:
    if not state["morning_signal_sent"] and within_time_window(now, MORNING_SIGNAL_WINDOW):
        return "morning"

    if not state["evening_signal_sent"] and within_time_window(now, EVENING_SIGNAL_WINDOW):
        return "evening"

    return None


def load_klines(client, symbol, interval, limit):
    raw = client.get_klines(symbol=symbol, interval=interval, limit=limit)

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades",
        "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore",
    ])

    df[["open", "high", "low", "close", "volume"]] = df[
        ["open", "high", "low", "close", "volume"]
    ].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def compute_indicators(df):
    df["ema9"] = EMAIndicator(df["close"], window=9).ema_indicator()
    df["ema21"] = EMAIndicator(df["close"], window=21).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()

    macd = MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    df["atr"] = AverageTrueRange(
        df["high"], df["low"], df["close"], window=14
    ).average_true_range()

    df["volume_sma"] = df["volume"].rolling(20).mean()

    return df


def get_current_price(client, symbol):
    ticker = client.get_symbol_ticker(symbol=symbol)
    return float(ticker["price"])


def build_signal(df, symbol, leverage):
    row = df.iloc[-1]
    price = row["close"]

    if pd.isna(row["ema200"]) or pd.isna(row["volume_sma"]) or pd.isna(row["atr"]):
        return None

    atr = max(row["atr"], price * 0.0015)

    bullish_trend = row["ema50"] > row["ema200"]
    bearish_trend = row["ema50"] < row["ema200"]

    volume_ok = row["volume"] > row["volume_sma"] * 1.2
    atr_ok = row["atr"] > price * 0.0015
    trend_strength_ok = abs(row["ema50"] - row["ema200"]) > price * 0.002

    long_bias = (
        bullish_trend
        and row["ema9"] > row["ema21"] > row["ema50"] > row["ema200"]
        and 45 <= row["rsi"] <= 68
        and row["macd"] > row["macd_signal"]
        and volume_ok
        and atr_ok
        and trend_strength_ok
    )

    short_bias = (
        bearish_trend
        and row["ema9"] < row["ema21"] < row["ema50"] < row["ema200"]
        and 32 <= row["rsi"] <= 55
        and row["macd"] < row["macd_signal"]
        and volume_ok
        and atr_ok
        and trend_strength_ok
    )

    print(f"\n📊 Analysis for {symbol}")
    print(f"Price: {price:.6f}")
    print(f"RSI: {row['rsi']:.2f}")
    print(f"Volume OK: {volume_ok}")
    print(f"ATR OK: {atr_ok}")
    print(f"Trend strength OK: {trend_strength_ok}")

    if long_bias:
        direction = "Long"
        entry_low = price - atr * 0.4
        entry_high = price + atr * 0.25
        stop_loss = price - atr * 1.4

    elif short_bias:
        direction = "Short"
        entry_low = price - atr * 0.25
        entry_high = price + atr * 0.4
        stop_loss = price + atr * 1.4

    else:
        print("⚪️ No high-quality signal.")
        return None

    all_targets = []

    for _, multipliers in TARGET_MULTIPLIERS.items():
        for mult in multipliers:
            if direction == "Long":
                tp = price + atr * mult
            else:
                tp = price - atr * mult

            all_targets.append(round(tp, 6))

    if direction == "Long":
        all_targets = sorted(all_targets)
    else:
        all_targets = sorted(all_targets, reverse=True)

    return {
        "id": f"{symbol}_{int(time.time())}",
        "symbol": symbol,
        "direction": direction,
        "entry_price": round(price, 6),
        "entry_range": [round(entry_low, 6), round(entry_high, 6)],
        "stop_loss": round(stop_loss, 6),
        "original_stop_loss": round(stop_loss, 6),
        "breakeven_stop": round(price, 6),
        "targets": all_targets,
        "tp1": all_targets[0],
        "final_tp": all_targets[-1],
        "tp1_hit": False,
        "status": "OPEN",
        "leverage": leverage,
        "rsi": round(row["rsi"], 2),
        "atr": round(atr, 6),
        "opened_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def format_signal(signal):
    return f"""
🚨 HIGH-QUALITY TRADE SIGNAL 🚨

⭕️ COIN: {signal['symbol']}
↔️ SIGNAL TYPE: {signal['direction']}
🔰 LEVERAGE: {signal['leverage']}x cross
👽 EXCHANGE: Binance

♻️ Entry Point:
{signal['entry_range'][1]} - {signal['entry_range'][0]}

🎯 TP1: {signal['tp1']}
🏁 Final TP: {signal['final_tp']}
🚭 Stop loss: {signal['stop_loss']}

📊 RSI: {signal['rsi']}
🧮 ATR: {signal['atr']}
🕒 Generated at: {signal['opened_at']}

⚠️ Not financial advice. Manage your risk.
""".strip()


def send_message_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }

    response = requests.post(url, data=payload, timeout=20)

    if response.status_code == 200:
        print("✅ Message sent to Telegram.")
    else:
        print(f"Telegram error: {response.status_code} - {response.text}")


def send_photo_to_telegram(photo_path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

    with open(photo_path, "rb") as photo:
        files = {"photo": photo}
        data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "caption": caption,
        }

        response = requests.post(url, files=files, data=data, timeout=30)

    if response.status_code == 200:
        print("✅ Result card sent to Telegram.")
    else:
        print(f"Telegram photo error: {response.status_code} - {response.text}")


def calculate_pnl_percent(trade, close_price):
    entry = trade["entry_price"]
    leverage = trade["leverage"]

    if trade["direction"] == "Long":
        raw_percent = ((close_price - entry) / entry) * 100
    else:
        raw_percent = ((entry - close_price) / entry) * 100

    return round(raw_percent * leverage, 2)


def create_result_card(trade, pnl_percent):
    width = 900
    height = 1200

    img = Image.new("RGB", (width, height), color=(8, 15, 32))
    draw = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("arial.ttf", 90)
        font_medium = ImageFont.truetype("arial.ttf", 48)
        font_small = ImageFont.truetype("arial.ttf", 32)
    except Exception:
        font_big = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    symbol_text = trade["symbol"].replace("USDT", "/USDT")
    status = trade["close_reason"]

    pnl_text = f"{pnl_percent:+.2f}%"

    draw.rounded_rectangle((40, 40, width - 40, height - 40), radius=35, fill=(13, 24, 52))

    draw.text((70, 80), symbol_text, fill=(255, 255, 255), font=font_medium)
    draw.text((70, 160), f"{trade['direction'].upper()} • {trade['leverage']}x", fill=(120, 255, 190), font=font_small)

    draw.text((70, 280), pnl_text, fill=(0, 255, 140), font=font_big)
    draw.text((75, 390), "REALIZED PNL", fill=(180, 190, 210), font=font_small)

    draw.line((70, 490, width - 70, 490), fill=(50, 70, 110), width=3)

    rows = [
        ("ENTRY", trade["entry_price"]),
        ("CLOSE", trade["close_price"]),
        ("TP1", trade["tp1"]),
        ("FINAL TP", trade["final_tp"]),
        ("STOP", trade["stop_loss"]),
        ("STATUS", status),
    ]

    y = 560

    for label, value in rows:
        draw.text((80, y), str(label), fill=(140, 150, 175), font=font_small)
        draw.text((420, y), str(value), fill=(255, 255, 255), font=font_small)
        y += 75

    draw.line((70, 1010, width - 70, 1010), fill=(50, 70, 110), width=3)

    draw.text((80, 1060), "BINANCE CRYPTO SIGNALS", fill=(0, 255, 140), font=font_small)
    draw.text((80, 1110), "Not financial advice. Manage your risk.", fill=(150, 160, 180), font=font_small)

    os.makedirs("result_cards", exist_ok=True)

    path = f"result_cards/{trade['id']}.png"
    img.save(path)

    return path


def save_new_open_trade(signal):
    open_trades = load_open_trades()

    for trade in open_trades:
        if trade["symbol"] == signal["symbol"] and trade["status"] == "OPEN":
            print(f"⚠️ Open trade already exists for {signal['symbol']}.")
            return

    open_trades.append(signal)
    save_open_trades(open_trades)


def track_open_trades(client):
    open_trades = load_open_trades()
    closed_trades = load_closed_trades()

    if not open_trades:
        return

    updated_open_trades = []

    for trade in open_trades:
        try:
            current_price = get_current_price(client, trade["symbol"])

            direction = trade["direction"]

            tp1_hit_now = (
                direction == "Long" and current_price >= trade["tp1"]
            ) or (
                direction == "Short" and current_price <= trade["tp1"]
            )

            final_tp_hit = (
                direction == "Long" and current_price >= trade["final_tp"]
            ) or (
                direction == "Short" and current_price <= trade["final_tp"]
            )

            stop_hit = (
                direction == "Long" and current_price <= trade["stop_loss"]
            ) or (
                direction == "Short" and current_price >= trade["stop_loss"]
            )

            if not trade["tp1_hit"] and tp1_hit_now:
                trade["tp1_hit"] = True
                trade["stop_loss"] = trade["breakeven_stop"]

                send_message_to_telegram(
                    f"✅ TP1 HIT for {trade['symbol']}\n"
                    f"Stop loss moved to breakeven: {trade['stop_loss']}"
                )

                updated_open_trades.append(trade)
                continue

            if final_tp_hit:
                trade["status"] = "CLOSED"
                trade["close_price"] = round(current_price, 6)
                trade["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                trade["close_reason"] = "FINAL TP HIT"

                pnl = calculate_pnl_percent(trade, current_price)
                trade["pnl_percent"] = pnl

                closed_trades.append(trade)

                card_path = create_result_card(trade, pnl)
                send_photo_to_telegram(card_path, f"✅ {trade['symbol']} CLOSED • {pnl:+.2f}%")

                continue

            if stop_hit:
                trade["status"] = "CLOSED"
                trade["close_price"] = round(current_price, 6)
                trade["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

                if trade["tp1_hit"]:
                    trade["close_reason"] = "BREAKEVEN AFTER TP1"
                else:
                    trade["close_reason"] = "STOP LOSS HIT"

                pnl = calculate_pnl_percent(trade, current_price)
                trade["pnl_percent"] = pnl

                closed_trades.append(trade)

                card_path = create_result_card(trade, pnl)
                send_photo_to_telegram(card_path, f"📊 {trade['symbol']} CLOSED • {pnl:+.2f}%")

                continue

            updated_open_trades.append(trade)

        except Exception as exc:
            print(f"❌ Failed to track {trade.get('symbol')}: {exc}")
            updated_open_trades.append(trade)

    save_open_trades(updated_open_trades)
    save_closed_trades(closed_trades)


def run_bot(is_manual=False):
    if not BINANCE_API_KEY or not BINANCE_API_SECRET:
        raise SystemExit("Set Binance API credentials in config.py")

    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

    track_open_trades(client)

    state = load_state()

    current_date = datetime.now(timezone.utc).date()
    last_reset_date = datetime.fromisoformat(state["last_reset_date"]).date()

    if current_date != last_reset_date:
        state = {
            "last_reset_date": current_date.isoformat(),
            "signals_sent_today": 0,
            "morning_signal_sent": False,
            "evening_signal_sent": False,
            "sent_symbols_today": [],
        }
        save_state(state)
        print("✅ Daily signal counter reset.")

    if state["signals_sent_today"] >= MAX_SIGNALS_PER_DAY:
        print(f"⚠️ Daily signal limit reached: {MAX_SIGNALS_PER_DAY}")
        return

    current_window = None

    if not is_manual:
        current_window = get_current_window(datetime.now(timezone.utc), state)

        if current_window is None:
            print("⏳ No scheduled signal window is open.")
            return

    print("\n🚀 Running high-quality market scan...\n")

    for symbol in SYMBOLS:
        if state["signals_sent_today"] >= MAX_SIGNALS_PER_DAY:
            break

        if symbol in state["sent_symbols_today"]:
            print(f"⏭️ Skipping {symbol}, already sent today.")
            continue

        try:
            print(f"\n📡 Scanning {symbol}...")

            df = load_klines(client, symbol, INTERVAL, CANDLE_LIMIT)
            df = compute_indicators(df)

            leverage = LEVERAGE_LEVELS[0]
            signal = build_signal(df, symbol, leverage)

            if signal is None:
                continue

            message = format_signal(signal)
            print(message)

            send_message_to_telegram(message)
            save_new_open_trade(signal)

            state["signals_sent_today"] += 1
            state["sent_symbols_today"].append(symbol)

            if not is_manual and current_window:
                state[f"{current_window}_signal_sent"] = True

            save_state(state)

            print(f"✅ Signal #{state['signals_sent_today']} sent today.")
            time.sleep(10)

        except Exception as exc:
            print(f"❌ Failed to analyze {symbol}: {exc}")


if __name__ == "__main__":
    manual_run = MANUAL_OVERRIDE_FLAG in sys.argv[1:]

    if manual_run:
        print("⚠️ Manual override enabled.")

    while True:
        try:
            run_bot(manual_run)
            print("\n😴 Sleeping for 5 minutes...\n")
            time.sleep(300)

        except Exception as exc:
            print(f"❌ Bot crashed: {exc}")
            print("Retrying in 60 seconds...\n")
            time.sleep(60)