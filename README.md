# Binance Trading Signal Bot

A starter trading analysis bot for Binance that generates formatted signal summaries similar to the example you provided.

## What it does
- Reads market data for Binance Futures symbols
- Computes basic signals using EMA, RSI, MACD, ATR
- Builds entry ranges, take profit targets, and stop loss levels
- Prints results in a signal-report style

## Files
- `trade_signal_bot.py` - main analysis bot
- `config_example.py` - configuration template
- `requirements.txt` - Python dependencies

## Setup
1. Copy `config_example.py` to `config.py`
2. Add your Binance API key and secret
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the bot:
   ```bash
   python trade_signal_bot.py
   ```

## Usage
You can customize the target symbol list and interval in `config.py`.

## Important
- This is an analysis/demo bot only.
- Do not trade live without reviewing the logic and applying proper risk controls.
- Always test with a paper account first.
# tradingbot
