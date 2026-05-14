@echo off
cd /d "C:\Users\USER\OneDrive\Desktop\Futurestradingbot"
python trade_signal_bot.py
echo Bot execution completed at %date% %time% >> bot_log.txt