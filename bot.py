import os
import time
import ccxt
import pandas as pd
import numpy as np
import threading
from flask import Flask
import requests

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PORT = int(os.getenv("PORT", 10000))

SCAN_INTERVAL = 30        # faster than old bot
PAIR_LIMIT = 60           # more pairs = more signals

EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]

# ======================================================
# TELEGRAM
# ======================================================

def send_telegram(text):
    try:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={CHAT_ID}&text={text}"
        )
    except:
        pass

# ======================================================
# STARTUP MESSAGE
# ======================================================

def startup():
    send_telegram(
        "🚀 HIGH-RETURN BREAKOUT BOT RUNNING\n"
        "Aggressive filtering enabled.\n"
        "More signals. Higher R. Faster detection.\n"
        "Multi-exchange scanning active."
    )

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

recent = {}
WINDOW = 3600   # 1 hour

def allow(symbol, key):
    now = time.time()

    if symbol not in recent:
        recent[symbol] = {}

    if key not in recent[symbol]:
        recent[symbol][key] = now
        return True

    if now - recent[symbol][key] > WINDOW:
        recent[symbol][key] = now
        return True

    return False

# ======================================================
# INDICATORS
# ======================================================

def add_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    df["range"] = df["high"] - df["low"]
    return df

# ======================================================
# EXCHANGE HELPERS
# ======================================================

def get_ex(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options": {"defaultType": "future"}})
        if name == "bybit":
            return ccxt.bybit({"options": {"defaultType": "linear"}})
        return getattr(ccxt, name)()
    except:
        return None

def get_pairs(ex):
    try:
        mk = ex.load_markets()
        return [s for s in mk if s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []

def get_df(ex, symbol, tf):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=150)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
    except:
        return None

# ======================================================
# HIGH-RETURN BREAKOUT LOGIC
# ======================================================

def breakout_long(df5, df15):

    last = df5.iloc[-1]
    p1   = df5.iloc[-2]
    p2   = df5.iloc[-3]
    p3   = df5.iloc[-4]

    # Trend bias
    if not (df5["ema20"].iloc[-1] > df5["ema50"].iloc[-1]): return False
    if not (df15["ema20"].iloc[-1] > df15["ema50"].iloc[-1]): return False

    # ATR explosion (aggressive)
    if not (last["atr"] >= p1["atr"] * 1.18): return False

    # Volume expansion
    if not (last["volume"] > last["vol_sma"] * 1.5): return False

    # Level
    breakout = max(p1["high"], p2["high"], p3["high"])

    # Early breakout capture
    if not (last["close"] > breakout * 1.001): return False

    # Candle power (looser)
    body = last["close"] - last["open"]
    if body <= 0: return False
    if body < 0.55 * last["range"]: return False

    return True

def breakout_short(df5, df15):

    last = df5.iloc[-1]
    p1   = df5.iloc[-2]
    p2   = df5.iloc[-3]
    p3   = df5.iloc[-4]

    if not (df5["ema20"].iloc[-1] < df5["ema50"].iloc[-1]): return False
    if not (df15["ema20"].iloc[-1] < df15["ema50"].iloc[-1]): return False

    if not (last["atr"] >= p1["atr"] * 1.18): return False
    if not (last["volume"] > last["vol_sma"] * 1.5): return False

    breakdown = min(p1["low"], p2["low"], p3["low"])

    if not (last["close"] < breakdown * 0.999): return False

    body = last["open"] - last["close"]
    if body <= 0: return False
    if body < 0.55 * last["range"]: return False

    return True

# ======================================================
# SEND SIGNAL
# ======================================================

def send_signal(symbol, side, price, atr):

    # Higher-return TP system
    if side == "LONG":
        sl  = price - 2 * atr
        tp1 = price + 2 * atr
        tp2 = price + 4 * atr
        tp3 = price + 7 * atr
        tp4 = price +12 * atr
    else:
        sl  = price + 2 * atr
        tp1 = price - 2 * atr
        tp2 = price - 4 * atr
        tp3 = price - 7 * atr
        tp4 = price -12 * atr

    msg = (
        f"🔥 HIGH-RETURN {side} BREAKOUT\n\n"
        f"Pair: {symbol}\n"
        f"Entry: {price}\n"
        f"ATR: {round(atr,4)}\n\n"
        f"SL:  {round(sl,4)}\n"
        f"TP1: {round(tp1,4)}\n"
        f"TP2: {round(tp2,4)}\n"
        f"TP3: {round(tp3,4)}\n"
        f"TP4: {round(tp4,4)}\n\n"
        "⚡ Aggressive breakout mode enabled."
    )

    send_telegram(msg)

# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner():

    startup()

    while True:
        for ex_name in EXCHANGES:

            ex = get_ex(ex_name)
            if ex is None: 
                continue

            for symbol in get_pairs(ex):

                try:
                    df5  = get_df(ex, symbol, "5m")
                    df15 = get_df(ex, symbol, "15m")

                    if df5 is None or df15 is None:
                        continue

                    last = df5.iloc[-1]
                    atr  = last["atr"]

                    # 🔥 HIGH-RETURN LONG
                    if breakout_long(df5, df15):
                        key = f"LONG_{symbol}_{last['close']}"
                        if allow(symbol, key):
                            send_signal(symbol, "LONG", last["close"], atr)

                    # 🔥 HIGH-RETURN SHORT
                    if breakout_short(df5, df15):
                        key = f"SHORT_{symbol}_{last['close']}"
                        if allow(symbol, key):
                            send_signal(symbol, "SHORT", last["close"], atr)

                except:
                    continue

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER — REQUIRED BY RENDER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "HIGH-RETURN BREAKOUT BOT RUNNING"

if __name__ == "__main__":
    threading.Thread(target=scanner, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
