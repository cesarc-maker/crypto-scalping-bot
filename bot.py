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

SCAN_INTERVAL = 10  # every 10 seconds
PAIR_LIMIT = 50

EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]

# ======================================================
# TELEGRAM SENDER
# ======================================================

def send_telegram(text):
    try:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={CHAT_ID}&text={text}"
        )
    except Exception as e:
        print("Telegram error:", e)

# ======================================================
# STARTUP MESSAGE
# ======================================================

def send_startup_message():
    msg = (
        "🚀 *SCALPING BOT ACTIVE*\n\n"
        "Engine: Liquidity Sweep + Trend + Momentum\n"
        "Timeframes: 1m entries + 15m trend bias\n"
        "Scan Interval: 10s\n"
        "TP System: 1.5R / 2.5R / 4R\n"
        "Duplicate Protection: ON\n"
        "Exchanges: Binance, Binance Futures, Kucoin, Bybit, OKX\n"
    )
    send_telegram(msg)

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

recent_signals = {}
DUPLICATE_WINDOW = 1800  # 30 minutes


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


def compute_rsi(series, length=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    avg_gain = up.rolling(length).mean()
    avg_loss = down.rolling(length).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def add_indicators(df):
    df["ema9"] = ema(df["close"], 9)
    df["ema21"] = ema(df["close"], 21)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"] = compute_rsi(df["close"])
    df["volume_sma"] = df["volume"].rolling(20).mean()
    return df

# ======================================================
# EXCHANGE SETUP
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
        markets = ex.load_markets()
        return [s for s in markets if s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []

# ======================================================
# SCALPING LOGIC
# ======================================================

def fetch_df(ex, symbol, tf):
    try:
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=tf, limit=200)
        df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
    except:
        return None


def detect_sweep(df, direction):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    if direction == "long":
        return last["low"] < prev["low"] and last["close"] > prev["low"]
    if direction == "short":
        return last["high"] > prev["high"] and last["close"] < prev["high"]
    return False


def generate_signal(df1, df15):
    # 15m trend filter
    trend_long = df15["ema50"].iloc[-1] > df15["ema200"].iloc[-1]
    trend_short = df15["ema50"].iloc[-1] < df15["ema200"].iloc[-1]

    # sweeps
    sweep_long = detect_sweep(df1, "long")
    sweep_short = detect_sweep(df1, "short")

    # momentum
    momentum_long = df1["ema9"].iloc[-1] > df1["ema21"].iloc[-1]
    momentum_short = df1["ema9"].iloc[-1] < df1["ema21"].iloc[-1]

    # RSI reversal
    rsi = df1["rsi"].iloc[-1]

    # volume spike
    vol = df1["volume"].iloc[-1]
    vol_sma = df1["volume_sma"].iloc[-1]
    volume_spike = vol > vol_sma * 1.5

    # LONG setup
    if trend_long and sweep_long and momentum_long and rsi > 35 and volume_spike:
        entry = df1["close"].iloc[-1]
        sl = df1["low"].iloc[-2]
        R = entry - sl
        return ("LONG", entry, sl, R)

    # SHORT setup
    if trend_short and sweep_short and momentum_short and rsi < 65 and volume_spike:
        entry = df1["close"].iloc[-1]
        sl = df1["high"].iloc[-2]
        R = sl - entry
        return ("SHORT", entry, sl, R)

    return None

# ======================================================
# TELEGRAM SIGNAL FORMAT
# ======================================================

def send_signal(symbol, exchange_name, direction, entry, sl, tp1, tp2, tp3):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lev = "10x–25x" if "BTC" in symbol or "ETH" in symbol else "5x–15x"

    msg = (
        f"🔥 SCALPING SIGNAL — {direction}\n\n"
        f"Pair: {symbol}\n"
        f"Exchange: {exchange_name.upper()}\n"
        f"Entry: {entry}\n\n"
        f"Reason:\n"
        f" • 1m Liquidity Sweep\n"
        f" • 15m Trend Alignment\n"
        f" • EMA Momentum\n"
        f" • RSI Reversal\n"
        f" • Volume Spike\n\n"
        f"Stop Loss: {sl}\n"
        f"TP1 (1.5R): {tp1}\n"
        f"TP2 (2.5R): {tp2}\n"
        f"TP3 (4R): {tp3}\n\n"
        f"Recommended Leverage: {lev}\n"
        f"Time: {timestamp}\n"
        f"Quality: HIGH\n"
    )

    send_telegram(msg)

# ======================================================
# MAIN SCANNER
# ======================================================

def scanner_loop():
    send_startup_message()

    while True:
        for ex_name in EXCHANGES:
            ex = get_exchange(ex_name)
            if not ex:
                continue

            pairs = fetch_pairs(ex)

            for symbol in pairs:
                try:
                    df1 = fetch_df(ex, symbol, "1m")
                    df15 = fetch_df(ex, symbol, "15m")
                    if df1 is None or df15 is None:
                        continue

                    result = generate_signal(df1, df15)
                    if not result:
                        continue

                    direction, entry, sl, R = result

                    if not allow_signal(symbol, direction):
                        continue

                    # compute take profits
                    if direction == "LONG":
                        tp1 = entry + 1.5 * R
                        tp2 = entry + 2.5 * R
                        tp3 = entry + 4 * R
                    else:
                        tp1 = entry - 1.5 * R
                        tp2 = entry - 2.5 * R
                        tp3 = entry - 4 * R

                    send_signal(symbol, ex_name, direction, entry, sl, tp1, tp2, tp3)

                except Exception as e:
                    print("Error on pair:", symbol, e)

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK KEEP-ALIVE SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "SCALPING SIGNAL BOT RUNNING"

threading.Thread(target=scanner_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
