import os
import time
import ccxt
import pandas as pd
import numpy as np
from flask import Flask
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
# TELEGRAM SENDER
# ======================================================

def send_telegram_message(text):
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
    send_telegram_message(
        "🚀 ULTRA-STRICT BREAKOUT BOT ACTIVE\n\n"
        "MTF Trend: 5m + 15m + 1h\n"
        "Volume > 2.2x SMA20\n"
        "ATR Explosion > 35%\n"
        "Power Candle ≥ 75%\n"
        "Displacement Breakouts Enabled\n"
        "Whale Pressure Filter Enabled\n"
        "Regime Filter Enabled\n"
        "Duplicate Protection: ON\n\n"
        "Only elite-quality signals will fire."
    )

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

last_signal_level = {}
signal_times = {}
MAX_DUPES = 2
WINDOW = 7200  # 2 hours

def allow_signal(symbol, breakout_level):
    now = time.time()

    if symbol not in last_signal_level:
        last_signal_level[symbol] = None
    if symbol not in signal_times:
        signal_times[symbol] = []

    if last_signal_level[symbol] == breakout_level:
        return False

    signal_times[symbol] = [ts for ts in signal_times[symbol] if now - ts < WINDOW]

    if len(signal_times[symbol]) >= MAX_DUPES:
        return False

    last_signal_level[symbol] = breakout_level
    signal_times[symbol].append(now)
    return True

# ======================================================
# INDICATORS
# ======================================================

def compute_rsi(series, length=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100/(1 + rs))

def compute_atr(df):
    df["H-L"] = df["high"] - df["low"]
    df["H-PC"] = abs(df["high"] - df["close"].shift(1))
    df["L-PC"] = abs(df["low"] - df["close"].shift(1))
    tr = df[["H-L","H-PC","L-PC"]].max(axis=1)
    return tr.rolling(14).mean()

def add_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"] = compute_rsi(df["close"])
    df["atr"] = compute_atr(df)
    df["vol_sma"] = df["volume"].rolling(20).mean()
    df["range"] = df["high"] - df["low"]
    df["std20"] = df["close"].rolling(20).std()
    df["std50"] = df["close"].rolling(50).std()
    return df

# ======================================================
# MTF FETCHER
# ======================================================

def fetch_tf(exchange, symbol, timeframe):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=150)
    df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
    return add_indicators(df)

# ======================================================
# ULTRA STRICT LONG LOGIC
# ======================================================

def check_breakout_long(df5, df15, df1h):
    last = df5.iloc[-1]
    p1 = df5.iloc[-2]
    p2 = df5.iloc[-3]

    # 1. MTF Trend Alignment
    if not (df5["ema20"].iloc[-1] > df5["ema50"].iloc[-1]): return False
    if not (df15["ema20"].iloc[-1] > df15["ema50"].iloc[-1]): return False
    if not (df1h["ema20"].iloc[-1] > df1h["ema50"].iloc[-1]): return False

    # 2. ATR Explosion (Ultra Strict)
    if not (last["atr"] >= p1["atr"] * 1.35): return False

    # 3. Volume Expansion (Ultra Strict)
    if not (last["volume"] > last["vol_sma"] * 2.2): return False

    breakout_lvl = max(p1["high"], p2["high"], df5.iloc[-4]["high"])

    # 4. Displacement Breakout
    if not (last["close"] > breakout_lvl * 1.0035): return False

    # 5. Power Candle (≥ 75%)
    body = last["close"] - last["open"]
    if not (body > 0): return False
    if not (body >= 0.75 * last["range"]): return False

    # 6. Whale Pressure (BUY imbalance)
    bull_wick = last["close"] - last["open"]
    bear_wick = last["high"] - last["close"]
    if not (bull_wick > bear_wick): return False

    # 7. Regime Filter
    if not (last["std20"] > last["std50"] * 0.85): return False

    # 8. RSI Regime Confirmation
    if not (last["rsi"] > 60): return False

    # 9. Candle must NOT be inside candle
    prev_range = p1["range"]
    if last["range"] < prev_range * 1.15: return False

    return True

# ======================================================
# ULTRA STRICT SHORT LOGIC
# ======================================================

def check_breakout_short(df5, df15, df1h):
    last = df5.iloc[-1]
    p1 = df5.iloc[-2]
    p2 = df5.iloc[-3]

    # 1. MTF Trend
    if not (df5["ema20"].iloc[-1] < df5["ema50"].iloc[-1]): return False
    if not (df15["ema20"].iloc[-1] < df15["ema50"].iloc[-1]): return False
    if not (df1h["ema20"].iloc[-1] < df1h["ema50"].iloc[-1]): return False

    # 2. ATR
    if not (last["atr"] >= p1["atr"] * 1.35): return False

    # 3. Volume
    if not (last["volume"] > last["vol_sma"] * 2.2): return False

    breakdown_lvl = min(p1["low"], p2["low"], df5.iloc[-4]["low"])

    # 4. Displacement
    if not (last["close"] < breakdown_lvl * 0.9965): return False

    # 5. Power Candle
    body = last["open"] - last["close"]
    if not (body > 0): return False
    if not (body >= 0.75 * last["range"]): return False

    # 6. Whale Pressure (SELL imbalance)
    bear_pressure = last["open"] - last["close"]
    bull_recover = last["close"] - last["low"]
    if not (bear_pressure > bull_recover): return False

    # 7. Regime Filter
    if not (last["std20"] > last["std50"] * 0.85): return False

    # 8. RSI
    if not (last["rsi"] < 40): return False

    # 9. Avoid inside candle
    prev_range = p1["range"]
    if last["range"] < prev_range * 1.15: return False

    return True

# ======================================================
# SIGNAL SENDER
# ======================================================

def send_signal(symbol, direction, price, atr):
    if direction == "LONG":
        sl  = price - 2*atr
        tp1 = price + 2*atr
        tp2 = price + 4*atr
        tp3 = price + 6*atr
        tp4 = price +10*atr

    else:
        sl  = price + 2*atr
        tp1 = price - 2*atr
        tp2 = price - 4*atr
        tp3 = price - 6*atr
        tp4 = price -10*atr

    msg = (
        f"🔥 ULTRA-STRICT {direction} BREAKOUT\n\n"
        f"Pair: {symbol}\n"
        f"Entry: {price}\n"
        f"ATR: {round(atr,4)}\n\n"
        f"SL:  {round(sl,4)}\n"
        f"TP1: {round(tp1,4)}\n"
        f"TP2: {round(tp2,4)}\n"
        f"TP3: {round(tp3,4)}\n"
        f"TP4: {round(tp4,4)}\n\n"
        "⚠ Elite-grade signal only."
    )

    send_telegram_message(msg)
    print("SIGNAL:", symbol, direction)

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
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():

    send_startup_message()

    while True:
        try:
            for ex_name in EXCHANGES:
                ex = get_exchange(ex_name)
                if ex is None:
                    continue

                for symbol in fetch_usdt_pairs(ex):
                    try:
                        df5 = fetch_tf(ex, symbol, "5m")
                        df15 = fetch_tf(ex, symbol, "15m")
                        df1h = fetch_tf(ex, symbol, "1h")

                        last = df5.iloc[-1]

                        breakout_lvl = max(df5.iloc[-2]["high"], df5.iloc[-3]["high"], df5.iloc[-4]["high"])
                        breakdown_lvl = min(df5.iloc[-2]["low"], df5.iloc[-3]["low"], df5.iloc[-4]["low"])

                        # LONG
                        if check_breakout_long(df5, df15, df1h):
                            if allow_signal(symbol, breakout_lvl):
                                send_signal(symbol, "LONG", last["close"], last["atr"])

                        # SHORT
                        if check_breakout_short(df5, df15, df1h):
                            if allow_signal(symbol, breakdown_lvl):
                                send_signal(symbol, "SHORT", last["close"], last["atr"])

                    except Exception as err:
                        print("Error:", symbol, err)

            time.sleep(SCAN_INTERVAL)

        except Exception as err:
            print("Scanner crashed:", err)
            time.sleep(10)

# ======================================================
# RENDER SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ULTRA-STRICT BREAKOUT BOT RUNNING"

threading.Thread(target=scanner_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
