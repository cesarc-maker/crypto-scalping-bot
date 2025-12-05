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

SCAN_INTERVAL = 60      # Scan every 60 seconds
PAIR_LIMIT = 50         # Top 50 USDT pairs per exchange

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
# DATA + INDICATORS
# ======================================================

def fetch_ohlcv_df(exchange, symbol, timeframe):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=150)
    df = pd.DataFrame(data, columns=["timestamp","open","high","low","close","volume"])
    return add_indicators(df)


def compute_rsi(series, length=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def compute_atr(df):
    df["H-L"]  = df["high"] - df["low"]
    df["H-PC"] = abs(df["high"] - df["close"].shift(1))
    df["L-PC"] = abs(df["low"] - df["close"].shift(1))
    tr = df[["H-L","H-PC","L-PC"]].max(axis=1)
    return tr.rolling(14).mean()


def add_indicators(df):
    df.columns = ["timestamp","open","high","low","close","volume"]
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"]   = compute_rsi(df["close"], 14)
    df["atr"]   = compute_atr(df)
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
# MAX-PROFIT LONG SETUP
# ======================================================

def check_long_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]

    # 1️⃣ Strong bullish trend: EMA separation
    if not (last["ema20"] > last["ema50"] * 1.0025):   # 0.25% separation
        return False

    # 2️⃣ RSI expansion momentum
    if not (52 < last["rsi"] < 68):
        return False

    # 3️⃣ Power candle (60% body)
    body  = last["close"] - last["open"]
    range_ = last["high"] - last["low"]
    if not (body > 0 and body >= 0.60 * range_):
        return False

    # 4️⃣ Micro-breakout (break previous 2 highs)
    if not (last["close"] > max(prev["high"], df5.iloc[-3]["high"])):
        return False

    # 5️⃣ ATR explosion (10% increase)
    if not (last["atr"] >= prev["atr"] * 1.10):
        return False

    # 6️⃣ Expected big move (≥1% move potential)
    if not (last["atr"] * 3 >= last["close"] * 0.01):
        return False

    # 7️⃣ Momentum continuation: close higher by 0.07%
    if not (last["close"] > prev["close"] * 1.0007):
        return False

    return True


# ======================================================
# MAX-PROFIT SHORT SETUP
# ======================================================

def check_short_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]

    # 1️⃣ Strong bearish trend: EMA separation
    if not (last["ema20"] < last["ema50"] * 0.9975):
        return False

    # 2️⃣ RSI expansion bearish zone
    if not (32 < last["rsi"] < 48):
        return False

    # 3️⃣ Power candle (60% body)
    body  = last["open"] - last["close"]
    range_ = last["high"] - last["low"]
    if not (body > 0 and body >= 0.60 * range_):
        return False

    # 4️⃣ Micro-breakdown (break previous 2 lows)
    if not (last["close"] < min(prev["low"], df5.iloc[-3]["low"])):
        return False

    # 5️⃣ ATR explosion (10% increase)
    if not (last["atr"] >= prev["atr"] * 1.10):
        return False

    # 6️⃣ Expected big move (≥1% potential)
    if not (last["atr"] * 3 >= last["close"] * 0.01):
        return False

    # 7️⃣ Momentum continuation: closes lower by 0.07%
    if not (last["close"] < prev["close"] * 0.9993):
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
# SEND SIGNAL
# ======================================================

def send_signal(symbol, direction, price, atr):

    if direction == "LONG":
        sl  = price - (2 * atr)
        tp1 = price + (2 * atr)
        tp2 = price + (4 * atr)
        tp3 = price + (6 * atr)
    else:
        sl  = price + (2 * atr)
        tp1 = price - (2 * atr)
        tp2 = price - (4 * atr)
        tp3 = price - (6 * atr)

    msg = (
        f"🔥 MAX-PROFIT {direction} SIGNAL\n\n"
        f"Pair: {symbol}\n"
        f"Entry: {price}\n"
        f"ATR: {round(atr,4)}\n\n"
        f"SL:  {round(sl,4)}\n"
        f"TP1: {round(tp1,4)}\n"
        f"TP2: {round(tp2,4)}\n"
        f"TP3: {round(tp3,4)}\n"
        f"⚠ High-momentum informational analysis only."
    )

    send_telegram_message(msg)
    print("SIGNAL →", symbol, direction)


# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():
    send_telegram_message("🚀 MAX-PROFIT Bot Activated (Very Selective)")

    while True:
        try:
            for ex_name in EXCHANGES:
                ex = get_exchange(ex_name)
                if ex is None:
                    continue

                for symbol in fetch_usdt_pairs(ex):

                    try:
                        df5 = fetch_ohlcv_df(ex, symbol, "5m")
                        trend = trend_direction(df5)
                        last  = df5.iloc[-1]

                        if trend == "UP" and check_long_setup(df5):
                            send_signal(symbol, "LONG", last["close"], last["atr"])

                        if trend == "DOWN" and check_short_setup(df5):
                            send_signal(symbol, "SHORT", last["close"], last["atr"])

                    except Exception as err:
                        print("Error on", symbol, ":", err)

            time.sleep(SCAN_INTERVAL)

        except Exception as err:
            print("Scanner error:", err)
            time.sleep(10)


# ======================================================
# TELEGRAM WEBHOOK
# ======================================================

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data: 
        return "OK"

    msg      = data.get("message", {})
    chat_id  = msg.get("chat", {}).get("id")
    text     = msg.get("text","")

    if text == "/start":
        send_telegram_message("Bot Online — MAX-PROFIT Mode Enabled.", chat_id)

    elif text == "/status":
        send_telegram_message("📡 Bot Running (Very Selective)...", chat_id)

    elif text == "/help":
        send_telegram_message(
            "/start — Start Bot\n"
            "/status — Bot Status\n"
            "/help — Commands",
            chat_id
        )

    return "OK"


# ======================================================
# START SCANNER
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# RENDER WEB SERVER
# ======================================================

@app.route("/")
def home():
    return "MAX-PROFIT Bot Running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
