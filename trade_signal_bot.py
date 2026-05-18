import time
import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
from binance.client import Client
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

try:
    from config import (
        BINANCE_API_KEY,
        BINANCE_API_SECRET,
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID,
        SYMBOLS,
        FUTURE_TYPE,
        INTERVAL,
        CANDLE_LIMIT,
        RISK_PERCENT,
        LEVERAGE_LEVELS,
        TARGET_MULTIPLIERS,
    )
except ImportError:
    raise SystemExit(
        "Please copy config_example.py to config.py and add your Binance API credentials."
    )


# =========================
# DAILY SIGNAL LIMIT SYSTEM
# =========================

signals_sent_today = 0
last_reset_date = datetime.now(timezone.utc).date()


def load_klines(
    client: Client,
    symbol: str,
    interval: str,
    limit: int
) -> pd.DataFrame:

    # Using SPOT endpoint
    raw = client.get_klines(
        symbol=symbol,
        interval=interval,
        limit=limit
    )

    df = pd.DataFrame(raw, columns=[
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_asset_volume",
        "num_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
        "ignore",
    ])

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    df[numeric_cols] = df[numeric_cols].astype(float)

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms"
    )

    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:

    # EMAs
    df["ema9"] = EMAIndicator(
        df["close"],
        window=9
    ).ema_indicator()

    df["ema21"] = EMAIndicator(
        df["close"],
        window=21
    ).ema_indicator()

    df["ema50"] = EMAIndicator(
        df["close"],
        window=50
    ).ema_indicator()

    df["ema200"] = EMAIndicator(
        df["close"],
        window=200
    ).ema_indicator()

    # RSI
    df["rsi"] = RSIIndicator(
        df["close"],
        window=14
    ).rsi()

    # MACD
    macd = MACD(
        df["close"],
        window_slow=26,
        window_fast=12,
        window_sign=9
    )

    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    # ATR
    df["atr"] = AverageTrueRange(
        df["high"],
        df["low"],
        df["close"],
        window=14
    ).average_true_range()

    # Volume SMA
    df["volume_sma"] = df["volume"].rolling(20).mean()

    return df


def build_signal(
    df: pd.DataFrame,
    symbol: str,
    leverage: int
) -> dict:

    row = df.iloc[-1]

    price = row["close"]

    print(f"📊 Analysis for {symbol}:")
    print(f"   Current Price: {price:.6f}")
    print(f"   RSI(14): {row['rsi']:.2f}")
    print(f"   EMA9: {row['ema9']:.6f}")
    print(f"   EMA21: {row['ema21']:.6f}")
    print(f"   EMA50: {row['ema50']:.6f}")
    print(f"   EMA200: {row['ema200']:.6f}")
    print(
        f"   MACD: {row['macd']:.6f} | "
        f"Signal: {row['macd_signal']:.6f}"
    )
    print(
        f"   Volume: {row['volume']:.2f} | "
        f"SMA(20): {row['volume_sma']:.2f}"
    )

    atr = max(row["atr"], price * 0.0012)

    print(f"   ATR(14): {atr:.6f}")

    bullish_trend = row["ema50"] > row["ema200"]
    bearish_trend = row["ema50"] < row["ema200"]

    print("\n   📈 Trend Analysis:")
    print(f"      Bullish: {bullish_trend}")
    print(f"      Bearish: {bearish_trend}")

    long_bias = (
        bullish_trend
        and row["ema9"] > row["ema21"] > row["ema50"]
        and row["rsi"] < 75
        and row["macd"] > row["macd_signal"]
    )

    short_bias = (
        bearish_trend
        and row["ema9"] < row["ema21"] < row["ema50"]
        and row["rsi"] > 25
        and row["macd"] < row["macd_signal"]
    )

    if long_bias:

        direction = "Long"

        print("\n✅ LONG Signal Generated")

        entry_low = price - atr * 0.4
        entry_high = price + atr * 0.25

        stop_loss = price - atr * 1.4

    elif short_bias:

        direction = "Short"

        print("\n✅ SHORT Signal Generated")

        entry_low = price - atr * 0.25
        entry_high = price + atr * 0.4

        stop_loss = price + atr * 1.4

    else:

        print("\n⚪️ No clear signal")

        return None

    entry_low = max(entry_low, price * 0.95)
    entry_high = min(entry_high, price * 1.05)

    targets = []

    for style, multipliers in TARGET_MULTIPLIERS.items():

        style_targets = []

        for mult in multipliers:

            if direction == "Long":
                tp = price + atr * mult
            else:
                tp = price - atr * mult

            style_targets.append(round(tp, 6))

        targets.append((style, style_targets))

    return {
        "symbol": symbol,
        "direction": direction,
        "price": round(price, 6),
        "entry_range": (
            round(entry_low, 6),
            round(entry_high, 6)
        ),
        "stop_loss": round(stop_loss, 6),
        "targets": targets,
        "leverage": leverage,
        "rsi": round(row["rsi"], 2),
        "ema9": round(row["ema9"], 6),
        "ema21": round(row["ema21"], 6),
        "ema50": round(row["ema50"], 6),
        "ema200": round(row["ema200"], 6),
        "atr": round(atr, 6),
        "time": datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def format_signal(signal: dict) -> str:

    lines = []

    lines.append(f"⭕️ COIN: {signal['symbol']}")
    lines.append(f"↔️ SIGNAL TYPE: {signal['direction']}")
    lines.append(
        f"🔰 LEVERAGE: {signal['leverage']}x cross"
    )

    lines.append("👽 EXCHANGE: Binance Spot")

    lines.append("")
    lines.append("♻️ Entry Point:")

    lines.append(
        f"{signal['entry_range'][1]} - "
        f"{signal['entry_range'][0]}"
    )

    lines.append("")
    lines.append("📈 Take profit targets")

    for style, targets in signal["targets"]:

        if style == "scalping":
            lines.append("🔰 SCALPING 🔰")

        elif style == "day":
            lines.append("☀️ DAY TRADING")

        else:
            lines.append("🌗 SWING TRADING")

        for target in targets:
            lines.append(f"⛳️ {target}")

        lines.append("")

    lines.append(
        f"🚭 Stop loss: {signal['stop_loss']}"
    )

    lines.append("")
    lines.append(
        f"🎯 Generated at: {signal['time']}"
    )

    lines.append(
        f"📊 RSI: {signal['rsi']} | "
        f"EMA9: {signal['ema9']} | "
        f"EMA21: {signal['ema21']} | "
        f"EMA50: {signal['ema50']} | "
        f"EMA200: {signal['ema200']}"
    )

    lines.append(
        f"💲 Current Price: {signal['price']}"
    )

    lines.append(f"🧮 ATR: {signal['atr']}")

    return "\n".join(lines)


def send_to_telegram(message: str):

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
    }

    try:

        response = requests.post(
            url,
            data=payload
        )

        if response.status_code == 200:
            print(
                "✅ Signal sent to Telegram successfully."
            )

        else:
            print(
                f"Telegram error: "
                f"{response.status_code} - "
                f"{response.text}"
            )

    except Exception as exc:

        print(f"Telegram send failed: {exc}")


def run_bot():

    global signals_sent_today
    global last_reset_date

    current_date = datetime.now(
        timezone.utc
    ).date()

    # Reset every new day
    if current_date != last_reset_date:

        signals_sent_today = 0
        last_reset_date = current_date

        print("✅ Daily signal counter reset.")

    # Stop after 2 signals
    if signals_sent_today >= 2:

        print(
            "⚠️ Daily limit reached "
            "(2 signals sent today)."
        )

        return

    api_key = BINANCE_API_KEY
    api_secret = BINANCE_API_SECRET

    if (
        not api_key
        or not api_secret
        or "YOUR_BINANCE" in api_key
    ):
        raise SystemExit(
            "Set Binance API credentials in config.py"
        )

    client = Client(api_key, api_secret)

    print("\n🚀 Running market scan...\n")

    found_signal = False

    for symbol in SYMBOLS:

        if signals_sent_today >= 2:
            break

        try:

            print(f"\n📡 Scanning {symbol}...\n")

            df = load_klines(
                client,
                symbol,
                INTERVAL,
                CANDLE_LIMIT
            )

            df = compute_indicators(df)

            for leverage in LEVERAGE_LEVELS:

                if signals_sent_today >= 2:
                    break

                signal = build_signal(
                    df,
                    symbol,
                    leverage
                )

                if signal is None:
                    continue

                formatted_signal = format_signal(
                    signal
                )

                print(formatted_signal)

                send_to_telegram(
                    formatted_signal
                )

                signals_sent_today += 1

                found_signal = True

                print(
                    f"\n✅ Signal "
                    f"#{signals_sent_today} "
                    f"sent today."
                )

                print(
                    "\n" + "-" * 76 + "\n"
                )

                time.sleep(10)

        except Exception as exc:

            print(
                f"❌ Failed to analyze "
                f"{symbol}: {exc}\n"
            )

    if not found_signal:

        print(
            "⚪️ No valid signals "
            "found this cycle."
        )


if __name__ == "__main__":

    while True:

        try:

            run_bot()

            print(
                "\n😴 Sleeping for "
                "5 minutes...\n"
            )

            time.sleep(300)

        except Exception as e:

            print(f"❌ Bot crashed: {e}")

            print(
                "Retrying in 60 seconds...\n"
            )

            time.sleep(60)