import math
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from binance.client import Client
from ta.trend import EMAIndicator, MACD
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

try:
    from config import (
        BINANCE_API_KEY,
        BINANCE_API_SECRET,
        SYMBOLS,
        FUTURE_TYPE,
        INTERVAL,
        CANDLE_LIMIT,
        RISK_PERCENT,
        LEVERAGE,
        TARGET_MULTIPLIERS,
    )
except ImportError:
    raise SystemExit(
        "Please copy config_example.py to config.py and add your Binance API credentials."
    )


def load_klines(client: Client, symbol: str, interval: str, limit: int) -> pd.DataFrame:
    raw = client.futures_klines(
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

    numeric_cols = ["open", "high", "low", "close", "volume"]

    df[numeric_cols] = df[numeric_cols].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")

    return df


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:

    # EMAs
    df["ema9"] = EMAIndicator(df["close"], window=9).ema_indicator()
    df["ema21"] = EMAIndicator(df["close"], window=21).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

    # RSI
    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()

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


def calculate_confidence(row, direction):

    confidence = 0

    # RSI
    if direction == "Long" and row["rsi"] < 65:
        confidence += 1

    if direction == "Short" and row["rsi"] > 35:
        confidence += 1

    # EMA alignment
    if direction == "Long":
        if row["ema9"] > row["ema21"] > row["ema50"]:
            confidence += 2

    if direction == "Short":
        if row["ema9"] < row["ema21"] < row["ema50"]:
            confidence += 2

    # MACD confirmation
    if direction == "Long" and row["macd"] > row["macd_signal"]:
        confidence += 1

    if direction == "Short" and row["macd"] < row["macd_signal"]:
        confidence += 1

    # Volume spike
    if row["volume"] > row["volume_sma"]:
        confidence += 1

    if confidence >= 5:
        return "HIGH"

    elif confidence >= 3:
        return "MEDIUM"

    return "LOW"


def build_signal(df: pd.DataFrame, symbol: str) -> dict:

    row = df.iloc[-1]

    price = row["close"]

    atr = max(row["atr"], price * 0.0012)

    # Higher timeframe trend filter
    bullish_trend = row["ema50"] > row["ema200"]
    bearish_trend = row["ema50"] < row["ema200"]

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

        entry_low = price - atr * 0.4
        entry_high = price + atr * 0.25

        stop_loss = price - atr * 1.4

    elif short_bias:

        direction = "Short"

        entry_low = price - atr * 0.25
        entry_high = price + atr * 0.4

        stop_loss = price + atr * 1.4

    else:

        direction = "Neutral"

        entry_low = price - atr * 0.3
        entry_high = price + atr * 0.3

        stop_loss = price - atr * 1.6

    entry_low = max(entry_low, price * 0.95)
    entry_high = min(entry_high, price * 1.05)

    # Confidence scoring
    confidence = calculate_confidence(row, direction)

    # Targets
    targets = []

    for style, multipliers in TARGET_MULTIPLIERS.items():

        style_targets = []

        for mult in multipliers:

            if direction == "Long":
                tp = price + atr * mult

            elif direction == "Short":
                tp = price - atr * mult

            else:
                tp = price + atr * mult * 0.5

            style_targets.append(round(tp, 6))

        targets.append((style, style_targets))

    return {
        "symbol": symbol,
        "direction": direction,
        "confidence": confidence,
        "price": round(price, 6),
        "entry_range": (
            round(entry_low, 6),
            round(entry_high, 6)
        ),
        "stop_loss": round(stop_loss, 6),
        "targets": targets,
        "leverage": LEVERAGE,
        "rsi": round(row["rsi"], 2),
        "ema9": round(row["ema9"], 6),
        "ema21": round(row["ema21"], 6),
        "ema50": round(row["ema50"], 6),
        "ema200": round(row["ema200"], 6),
        "atr": round(atr, 6),
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def format_signal(signal: dict) -> str:

    lines = []

    lines.append(f"⭕️ COIN: {signal['symbol']}")
    lines.append(f"↔️ SIGNAL TYPE: {signal['direction']}")
    lines.append(f"🔥 CONFIDENCE: {signal['confidence']}")
    lines.append(f"🔰 LEVERAGE: {signal['leverage']}x cross")
    lines.append("👽 EXCHANGE: Binance Futures")

    lines.append("")
    lines.append("♻️ Entry Point:")
    lines.append(
        f"{signal['entry_range'][1]} - {signal['entry_range'][0]}"
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

    lines.append(f"🚭 Stop loss: {signal['stop_loss']}")

    lines.append("")
    lines.append(f"🎯 Generated at: {signal['time']}")

    lines.append(
        f"📊 RSI: {signal['rsi']} | "
        f"EMA9: {signal['ema9']} | "
        f"EMA21: {signal['ema21']} | "
        f"EMA50: {signal['ema50']} | "
        f"EMA200: {signal['ema200']}"
    )

    lines.append(f"💲 Current Price: {signal['price']}")
    lines.append(f"🧮 ATR: {signal['atr']}")

    return "\n".join(lines)


def main():

    api_key = BINANCE_API_KEY
    api_secret = BINANCE_API_SECRET

    if (
        not api_key
        or not api_secret
        or "YOUR_BINANCE" in api_key
    ):
        raise SystemExit(
            "Set your Binance API credentials in config.py before running."
        )

    client = Client(api_key, api_secret)

    client.FUTURES_URL = "https://fapi.binance.com/fapi"

    print("Starting Binance trading signal analysis...\n")

    for symbol in SYMBOLS:

        try:

            df = load_klines(
                client,
                symbol,
                INTERVAL,
                CANDLE_LIMIT
            )

            df = compute_indicators(df)

            signal = build_signal(df, symbol)

            print(format_signal(signal))

            print("\n" + "-" * 76 + "\n")

        except Exception as exc:

            print(f"Failed to analyze {symbol}: {exc}\n")


if __name__ == "__main__":
    main()