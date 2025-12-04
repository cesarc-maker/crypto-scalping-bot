import os
import time
import ccxt
import pandas as pd
from flask import Flask
import threading
import requests

# ======================================================
# CONFIGURATION
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 60              # scan once per minute
PAIR_LIMIT = 40                 # top pairs
EXCHANGES = ["binance"]         # start with Binance


# ======================================================
# TELEGRAM ALERT FUNCTION (synchronous)
# ======================================================

def send_telegram_message(text):
    try:
        url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={CHAT_ID}&text={text}"
        )
        requests.get(url)
    except Exception as e:
        print(f"Error sending message: {e}")


# ======================================================
# SCALPING SYSTEM LOGIC
# ======================================================

def fetch_ohlcv_df(exchange, symbol, timeframe):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
    df = pd.DataFrame(
        data, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def add_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df)
    return df


def compute_rsi(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_atr(df):
    df["H-L"] = df["high"] - df["low"]
    df["H-PC"] = abs(df["high"] - df["close"].shift(1))
    df["L-PC"] = abs(df["low"] - df["close"].shift(1))
    tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
    return tr.rolling(14).mean()


def detect_trend(df):
    if df["ema20"].iloc[-1] > df["ema50"].iloc[-1]:
        return "UP"
    if df["ema20"].iloc[-1] < df["ema50"].iloc[-1]:
        return "DOWN"
    return "NONE"


def check_long_setup(df):
    last = df.iloc[-1]
    return (
        last["close"] > last["ema20"] and
        last["rsi"] < 70 and
        last["close"] - last["ema20"] < last["atr"]
    )


def check_short_setup(df):
    last = df.iloc[-1]
    return (
        last["close"] < last["ema20"] and
        last["rsi"] > 30 and
        last["ema20"] - last["close"] < last["atr"]
    )


def fetch_symbols(exchange):
    try:
        markets = exchange.load_markets()
        usdt_pairs = [s for s in markets if s.endswith("USDT")]
        return usdt_pairs[:PAIR_LIMIT]
    except:
        return []


def send_alert(symbol, direction, price, atr):
    message = (
        f"{direction} signal detected for {symbol}\n"
        f"Price: {price}\nATR: {atr}\n\n"
        f"Timeframe: 5m"
    )
    send_telegram_message(message)
    print(f"Sent alert → {symbol} {direction}")


# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():
    print("Bot scanner started...")

    # Send startup confirmation
    try:
        send_telegram_message("Bot is running successfully 🎉")
    except:
        pass

    while True:
        try:
            for ex_name in EXCHANGES:
                exchange = getattr(ccxt, ex_name)()

                symbols = fetch_symbols(exchange)

                for symbol in symbols:
                    try:
                        df15 = add_indicators(fetch_ohlcv_df(exchange, symbol, "15m"))
                        df5 = add_indicators(fetch_ohlcv_df(exchange, symbol, "5m"))

                        trend = detect_trend(df15)
                        if trend == "NONE":
                            continue

                        last5 = df5.iloc[-1]

                        if trend == "UP" and check_long_setup(df5):
                            send_alert(symbol, "LONG", last5["close"], last5["atr"])

                        if trend == "DOWN" and check_short_setup(df5):
                            send_alert(symbol, "SHORT", last5["close"], last5["atr"])

                    except Exception as inner_err:
                        print(f"Error in symbol loop: {inner_err}")
                        continue

            time.sleep(SCAN_INTERVAL)

        except Exception as outer_err:
            print(f"Main loop error: {outer_err}")
            time.sleep(10)


# ======================================================
# START BACKGROUND THREAD (REQUIRED FOR RENDER)
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# FLASK SERVER FOR RENDER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
