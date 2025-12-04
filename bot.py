import os
import time
import ccxt
import pandas as pd
from flask import Flask, request
import threading
import requests


# ======================================================
# CONFIGURATION
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 60
PAIR_LIMIT = 50

# Mode 2 – Moderate signals across top USDT sources
EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]


# ======================================================
# TELEGRAM MESSAGE SENDER
# ======================================================

def send_telegram_message(text, chat_id=None):
    if chat_id is None:
        chat_id = CHAT_ID
    try:
        url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={chat_id}&text={text}"
        )
        requests.get(url)
    except Exception as e:
        print(f"Telegram send error: {e}")


# ======================================================
# OHLCV + INDICATORS
# ======================================================

def fetch_ohlcv_df(exchange, symbol, timeframe):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=120)
    df = pd.DataFrame(
        data,
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def compute_rsi(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def compute_atr(df):
    df["H-L"] = df["high"] - df["low"]
    df["H-PC"] = abs(df["high"] - df["close"].shift(1))
    df["L-PC"] = abs(df["low"] - df["close"].shift(1))
    tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
    return tr.rolling(14).mean()


def add_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df)
    return df


# ======================================================
# TREND & SIGNAL LOGIC (MODE 2)
# ======================================================

def detect_trend(df15):
    last = df15.iloc[-1]
    if last["ema20"] > last["ema50"]:
        return "UP"
    if last["ema20"] < last["ema50"]:
        return "DOWN"
    return "NONE"


def check_long_setup(df5):
    last = df5.iloc[-1]
    ema_pullback = (
        last["close"] > last["ema20"] or
        last["close"] > last["ema50"]
    )
    rsi_ok = last["rsi"] < 80
    return ema_pullback and rsi_ok


def check_short_setup(df5):
    last = df5.iloc[-1]
    ema_pullback = (
        last["close"] < last["ema20"] or
        last["close"] < last["ema50"]
    )
    rsi_ok = last["rsi"] > 20
    return ema_pullback and rsi_ok


# ======================================================
# MULTI-EXCHANGE USDT PAIR FETCHING
# ======================================================

def get_exchange(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options": {"defaultType": "future"}})
        if name == "bybit":
            return ccxt.bybit({"options": {"defaultType": "linear"}})
        return getattr(ccxt, name)()
    except Exception as e:
        print(f"Exchange load error: {e}")
        return None


def fetch_usdt_pairs(exchange):
    try:
        markets = exchange.load_markets()
        return [s for s in markets if isinstance(s, str) and s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []


# ======================================================
# TP / SL + SIGNAL SENDER
# ======================================================

def send_signal(symbol, direction, price, atr):
    atr = float(atr)

    if direction == "LONG":
        sl = price - (1.5 * atr)
        tp1 = price + (1 * atr)
        tp2 = price + (2 * atr)
        tp3 = price + (3 * atr)
    else:
        sl = price + (1.5 * atr)
        tp1 = price - (1 * atr)
        tp2 = price - (2 * atr)
        tp3 = price - (3 * atr)

    message = (
        f"🔥 {direction} Signal Detected\n\n"
        f"Pair: {symbol}\n"
        f"Price: {price}\n"
        f"ATR: {round(atr, 4)}\n"
        f"Timeframe: 5m\n\n"
        f"📍 Stop Loss: {round(sl, 4)}\n\n"
        f"🎯 Take Profits:\n"
        f"• TP1: {round(tp1, 4)}\n"
        f"• TP2: {round(tp2, 4)}\n"
        f"• TP3: {round(tp3, 4)}\n\n"
        f"⚠️ For informational purposes only."
    )

    send_telegram_message(message)
    print(f"Sent alert → {symbol} {direction}")


# ======================================================
# SCANNER LOOP
# ======================================================

def scanner_loop():
    print("Bot scanner started...")
    send_telegram_message("Bot is running successfully 🎉")

    while True:
        try:
            for ex_name in EXCHANGES:

                ex = get_exchange(ex_name)
                if ex is None:
                    continue

                symbols = fetch_usdt_pairs(ex)

                for symbol in symbols:
                    try:
                        df15 = add_indicators(fetch_ohlcv_df(ex, symbol, "15m"))
                        df5 = add_indicators(fetch_ohlcv_df(ex, symbol, "5m"))

                        trend = detect_trend(df15)
                        if trend == "NONE":
                            continue

                        last5 = df5.iloc[-1]

                        if trend == "UP" and check_long_setup(df5):
                            send_signal(symbol, "LONG", last5["close"], last5["atr"])

                        if trend == "DOWN" and check_short_setup(df5):
                            send_signal(symbol, "SHORT", last5["close"], last5["atr"])

                    except Exception as e:
                        print(f"Symbol error ({symbol}): {e}")
                        continue

            time.sleep(SCAN_INTERVAL)

        except Exception as err:
            print(f"Scanner error: {err}")
            time.sleep(10)


# ======================================================
# WEBHOOK COMMAND HANDLER
# ======================================================

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    if not data:
        return "OK"

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if text == "/start":
            send_telegram_message("Bot is active and running. Use /status to check scan health.", chat_id)

        if text == "/status":
            send_telegram_message("📡 Bot is online and scanning markets in real-time.", chat_id)

        if text == "/help":
            send_telegram_message(
                "Commands:\n"
                "/start - Activate bot\n"
                "/status - Check scan status\n"
                "/help - Show commands",
                chat_id
            )

    return "OK"


# ======================================================
# START SCANNER THREAD
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# FLASK ROOT ENDPOINT
# ======================================================

@app.route("/")
def home():
    return "Bot is running with Mode 2 + Webhooks."


# ======================================================
# RUN FLASK SERVER (RENDER)
# ======================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
