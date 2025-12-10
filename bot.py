import os
import time
import ccxt
import pandas as pd
import numpy as np
import threading
from flask import Flask
import requests
from datetime import datetime, timezone

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = os.getenv("CHAT_ID")
PORT      = int(os.getenv("PORT", 10000))

SCAN_INTERVAL = 20
PAIR_LIMIT    = 80
TOP_MOVER_COUNT = 20

EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]

recent_signals = {}
WINDOW = 1800  # 30 min duplicate protection

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

def startup():
    send_telegram(
        "🚀 QUICK-SCALP BREAKOUT BOT ACTIVE\n"
        "Controlled Aggressive Mode + Top Movers Enabled."
    )

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

def allow(symbol, direction, price):
    now = time.time()
    key = f"{symbol}_{direction}"

    if symbol not in recent_signals:
        recent_signals[symbol] = {}

    if key not in recent_signals[symbol]:
        recent_signals[symbol][key] = now
        return True

    if now - recent_signals[symbol][key] > WINDOW:
        recent_signals[symbol][key] = now
        return True

    return False

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

# ======================================================
# INDICATORS
# ======================================================

def add_indicators(df):
    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    df["range"] = df["high"] - df["low"]
    return df

def get_df(ex, symbol, tf):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=120)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
    except:
        return None

# ======================================================
# HYBRID TOP MOVER DETECTION
# ======================================================

def detect_top_movers(ex):
    movers = []
    pairs = get_pairs(ex)

    for s in pairs:
        df = get_df(ex, s, "15m")
        if df is None or len(df) < 20:
            continue

        pct_change = (df["close"].iloc[-1] - df["close"].iloc[-4]) / df["close"].iloc[-4] * 100
        vol_ratio = df["volume"].iloc[-1] / (df["vol_sma"].iloc[-1] + 1e-10)

        score = pct_change * 0.6 + vol_ratio * 0.4
        movers.append((s, score))

    movers_sorted = sorted(movers, key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers_sorted[:TOP_MOVER_COUNT]]

# ======================================================
# SCALPING BREAKOUT LOGIC
# ======================================================

def breakout_long(df5):
    last = df5.iloc[-1]
    p1 = df5.iloc[-2]
    p2 = df5.iloc[-3]

    if not (last["ema9"] > last["ema20"]):
        return False

    if not (last["atr"] > p1["atr"] * 1.12):
        return False

    if not (last["volume"] > last["vol_sma"] * 1.4):
        return False

    breakout = max(p1["high"], p2["high"])

    if not (last["close"] > breakout * 1.0008):
        return False

    body = last["close"] - last["open"]
    if body <= 0 or body < 0.52 * last["range"]:
        return False

    return True

def breakout_short(df5):
    last = df5.iloc[-1]
    p1 = df5.iloc[-2]
    p2 = df5.iloc[-3]

    if not (last["ema9"] < last["ema20"]):
        return False

    if not (last["atr"] > p1["atr"] * 1.12):
        return False

    if not (last["volume"] > last["vol_sma"] * 1.4):
        return False

    breakdown = min(p1["low"], p2["low"])

    if not (last["close"] < breakdown * 0.9992):
        return False

    body = last["open"] - last["close"]
    if body <= 0 or body < 0.52 * last["range"]:
        return False

    return True

# ======================================================
# SIGNAL MESSAGE WITH CHALLENGE FRAMEWORK (NO PROGRESSION)
# ======================================================

def send_signal(symbol, direction, price, atr):

    # SL + TP targets
    if direction == "LONG":
        sl  = price - 1.6 * atr
        tp1 = price + 1.2 * atr
        tp2 = price + 2.0 * atr
        tp3 = price + 3.5 * atr
    else:
        sl  = price + 1.6 * atr
        tp1 = price - 1.2 * atr
        tp2 = price - 2.0 * atr
        tp3 = price - 3.5 * atr

    # leverage suggestion (general)
    lv = (
        "10–20x" if ("BTC" in symbol or "ETH" in symbol)
        else "8–15x" if any(x in symbol for x in ["SOL", "AVAX", "LINK", "BNB"])
        else "5–10x"
    )

    # generic challenge framework
    hypothetical_account = 100
    risk_percent = 0.01
    risk_amount  = hypothetical_account * risk_percent

    stop_distance = abs(price - sl) / price
    stop_distance = stop_distance if stop_distance > 0 else 0.0001

    example_size = risk_amount / stop_distance

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    msg = (
        f"🔥 QUICK-SCALP {direction}\n\n"
