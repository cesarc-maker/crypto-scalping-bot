import os
import time
import ccxt
import pandas as pd
import numpy as np
from flask import Flask
import threading
import requests
from datetime import datetime, timezone

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 10
PAIR_LIMIT = 50

EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]

# Tracks symbols that error repeatedly
blacklist = set()

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

def send_startup_message():
    send_telegram("🚀 ELITE SCALPER BOT RUNNING")

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

last_signals = {}
DUPLICATE_WINDOW = 1800  # 30 minutes

def allow_signal(symbol, direction):
    now = time.time()

    key = f"{symbol}_{direction}"

    if key not in last_signals:
        last_signals[key] = []

    # keep only recent timestamps
    last_signals[key] = [
        t for t in last_signals[key] if now - t < DUPLICATE_WINDOW
    ]

    if len(last_signals[key]) >= 1:
        return False

    last_signals[key].append(now)
    return True

# ======================================================
# INDICATORS
# ======================================================

def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def compute_rsi(series, length=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_up = up.rolling(length).mean()
    avg_down = down.rolling(length).mean()
    rs = avg_up / (avg_down + 1e-10)
    return 100 - 100/(1+rs)

def add_indicators(df):
    df["ema9"] = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"] = compute_rsi(df["close"])
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    df["std"] = df["close"].rolling(20).std()
    return df

# ======================================================
# EXCHANGE HELPERS
# ======================================================

def get_exchange(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options": {"defaultType": "future"}})
        if name == "bybit":
            return ccxt.bybit({"options": {"defaultType": "linear"}})
        return getattr(ccxt, name)()
    except Exception:
        return None

def fetch_usdt_pairs(ex):
    try:
        mk = ex.load_markets()
        return [s for s in mk if s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []

def fetch_df(ex, symbol, tf):
    try:
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=tf, limit=200)
        df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
    except:
        return None

# ======================================================
# ELITE LOGIC
# ======================================================

def detect_sweep(df, direction):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    if direction == "long":
        return last["low"] < prev["low"] and last["close"] > prev["low"]
    if direction == "short":
        return last["high"] > prev["high"] and last["close"] < prev["high"]
    return False

def detect_displacement(df, direction):
    last = df.iloc[-1]
    full = last["high"] - last["low"]

    if full == 0:
        return False

    body = abs(last["close"] - last["open"])
    body_ratio = body / full
    vol_ok = last["volume"] > df["vol_sma"].iloc[-1] * 1.5

    if direction == "long":
        return last["close"] > last["open"] and body_ratio >= 0.60 and vol_ok

    if direction == "short":
        return last["open"] > last["close"] and body_ratio >= 0.60 and vol_ok

def detect_fvg(df, direction):
    c1 = df.iloc[-3]
    c3 = df.iloc[-1]

    # STRICT FVG
    if direction == "long":
        if c1["low"] > c3["high"]:
            return (c3["high"], c1["low"])

    if direction == "short":
        if c1["high"] < c3["low"]:
            return (c1["high"], c3["low"])

    return None

def price_in_mid_fvg(price, fvg):
    low, high = min(fvg), max(fvg)
    mid = low + (high - low) * 0.50
    return low < price < mid

def generate_signal(df1, df5, df15):

    if len(df1) < 50 or len(df5) < 50 or len(df15) < 50:
        return None

    trend_long = df15["ema50"].iloc[-1] > df15["ema200"].iloc[-1]
    trend_short = df15["ema50"].iloc[-1] < df15["ema200"].iloc[-1]

    for direction in ["long", "short"]:

        # Sweep must appear on 1m + 5m
        if not detect_sweep(df1, direction): 
            continue
        if not detect_sweep(df5, direction): 
            continue

        # Strong displacement
        if not detect_displacement(df1, direction):
            continue

        # Strict FVG
        fvg = detect_fvg(df1, direction)
        if not fvg:
            continue

        entry = df1["close"].iloc[-1]

        # Mid-level FVG pullback entry
        if not price_in_mid_fvg(entry, fvg):
            continue

        # Trend filter
        if direction == "long" and not trend_long:
            continue
        if direction == "short" and not trend_short:
            continue

        # SL = sweep extreme
        sl = df1["low"].iloc[-2] if direction == "long" else df1["high"].iloc[-2]
        R = abs(entry - sl)

        return direction.upper(), entry, sl, R

    return None

# =========================
