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

SCAN_INTERVAL = 15            # Faster for scalping
PAIR_LIMIT    = 80
TOP_MOVER_COUNT = 12          # Explosive RR = strongest pairs only

EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]

recent_signals = {}
WINDOW = 1800  # 30 mins

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
    send_telegram("🚀 EXPLOSIVE RR SCALP BOT ACTIVE\nHigh-return breakout mode enabled.")

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

def allow(symbol, direction):
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
            return ccxt.binance({"options":{"defaultType":"future"}})
        if name == "bybit":
            return ccxt.bybit({"options":{"defaultType":"linear"}})
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
    df["ema9"]  = df["close"].ewm(span=9).mean()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
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

        score = pct_change * 0.55 + vol_ratio * 0.45
        movers.append((s, score))

    movers_sorted = sorted(movers, key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers_sorted[:TOP_MOVER_COUNT]]

# ======================================================
# EXPLOSIVE RR SCALPING BREAKOUT LOGIC
# ======================================================

def breakout_long(df5):
    last = df5.iloc[-1]
    p1   = df5.iloc[-2]
    p2   = df5.iloc[-3]

    # Strong trend stack for explosive breakouts
    if not (last["ema9"] > last["ema20"] > last["ema50"]):
        return False

    # ATR expansion (looser)
    if not (last["atr"] > p1["atr"] * 1.08):
        return False

    # Volume expansion (looser but required)
    if not (last["volume"] > last["vol_sma"] * 1.25):
        return False

    breakout = max(p1["high"], p2["high"])

    # Early breakout capture
    if not (last["close"] > breakout * 1.0004):
        return False

    # Ignition candle
    body = last["close"] - last["open"]
    if body <= 0 or body < 0.50 * last["range"]:
        return False

    return True

def breakout_short(df5):
    last = df5.iloc[-1]
    p1   = df5.iloc[-2]
    p2   = df5.iloc[-3]

    if not (last["ema9"] < last["ema20"] < last["ema50"]):
        return False

    if not (last["atr"] > p1["atr"] * 1.08):
        return False

    if not (last["volume"] > last["vol_sma"] * 1.25):
        return False

    breakdown = min(p1["low"], p2["low"])

    if not (last["close"] < breakdown * 0.9996):
        return False

    body = last["open"] - last["close"]
    if body <= 0 or body < 0.50 * last["range"]:
        return False

    return True

# ======================================================
# SIGNAL SYSTEM (EXPLOSIVE RR)
# ======================================================

def send_signal(symbol, direction, price, atr):

    # Tighter stop, bigger returns
    if direction == "LONG":
        sl  = price - 1.3 * atr
        tp1 = price + 2.0 * atr
        tp2 = price + 4.0 * atr
        tp3 = price + 7.0 * atr
        tp4 = price +12.0 * atr
    else:
        sl  = price + 1.3 * atr
        tp1 = price - 2.0 * atr
        tp2 = price - 4.0 * atr
        tp3 = price - 7.0 * atr
        tp4 = price -12.0 * atr

    # generic (non-advice) classification of leverage tiers
    lv = (
        "10–20x" if ("BTC" in symbol or "ETH" in symbol)
        else "8–15x" if any(x in symbol for x in ["SOL", "AVAX", "LINK", "BNB"])
        else "5–10x"
    )

    # general challenge framework
    hypothetical_account = 100
    risk_percent = 0.01
    risk_amount  = hypothetical_account * risk_percent

    stop_distance = abs(price - sl) / price
    stop_distance = stop_distance if stop_distance > 0 else 0.0001

    example_size = risk_amount / stop_distance

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    msg = (
        f"🔥 EXPLOSIVE RR {direction}\n\n"
        f"Pair: {symbol}\n"
        f"Entry: {round(price,6)}\n"
        f"ATR: {round(atr,6)}\n\n"

        f"SL:  {round(sl,6)}\n"
        f"TP1: {round(tp1,6)}\n"
        f"TP2: {round(tp2,6)}\n"
        f"TP3: {round(tp3,6)}\n"
        f"TP4: {round(tp4,6)}\n\n"

        f"Suggested Leverage Tier: {lv}\n"
        f"Time: {ts}\n\n"

        f"📈 Challenge Framework (General Example Only)\n"
        f"Example Starting Account: ${hypothetical_account}\n"
        f"Risk Tier Example (1%): ${risk_amount:.2f}\n"
        f"Stop Distance: {stop_distance*100:.2f}%\n"
        f"Formula Example:\n"
        f"    size = risk_amount / stop_distance\n"
        f"    size ≈ ${example_size:.2f}\n\n"

        f"🧠 Challenge Mindset (General Note):\n"
        f"Explosive setups require discipline and consistency. "
        f"This is technical information only — not financial advice."
    )

    send_telegram(msg)

# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():

    startup()

    while True:
        for ex_name in EXCHANGES:

            ex = get_ex(ex_name)
            if not ex:
                continue

            movers = detect_top_movers(ex)

            for symbol in movers:
                try:
                    df5 = get_df(ex, symbol, "5m")
                    if df5 is None or len(df5) < 20:
                        continue

                    last = df5.iloc[-1]
                    atr  = last["atr"]

                    if breakout_long(df5):
                        if allow(symbol, "LONG"):
                            send_signal(symbol, "LONG", last["close"], atr)

                    if breakout_short(df5):
                        if allow(symbol, "SHORT"):
                            send_signal(symbol, "SHORT", last["close"], atr)

                except:
                    continue

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "EXPLOSIVE RR SCALP BOT RUNNING"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
