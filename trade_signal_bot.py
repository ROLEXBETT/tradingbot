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
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    return df


def interval_to_minutes(interval: str) -> int:
    if interval.endswith("m"):
        return int(interval[:-1])
    if interval.endswith("h"):
        return int(interval[:-1]) * 60
    if interval.endswith("d"):
        return int(interval[:-1]) * 60 * 24
    return 60


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

    latest_close = df["close_time"].iloc[-1]
    now = datetime.now(timezone.utc)
    interval_minutes = interval_to_minutes(INTERVAL)

    if now > latest_close + pd.Timedelta(minutes=interval_minutes + 8):
        print(
            f"⚠️ Stale signal data for {symbol}: latest candle closed at "
            f"{latest_close.isoformat()}, skipping."
        )
        return None

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
🎯 NEW SIGNAL ALERT! ✅

{signal['symbol']} {signal['direction']}
🔰 Leverage: {signal['leverage']}x

🎯 TP1: {signal['tp1']}
🏁 Final TP: {signal['final_tp']}
🚭 Stop loss: {signal['stop_loss']}

📊 RSI: {signal['rsi']}
🧮 ATR: {signal['atr']}
⏰ {signal['opened_at']}

⚠️ Not financial advice. Manage your risk.
""".strip()


def format_trade_update(trade, current_price, event_label, pnl_percent, hit_count=None):
    total_tps = len(trade.get("targets", []))
    hit_count = hit_count if hit_count is not None else (
        total_tps if event_label == "FINAL TP" else 1
    )
    now = datetime.now(timezone.utc)
    timestamp = f"{now.month}/{now.day}/{now.year}, {now.strftime('%I:%M:%S %p').lstrip('0')}"

    title = f"🎯 {event_label} HIT! ✅"
    if event_label == "STOP LOSS":
        title = "🔴 STOP LOSS HIT"

    return f"""
{title}

{trade['symbol']} {trade['direction']}
🎯 {event_label}: {current_price}
💲 Current: {current_price}
📊 P&L: {pnl_percent:+.2f}%
⚡ Leverage: {trade['leverage']}x
✅ {hit_count}/{total_tps} TPs reached
⏰ {timestamp}
📺 Binance Futures SIGNALS
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


def create_result_card(trade, pnl_percent, current_price, event_label="CLOSED"):
    width = 1000
    height = 1200

    img = Image.new("RGB", (width, height), color=(12, 16, 38))
    draw = ImageDraw.Draw(img)

    try:
        font_big = ImageFont.truetype("arial.ttf", 90)
        font_medium = ImageFont.truetype("arial.ttf", 56)
        font_small = ImageFont.truetype("arial.ttf", 38)
        font_xsmall = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font_big = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_xsmall = ImageFont.load_default()

    symbol_text = trade["symbol"].replace("USDT", "/USDT")
    status = trade.get("close_reason", event_label)
    pnl_text = f"{pnl_percent:+.2f}%"
    status_color = (0, 255, 140) if pnl_percent >= 0 else (255, 90, 90)
    event_title = f"{event_label} HIt!" if event_label not in ["STOP LOSS", "BREAKEVEN"] else event_label

    draw.rectangle((0, 0, width, height), fill=(8, 12, 32))
    draw.rectangle((40, 40, width - 40, 240), fill=(15, 25, 70))
    draw.text((70, 60), "Binance Futures SIGNALS", fill=(150, 190, 255), font=font_small)
    draw.text((70, 120), symbol_text, fill=(255, 255, 255), font=font_big)
    draw.text((70, 210), f"{trade['direction'].upper()} • {trade['leverage']}x", fill=(120, 255, 190), font=font_medium)

    draw.rectangle((40, 270, width - 40, 560), fill=(17, 26, 90), radius=30)
    draw.text((70, 300), pnl_text, fill=status_color, font=font_big)
    draw.text((70, 420), "REALIZED PNL" if event_label in ["FINAL TP", "TP1"] else "P&L", fill=(204, 214, 230), font=font_small)
    draw.text((70, 470), f"{event_label} • {current_price}", fill=(190, 210, 255), font=font_small)

    draw.line((70, 590, width - 70, 590), fill=(50, 70, 110), width=3)

    rows = [
        ("ENTRY", trade["entry_price"]),
        ("CURRENT", current_price),
        ("TP1", trade["tp1"]),
        ("FINAL TP", trade["final_tp"]),
        ("STOP", trade["stop_loss"]),
        ("STATUS", status),
    ]

    y = 620

    for label, value in rows:
        draw.text((70, y), str(label), fill=(160, 170, 195), font=font_small)
        draw.text((360, y), str(value), fill=(255, 255, 255), font=font_small)
        y += 70

    draw.line((70, 1040, width - 70, 1040), fill=(50, 70, 110), width=3)
    draw.text((70, 1060), "Accurate signals • Smart trading • Max profits", fill=(120, 170, 230), font=font_xsmall)

    os.makedirs("result_cards", exist_ok=True)
    sanitized_label = event_label.replace(" ", "_").replace("/", "_")
    path = f"result_cards/{trade['id']}_{sanitized_label}.png"
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
                pnl = calculate_pnl_percent(trade, current_price)
                message = format_trade_update(
                    trade,
                    current_price,
                    "TP1",
                    pnl,
                    hit_count=1,
                )
                print(message)
                send_message_to_telegram(message)

                card_path = create_result_card(
                    trade,
                    pnl,
                    current_price,
                    event_label="TP1"
                )
                send_photo_to_telegram(card_path, f"✅ TP1 HIT • {trade['symbol']} • {pnl:+.2f}%")

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

                message = format_trade_update(
                    trade,
                    current_price,
                    "FINAL TP",
                    pnl,
                    hit_count=len(trade.get("targets", [])),
                )
                print(message)
                send_message_to_telegram(message)

                card_path = create_result_card(
                    trade,
                    pnl,
                    current_price,
                    event_label="FINAL TP"
                )
                send_photo_to_telegram(card_path, f"✅ {trade['symbol']} FINAL TP • {pnl:+.2f}%")

                continue

            if stop_hit:
                trade["status"] = "CLOSED"
                trade["close_price"] = round(current_price, 6)
                trade["closed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

                if trade["tp1_hit"]:
                    trade["close_reason"] = "BREAKEVEN AFTER TP1"
                    event_label = "BREAKEVEN"
                    hit_count = 1
                else:
                    trade["close_reason"] = "STOP LOSS HIT"
                    event_label = "STOP LOSS"
                    hit_count = 0

                pnl = calculate_pnl_percent(trade, current_price)
                trade["pnl_percent"] = pnl

                closed_trades.append(trade)

                message = format_trade_update(
                    trade,
                    current_price,
                    event_label,
                    pnl,
                    hit_count=hit_count,
                )
                print(message)
                send_message_to_telegram(message)

                card_path = create_result_card(
                    trade,
                    pnl,
                    current_price,
                    event_label=event_label
                )
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