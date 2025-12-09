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

def send_startup():
    msg = (
        "🚀 *ELITE SCALPING BOT ONLINE*\n\n"
        "• Multi-timeframe Liquidity Sweep\n"
        "• Strong Displacement (60% body + vol spike)\n"
        "• Strict FVG Detection\n"
        "• Mid-level Pullback Entry\n"
        "• 15m Trend Filter\n"
        "• Dynamic Quality Rating\n"
        "• TP Targets: 1.5R / 2.5R / 4R\n"
        "• Exchanges: Binance, Binance Futures, Kucoin, Bybit, OKX\n"
        "• Scan Interval: 10 seconds\n"
    )
    send_telegram(msg)

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

recent_signals = {}
DUPLICATE_WINDOW = 1800

def allow_signal(symbol, direction):
    now = time.time()
    key = f"{symbol}_{direction}"

    if key not in recent_signals:
        recent_signals[key] = []

    recent_signals[key] = [t for t in recent_signals[key] if now - t < DUPLICATE_WINDOW]

    if len(recent_signals[key]) >= 1:
        return False

    recent_signals[key].append(now)
    return True

# ======================================================
# INDICATORS
# ======================================================

def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def rsi(series, length=14):
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
    df["rsi"] = rsi(df["close"])
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
    df["std"] = df["close"].rolling(20).std()
    return df

# ======================================================
# EXCHANGE WRAPPER
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

def fetch_pairs(ex):
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
# ELITE LOGIC — SWEEP → BOS → FVG → PULLBACK
# ======================================================

def sweep_detect(df, direction):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    if direction == "long":
        return last["low"] < prev["low"] and last["close"] > prev["low"]
    if direction == "short":
        return last["high"] > prev["high"] and last["close"] < prev["high"]
    return False

def detect_displacement(df, direction):
    last = df.iloc[-1]
    body = abs(last["close"] - last["open"])
    full = last["high"] - last["low"]

    if full == 0:
        return False

    body_ratio = body / full
    vol_ok = last["volume"] > df["vol_sma"].iloc[-1] * 1.5

    if direction == "long":
        return last["close"] > last["open"] and body_ratio >= 0.60 and vol_ok

    if direction == "short":
        return last["open"] > last["close"] and body_ratio >= 0.60 and vol_ok

def detect_fvg(df, direction):
    c1 = df.iloc[-3]
    c3 = df.iloc[-1]

    if direction == "long":
        if c1["low"] > c3["high"]:
            return (c3["high"], c1["low"])  
    if direction == "short":
        if c1["high"] < c3["low"]:
            return (c1["high"], c3["low"])
    return None

def in_mid_fvg(price, fvg):
    low, high = min(fvg), max(fvg)
    mid = low + (high - low) * 0.50
    return low < price < high and price <= mid + (high-low)*0.1

def generate_signal(df1, df5, df15):

    trend_long = df15["ema50"].iloc[-1] > df15["ema200"].iloc[-1]
    trend_short = df15["ema50"].iloc[-1] < df15["ema200"].iloc[-1]

    for direction in ["long", "short"]:

        sweep_1m = sweep_detect(df1, direction)
        sweep_5m = sweep_detect(df5, direction)

        if not (sweep_1m and sweep_5m):
            continue

        disp = detect_displacement(df1, direction)
        if not disp:
            continue

        fvg = detect_fvg(df1, direction)
        if not fvg:
            continue

        entry = df1["close"].iloc[-1]

        if not in_mid_fvg(entry, fvg):
            continue

        if direction == "long" and not trend_long:
            continue
        if direction == "short" and not trend_short:
            continue

        sl = df1["low"].iloc[-2] if direction == "long" else df1["high"].iloc[-2]
        R = abs(entry - sl)

        return (direction.upper(), entry, sl, R, fvg)

    return None

# ======================================================
# QUALITY RATING
# ======================================================

def quality_rating(df):
    last = df.iloc[-1]
    vol = last["volume"]
    atr = last["atr"]
    std = last["std"]
    vol_sma = df["vol_sma"].iloc[-1]

    score = 0
    if vol > vol_sma * 2: score += 1
    if atr > df["atr"].rolling(20).mean().iloc[-1]: score += 1
    if std > df["std"].rolling(20).mean().iloc[-1]: score += 1

    if score == 3:
        return "EXTREME"
    if score == 2:
        return "VERY HIGH"
    return "HIGH"

# ======================================================
# SEND SIGNAL
# ======================================================

def send_signal(symbol, ex_name, direction, entry, sl, tp1, tp2, tp3, quality):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lev = "10x–25x" if ("BTC" in symbol or "ETH" in symbol) else "5x–15x"

    msg = (
        f"🔥 ELITE SCALPING SIGNAL — {direction}\n\n"
        f"Pair: {symbol}\n"
        f"Exchange: {ex_name.upper()}\n"
        f"Entry: {entry}\n\n"
        f"Stop Loss: {sl}\n"
        f"TP1 (1.5R): {tp1}\n"
        f"TP2 (2.5R): {tp2}\n"
        f"TP3 (4R): {tp3}\n\n"
        f"Recommended Leverage: {lev}\n"
        f"Quality: {quality}\n"
        f"Time: {ts}\n"
    )
    send_telegram(msg)

# ======================================================
# MAIN SCANNER
# ======================================================

def scanner():
    send_startup()

    while True:
        for ex_name in EXCHANGES:
            ex = get_exchange(ex_name)
            if not ex:
                continue

            for symbol in fetch_pairs(ex):
                try:
                    df1 = fetch_df(ex, symbol, "1m")
                    df5 = fetch_df(ex, symbol, "5m")
                    df15 = fetch_df(ex, symbol, "15m")

                    if df1 is None or df5 is None or df15 is None:
                        continue

                    result = generate_signal(df1, df5, df15)
                    if not result:
                        continue

                    direction, entry, sl, R, fvg = result

                    if not allow_signal(symbol, direction):
                        continue

                    tp1 = entry + 1.5*R if direction=="LONG" else entry - 1.5*R
                    tp2 = entry + 2.5*R if direction=="LONG" else entry - 2.5*R
                    tp3 = entry + 4*R   if direction=="LONG" else entry - 4*R

                    quality = quality_rating(df1)

                    send_signal(symbol, ex_name, direction, entry, sl, tp1, tp2, tp3, quality)

                except Exception as e:
                    print("Error:", symbol, e)

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK KEEP-ALIVE
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ELITE SCALPING BOT RUNNING"

threading.Thread(target=scanner, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
