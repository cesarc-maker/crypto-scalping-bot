import os
import time
import ccxt
import pandas as pd
from flask import Flask, request
import threading
import requests


# ======================================================
# CONFIG
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
# TELEGRAM MSG
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
# INDICATORS
# ======================================================

def fetch_ohlcv_df(exchange, symbol, timeframe="5m"):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=150)
    df = pd.DataFrame(data, columns=[
        "timestamp","open","high","low","close","volume"
    ])
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
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"]   = compute_rsi(df["close"])
    df["atr"]   = compute_atr(df)
    df["vol_sma"] = df["volume"].rolling(20).mean()
    return df


# ======================================================
# HIGH-RETURN BREAKOUT LOGIC
# ======================================================

def check_breakout_long(df):
    last = df.iloc[-1]
    prev1 = df.iloc[-2]
    prev2 = df.iloc[-3]

    # 1. ATR EXPLOSION (20%+)
    if not (last["atr"] >= prev1["atr"] * 1.20):
        return False

    # 2. VOLUME EXPANSION (150%+)
    if not (last["volume"] > last["vol_sma"] * 1.5):
        return False

    # 3. STRUCTURE BREAKOUT (break last 3 highs)
    breakout_level = max(prev1["high"], prev2["high"], df.iloc[-4]["high"])
    if not (last["close"] > breakout_level):
        return False

    # 4. POWER CANDLE (65%+ body)
    body = last["close"] - last["open"]
    range_ = last["high"] - last["low"]
    if not (body > 0 and body >= 0.65 * range_):
        return False

    return True


def check_breakout_short(df):
    last = df.iloc[-1]
    prev1 = df.iloc[-2]
    prev2 = df.iloc[-3]

    # 1. ATR EXPLOSION (20%+)
    if not (last["atr"] >= prev1["atr"] * 1.20):
        return False

    # 2. VOLUME EXPANSION (150%+)
    if not (last["volume"] > last["vol_sma"] * 1.5):
        return False

    # 3. STRUCTURE BREAKDOWN (break last 3 lows)
    breakdown_level = min(prev1["low"], prev2["low"], df.iloc[-4]["low"])
    if not (last["close"] < breakdown_level):
        return False

    # 4. POWER CANDLE (65%+ body)
    body = last["open"] - last["close"]
    range_ = last["high"] - last["low"]
    if not (body > 0 and body >= 0.65 * range_):
        return False

    return True


# ======================================================
# SIGNAL SENDER
# ======================================================

def send_signal(symbol, direction, price, atr):

    if direction == "LONG":
        sl  = price - (2 * atr)
        tp1 = price + (2 * atr)
        tp2 = price + (4 * atr)
        tp3 = price + (6 * atr)
        tp4 = price + (10 * atr)
    else:
        sl  = price + (2 * atr)
        tp1 = price - (2 * atr)
        tp2 = price - (4 * atr)
        tp3 = price - (6 * atr)
        tp4 = price - (10 * atr)

    msg = (
        f"🔥 HIGH-RETURN {direction} BREAKOUT SIGNAL\n\n"
        f"Pair: {symbol}\n"
        f"Entry: {price}\n"
        f"ATR: {round(atr,4)}\n\n"
        f"SL:  {round(sl,4)}\n"
        f"TP1: {round(tp1,4)}\n"
        f"TP2: {round(tp2,4)}\n"
        f"TP3: {round(tp3,4)}\n"
        f"TP4: {round(tp4,4)} (High-return target)\n"
        f"⚠ Informational only."
    )

    send_telegram_message(msg)
    print("SIGNAL →", symbol, direction)


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
# MAIN SCANNING LOOP
# ======================================================

def scanner_loop():
    send_telegram_message("🚀 HIGH-RETURN BREAKOUT BOT ACTIVE")

    while True:
        try:
            for ex_name in EXCHANGES:

                ex = get_exchange(ex_name)
                if ex is None:
                    continue

                for symbol in fetch_usdt_pairs(ex):

                    try:
                        df = fetch_ohlcv_df(ex, symbol, "5m")
                        last = df.iloc[-1]

                        # Long breakout
                        if check_breakout_long(df):
                            send_signal(symbol, "LONG", last["close"], last["atr"])

                        # Short breakout
                        if check_breakout_short(df):
                            send_signal(symbol, "SHORT", last["close"], last["atr"])

                    except Exception as err:
                        print("Error on", symbol, ":", err)

            time.sleep(SCAN_INTERVAL)

        except Exception as err:
            print("Scanner crashed:", err)
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

    msg  = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text","")

    if text == "/start":
        send_telegram_message("Bot Online — High-Return Breakout Mode Enabled.", chat_id)

    elif text == "/status":
        send_telegram_message("📡 Bot Running.", chat_id)

    elif text == "/help":
        send_telegram_message(
            "/start — Activate Bot\n"
            "/status — Bot Status\n"
            "/help — Commands",
            chat_id
        )

    return "OK"


# ======================================================
# START THREAD
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# RENDER SERVER
# ======================================================

@app.route("/")
def home():
    return "HIGH-RETURN BREAKOUT BOT RUNNING"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
