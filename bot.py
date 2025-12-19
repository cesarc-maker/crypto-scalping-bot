# ======================================================
# ADVANCED S&D SCALPING BOT — 70% MODE (HIGH RETURN)
# LIMIT-ONLY • HIGH ACCURACY • RUNNER ENABLED
# ======================================================

import os
import time
import ccxt
import pandas as pd
import threading
from flask import Flask
import requests
import logging
from datetime import datetime, timezone

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("SDBOT_70_PLUS")

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

CHAT_ID1 = os.getenv("CHAT_ID", "").strip()
CHAT_ID2 = os.getenv("CHAT_ID2", "").strip()
RAW_CHAT_IDS = os.getenv("CHAT_IDS", "")

CHAT_IDS = set()
if CHAT_ID1: CHAT_IDS.add(CHAT_ID1)
if CHAT_ID2: CHAT_IDS.add(CHAT_ID2)
if RAW_CHAT_IDS:
    for cid in RAW_CHAT_IDS.split(","):
        if cid.strip():
            CHAT_IDS.add(cid.strip())
CHAT_IDS = list(CHAT_IDS)

PORT = int(os.getenv("PORT", 10000))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 80))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 12))
WINDOW = int(os.getenv("WINDOW", 1800))

EXCHANGES = ["binance", "binance_futures", "bybit", "kucoin", "okx"]

recent_signals = {}

# ======================================================
# TELEGRAM
# ======================================================

def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_IDS:
        return
    encoded = requests.utils.quote(text)
    for cid in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={cid}&text={encoded}"
            requests.get(url, timeout=5)
        except Exception as e:
            log.error(f"Telegram error {cid}: {e}")

def send_startup():
    msg = (
        "🚀 ADVANCED S&D BOT — *70% MODE + HIGH RETURN*\n\n"
        "Mode: LIMIT ONLY\n"
        "Entries: Strong Displacement + Structure\n"
        "Exits: Partial + Runner Logic\n"
        "Risk: LOW / MEDIUM ONLY\n\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n\n"
        "Institutional continuation engine online ⚡"
    )
    send_telegram(msg)
    log.info("Startup message sent")

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

def allow(symbol, direction):
    now = time.time()
    key = f"{symbol}_{direction}"
    if key not in recent_signals:
        recent_signals[key] = now
        return True
    if now - recent_signals[key] > WINDOW:
        recent_signals[key] = now
        return True
    return False

# ======================================================
# INDICATORS
# ======================================================

def add_indicators(df):
    df["ema9"]  = df["close"].ewm(span=9).mean()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    df["vol_sma"] = df["volume"].rolling(20).mean()

    df["atr_raw"] = df["high"] - df["low"]
    df["atr"] = df["atr_raw"].rolling(14).mean()
    df["atr_sma"] = df["atr"].rolling(14).mean()

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
# TOP MOVERS
# ======================================================

def detect_top_movers(ex):
    movers = []
    for s in get_pairs(ex):
        df = get_df(ex, s, "15m")
        if df is None or len(df) < 20:
            continue
        pct = (df["close"].iloc[-1] - df["close"].iloc[-4]) / df["close"].iloc[-4] * 100
        vol = df["volume"].iloc[-1] / (df["vol_sma"].iloc[-1] + 1e-9)
        movers.append((s, pct * 0.55 + vol * 0.45))
    movers.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers[:TOP_MOVER_COUNT]]

# ======================================================
# CORE STRATEGY (UNCHANGED STRUCTURE)
# ======================================================

def trend_long(df5, df15):
    return (
        df5["ema9"].iloc[-1] > df5["ema20"].iloc[-1] > df5["ema50"].iloc[-1] and
        df15["ema9"].iloc[-1] > df15["ema20"].iloc[-1] > df15["ema50"].iloc[-1]
    )

def trend_short(df5, df15):
    return (
        df5["ema9"].iloc[-1] < df5["ema20"].iloc[-1] < df5["ema50"].iloc[-1] and
        df15["ema9"].iloc[-1] < df15["ema20"].iloc[-1] < df15["ema50"].iloc[-1]
    )

def volatility_ok(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return last["atr"] > last["atr_sma"] and last["atr"] > prev["atr"] * 1.02

def volume_ok(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return last["volume"] > last["vol_sma"] * 2.0 and last["volume"] > prev["volume"]

def find_recent_swing_high(df):
    for i in range(len(df)-3, 2, -1):
        if df["high"].iloc[i] > df["high"].iloc[i-1] and df["high"].iloc[i] > df["high"].iloc[i+1]:
            return df["high"].iloc[i]
    return None

def find_recent_swing_low(df):
    for i in range(len(df)-3, 2, -1):
        if df["low"].iloc[i] < df["low"].iloc[i-1] and df["low"].iloc[i] < df["low"].iloc[i+1]:
            return df["low"].iloc[i]
    return None

def breakout_long(df5, df15):
    last = df5.iloc[-1]
    price = last["close"]
    if not trend_long(df5, df15):
        return False
    if not volatility_ok(df5) or not volume_ok(df5):
        return False
    swing_high = find_recent_swing_high(df5)
    if swing_high is None or price <= swing_high * 1.0005:
        return False
    return (last["close"] - last["open"]) >= 0.55 * last["range"]

def breakout_short(df5, df15):
    last = df5.iloc[-1]
    price = last["close"]
    if not trend_short(df5, df15):
        return False
    if not volatility_ok(df5) or not volume_ok(df5):
        return False
    swing_low = find_recent_swing_low(df5)
    if swing_low is None or price >= swing_low * 0.9995:
        return False
    return (last["open"] - last["close"]) >= 0.55 * last["range"]

# ======================================================
# 70% MODE FILTER
# ======================================================

def strong_displacement(df):
    last = df.iloc[-1]
    return last["atr"] >= last["atr_sma"] * 2.0 and last["volume"] >= last["vol_sma"] * 2.0

# ======================================================
# SIGNAL BUILDER (HIGH RETURN)
# ======================================================

def send_signal(symbol, direction, price, atr):
    sl = price - 1.2 * atr if direction == "LONG" else price + 1.2 * atr
    stop_pct = abs(price - sl) / price * 100

    if stop_pct < 0.35 or stop_pct > 0.90:
        return

    # Core targets
    tp1 = price + 1.5 * atr if direction == "LONG" else price - 1.5 * atr
    tp2 = price + 3.0 * atr if direction == "LONG" else price - 3.0 * atr

    # Runner logic
    runner = price + 4.5 * atr if direction == "LONG" else price - 4.5 * atr

    leverage = 60 if stop_pct < 0.6 else 30
    risk = "LOW" if leverage >= 50 else "MEDIUM"

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    msg = (
        f"📌 LIMIT {direction} (70% MODE)\n\n"
        f"Pair: {symbol}\n"
        f"Entry: {round(price,6)}\n"
        f"Stop: {round(sl,6)}\n\n"
        f"TP1: {round(tp1,6)} (40%)\n"
        f"TP2: {round(tp2,6)} (40%)\n"
        f"Runner: {round(runner,6)} (20%)\n\n"
        f"Leverage: {leverage}x\n"
        f"Risk Level: {risk}\n"
        f"Time: {ts}"
    )

    send_telegram(msg)
    log.info(f"Signal sent → {symbol} {direction}")

# ======================================================
# MAIN LOOP
# ======================================================

def scanner_loop():
    send_startup()
    log.info("Scanner started (70% MODE + HIGH RETURN)")

    while True:
        for ex_name in EXCHANGES:
            ex = get_ex(ex_name)
            if not ex:
                continue

            for symbol in detect_top_movers(ex):
                try:
                    df5 = get_df(ex, symbol, "5m")
                    df15 = get_df(ex, symbol, "15m")
                    if df5 is None or df15 is None:
                        continue

                    last = df5.iloc[-1]
                    atr = last["atr"]

                    if not strong_displacement(df5):
                        continue

                    if breakout_long(df5, df15) and allow(symbol, "LONG"):
                        send_signal(symbol, "LONG", last["close"], atr)

                    if breakout_short(df5, df15) and allow(symbol, "SHORT"):
                        send_signal(symbol, "SHORT", last["close"], atr)

                except Exception as e:
                    log.error(f"Scan error {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ADVANCED S&D BOT — 70% MODE + HIGH RETURN RUNNING"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
