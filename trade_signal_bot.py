# =========================
# DAILY SIGNAL LIMIT SYSTEM
# =========================

signals_sent_today = 0
last_reset_date = datetime.now(timezone.utc).date()


if __name__ == "__main__":

    while True:

        try:

            # Reset counter every new UTC day
            current_date = datetime.now(timezone.utc).date()

            if current_date != last_reset_date:
                signals_sent_today = 0
                last_reset_date = current_date
                print("✅ Daily signal counter reset.")

            # Stop if already sent 2 signals today
            if signals_sent_today >= 2:
                print("⚠️ Daily limit reached (2 signals sent today).")
                print("Sleeping for 1 hour...\n")
                time.sleep(3600)
                continue

            print("🚀 Running market scan...\n")

            api_key = BINANCE_API_KEY
            api_secret = BINANCE_API_SECRET

            client = Client(api_key, api_secret)
            client.FUTURES_URL = "https://fapi.binance.com/fapi"

            found_signal = False

            for symbol in SYMBOLS:

                try:

                    df = load_klines(
                        client,
                        symbol,
                        INTERVAL,
                        CANDLE_LIMIT
                    )

                    df = compute_indicators(df)

                    for leverage in LEVERAGE_LEVELS:

                        # Stop immediately if limit reached
                        if signals_sent_today >= 2:
                            break

                        signal = build_signal(df, symbol, leverage)

                        if signal is None:
                            continue

                        formatted_signal = format_signal(signal)

                        print(formatted_signal)

                        send_to_telegram(formatted_signal)

                        signals_sent_today += 1
                        found_signal = True

                        print(
                            f"\n✅ Signal #{signals_sent_today} sent today."
                        )

                        print("\n" + "-" * 76 + "\n")

                        # Sleep 10 seconds between signals
                        time.sleep(10)

                    if signals_sent_today >= 2:
                        break

                except Exception as exc:
                    print(f"❌ Failed to analyze {symbol}: {exc}\n")

            if not found_signal:
                print("⚪️ No valid signals found this cycle.\n")

            print("😴 Sleeping for 5 minutes...\n")

            time.sleep(300)

        except Exception as e:

            print(f"❌ Bot crashed: {e}")

            print("Retrying in 60 seconds...\n")

            time.sleep(60)