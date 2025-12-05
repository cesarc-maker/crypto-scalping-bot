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

SCAN_INTERVAL = 60     # scans every minute
PAIR_LIMIT = 50        # scans top 50 USDT pairs per exchange

EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]


# ======================================================
# TELEGRAM ALERTING
# ======================================================

def send_telegram_message(text, chat_id=None):
    if chat_id is None:
        chat_id = CHAT_ID
    try:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={chat_id}&text={text}"
        )
    except:
        pass


# ======================================================
# MARKET DATA + INDICATORS
# ======================================================

def fetch_ohlcv_df(exchange, symbol, timeframe):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=120)
    df = pd.DataFrame(
        data,
        columns=["timestamp","open","high","low","close","volume"]
    )
    return add_indicators(df)


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
    tr = df[["H-L","H-PC","L-PC"]].max(axis=1)
    return tr.rolling(14).mean()


def add_indicators(df):
    df.columns = ["timestamp","open","high","low","close","volume"]
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df)
    return df


# ======================================================
# TREND FILTER
# ======================================================

def trend_direction(df):
    last = df.iloc[-1]
    if last["ema20"] > last["ema50"]:
        return "UP"
    if last["ema20"] < last["ema50"]:
        return "DOWN"
    return "NONE"


# ======================================================
# STRICTER CLEAN TREND SCALPING — LONG LOGIC
# ======================================================

def check_long_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]

    # 1️⃣ Trend must be bullish
    if last["ema20"] <= last["ema50"]:
        return False

    # 2️⃣ RSI slightly stricter
    if not (48 < last["rsi"] < 63):
        return False

    # 3️⃣ Candle body strength ≥ 45% of range
    body = last["close"] - last["open"]
    range_ = last["high"] - last["low"]
    if not (body > 0 and body >= 0.45 * range_):
        return False

    # 4️⃣ Must close above previous candle (momentum continuation)
    if last["close"] <= prev["close"]:
        return False

    return True


# ======================================================
# STRICTER CLEAN TREND SCALPING — SHORT LOGIC
# ======================================================

def check_short_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]

    # 1️⃣ Trend must be bearish
    if last["ema20"] >= last["ema50"]:
        return False

    # 2️⃣ RSI slightly stricter
    if not (37 < last["rsi"] < 52):
        return False

    # 3️⃣ Candle body strength ≥ 45% of range
    body = last["open"] - last["close"]
    range_ = last["high"] - last["low"]
    if not (body > 0 and body >= 0.45 * range_):
        return False

    # 4️⃣ Must close below previous close
    if last["close"] >= prev["close"]:
        return False

    return True


# ======================================================
# EXCHANGE HELPERS
# ======================================================

def get_exchange(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options":{"defaultType":"future"}})
        if name == "bybit":
            return ccxt.bybit({"options":{"defaultType":"linear"}})
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
# SIGNAL SENDER
# ======================================================

def send_signal(symbol, direction, price, atr):

    atr = float(atr)

    if direction == "LONG":
        sl = price - (1.5 * atr)
        tp1 = price + atr
        tp2 = price + (2 * atr)
        tp3 = price + (3 * atr)
    else:
        sl = price + (1.5 * atr)
        tp1 = price - atr
        tp2 = price - (2 * atr)
        tp3 = price - (3 * atr)

    msg = (
        f"🔥 {direction} SIGNAL\n\n"
        f"Pair: {symbol}\n"
        f"Price: {price}\n"
        f"ATR: {round(atr,4)}\n\n"
        f"SL: {round(sl,4)}\n"
        f"TP1: {round(tp1,4)}\n"
        f"TP2: {round(tp2,4)} (1:2 RR)\n"
        f"TP3: {round(tp3,4)}\n"
        f"⚠ informational only."
    )

    send_telegram_message(msg)
    print("SIGNAL →", symbol, direction)


# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():
    send_telegram_message("🚀 Bot Active (Stricter Clean Trend Scalping Mode)")

    while True:
        try:
            for ex_name in EXCHANGES:

                ex = get_exchange(ex_name)
                if ex is None:
                    continue

                symbols = fetch_usdt_pairs(ex)

                for symbol in symbols:

                    try:
                        df5 = fetch_ohlcv_df(ex, symbol, "5m")
                        trend = trend_direction(df5)
                        last = df5.iloc[-1]

                        # Long signals
                        if trend == "UP" and check_long_setup(df5):
                            send_signal(symbol, "LONG", last["close"], last["atr"])

                        # Short signals
                        if trend == "DOWN" and check_short_setup(df5):
                            send_signal(symbol, "SHORT", last["close"], last["atr"])

                    except Exception as err:
                        print("Error on", symbol, ":", err)

            time.sleep(SCAN_INTERVAL)

        except Exception as err:
            print("Scanner error:", err)
            time.sleep(10)


# ======================================================
# TELEGRAM WEBHOOKS
# ======================================================

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return "OK"

    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text","")

    if text == "/start":
        send_telegram_message("Bot Online — Stricter Trend Scalping Enabled", chat_id)

    elif text == "/status":
        send_telegram_message("📡 Bot Running & Scanning...", chat_id)

    elif text == "/help":
        send_telegram_message(
            "/start — Activate Bot\n"
            "/status — Bot Health\n"
            "/help — Commands",
            chat_id
        )

    return "OK"


# ======================================================
# START THREAD
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# FLASK SERVER FOR RENDER
# ======================================================

@app.route("/")
def home():
    return "Bot Running (Stricter Clean Trend Scalping Mode)"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
