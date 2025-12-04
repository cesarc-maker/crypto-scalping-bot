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
# TREND LOGIC
# ======================================================

def detect_trend(df15):
    last = df15.iloc[-1]
    if last["ema20"] > last["ema50"]:
        return "UP"
    if last["ema20"] < last["ema50"]:
        return "DOWN"
    return "NONE"


# ======================================================
# LESS AGGRESSIVE SIGNAL LOGIC (1:2 RR Compatible)
# ======================================================

def check_long_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]

    ema_trend = last["ema20"] > prev["ema20"]
    ema_position = last["close"] > last["ema20"]
    rsi_ok = 40 < last["rsi"] < 70
    bullish_candle = last["close"] > last["open"]
    atr_ok = last["atr"] > 0

    return ema_trend and ema_position and rsi_ok and bullish_candle and atr_ok


def check_short_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]

    ema_trend = last["ema20"] < prev["ema20"]
    ema_position = last["close"] < last["ema20"]
    rsi_ok = 30 < last["rsi"] < 60
    bearish_candle = last["close"] < last["open"]
    atr_ok = last["atr"] > 0

    return ema_trend and ema_position and rsi_ok and bearish_candle and atr_ok


# ======================================================
# MULTI-EXCHANGE USDT PAIRS
# ======================================================

def get_exchange(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options": {"defaultType": "future"}})
        if name == "bybit":
            return ccxt.bybit({"options": {"defaultType": "linear"}})
        return getattr(ccxt, name)()
    except:
        return None


def fetch_usdt_pairs(exchange):
    try:
        markets = exchange.load_markets()
        return [s for s in markets if s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []


# ======================================================
# SEND SIGNAL (WITH 1:2 RR TP/SL)
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
        f"• TP2: {round(tp2, 4)} (1:2 RR)\n"
        f"• TP3: {round(tp3, 4)}\n\n"
        f"⚠️ Informational only."
    )

    send_telegram_message(message)
    print(f"Sent alert → {symbol} {direction}")


# ======================================================
# MARKET SCANNER
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
# WEBHOOK TELEGRAM COMMANDS
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
            send_telegram_message("Bot active. Use /status to check health.", chat_id)

        if text == "/status":
            send_telegram_message("📡 Bot online. Scanning markets live.", chat_id)

        if text == "/help":
            send_telegram_message(
                "Commands:\n"
                "/start - Activate bot\n"
                "/status - Check bot health\n"
                "/help - Show commands",
                chat_id
            )

    return "OK"


# ======================================================
# THREAD STARTER
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# FLASK ROOT
# ======================================================

@app.route("/")
def home():
    return "Bot is running (Mode 2 – Less Aggressive – 1:2 RR)."


# ======================================================
# RUN SERVER FOR RENDER
# ======================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
