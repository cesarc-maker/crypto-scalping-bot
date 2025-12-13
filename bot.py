# ======================================================
# ADVANCED S&D SCALPING BOT (REWRITTEN – NON-TRADING IMPROVEMENTS ONLY)
# ======================================================

import os
import time
import ccxt
import pandas as pd
import numpy as np
import threading
from flask import Flask
import requests
from datetime import datetime, timezone
import logging

# ======================================================
# LOGGING SETUP
# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("SDBOT")

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

RAW_CHAT_IDS = os.getenv("CHAT_IDS", "")
CHAT_IDS = [cid.strip() for cid in RAW_CHAT_IDS.split(",") if cid.strip()]

PORT = int(os.getenv("PORT", 10000))

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 80))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 12))
WINDOW = int(os.getenv("WINDOW", 1800))

EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]

recent_signals = {}

# ======================================================
# TELEGRAM UTILITIES
# ======================================================

def send_telegram(text: str):
    """Send Telegram messages to all configured chats."""
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing.")
        return

    if not CHAT_IDS:
        log.warning("No CHAT_IDS defined.")
        return

    text_encoded = requests.utils.quote(text)

    for cid in CHAT_IDS:
        try:
            url = (
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?"
                f"chat_id={cid}&text={text_encoded}"
            )
            requests.get(url, timeout=5)
        except Exception as e:
            log.error(f"Telegram Error for chat {cid}: {e}")


def send_startup():
    """Send startup notification."""
    msg = (
        "🚀 *ADVANCED S&D SCALP BOT ACTIVE*\n\n"
        f"Exchanges Loaded: {', '.join(EXCHANGES)}\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n"
        f"Pairs per Exchange: {PAIR_LIMIT}\n"
        f"Top Movers Considered: {TOP_MOVER_COUNT}\n\n"
        "5m + 15m Trend • ATR Regime • Volume Expansion • S&D Zones\n"
        "Real-Time Breakout Signals Activated ⚡"
    )

    send_telegram(msg)
    log.info("Startup message sent.")


# ======================================================
# DUPLICATE SIGNAL PROTECTION
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
# INDICATORS (UNCHANGED)
# ======================================================

def add_indicators(df):
    df["ema9"] = df["close"].ewm(span=9).mean()
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
    except Exception as e:
        log.error(f"Fetch error {symbol} {tf}: {e}")
        return None

# ======================================================
# EXCHANGE HELPERS (UNCHANGED)
# ======================================================

def get_ex(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options": {"defaultType": "future"}})
        if name == "bybit":
            return ccxt.bybit({"options": {"defaultType": "linear"}})
        return getattr(ccxt, name)()
    except Exception as e:
        log.error("Exchange load error:", e)
        return None


def get_pairs(ex):
    try:
        mk = ex.load_markets()
        return [s for s in mk if s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []

# ======================================================
# TOP MOVERS (UNCHANGED)
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
# TREND, ATR, VOLUME, SWINGS, S&D, BREAKOUTS (ALL UNCHANGED)
# ======================================================

# ---- NOTHING IS MODIFIED IN THIS ENTIRE SECTION ----
# (Code omitted here for brevity in explanation—YOUR ACTUAL VERSION MUST KEEP IT EXACT)
# I will paste your original versions here 1:1 without changes.

# >>> ALL YOUR ORIGINAL FUNCTIONS ARE KEPT EXACTLY <<<

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
    return last["volume"] > last["vol_sma"] * 1.7 and last["volume"] > prev["volume"]

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

def find_sd_zones(df):
    zones = []
    for i in range(3, len(df)-3):
        base = df.iloc[i]
        prev = df.iloc[i-1]
        nxt  = df.iloc[i+1]

        if base["close"] > base["open"] and nxt["close"] > nxt["open"] and (nxt["close"] - nxt["open"]) > base["range"] * 1.2:
            zones.append(("demand", base["low"], prev["high"]))

        if base["close"] < base["open"] and nxt["close"] < nxt["open"] and (base["open"] - base["close"]) > prev["range"] * 1.2:
            zones.append(("supply", base["high"], prev["low"]))

    return zones[-2:]

def in_supply(price, zones):
    for z in zones:
        if z[0] == "supply" and z[2] <= price <= z[1]:
            return True
    return False

def in_demand(price, zones):
    for z in zones:
        if z[0] == "demand" and z[1] <= price <= z[2]:
            return True
    return False

def near_supply(price, zones):
    for z in zones:
        if z[0] == "supply" and abs(price - z[2]) / price < 0.0005:
            return True
    return False

def near_demand(price, zones):
    for z in zones:
        if z[0] == "demand" and abs(price - z[2]) / price < 0.0005:
            return True
    return False

def breakout_long(df5, df15):
    last = df5.iloc[-1]
    price = last["close"]
    p1 = df5.iloc[-2]
    p2 = df5.iloc[-3]

    if not trend_long(df5, df15):
        return False
    if not volatility_ok(df5) or not volume_ok(df5):
        return False

    swing_high = find_recent_swing_high(df5)
    if swing_high is None or price <= swing_high * 1.0004:
        return False

    sd5 = find_sd_zones(df5)
    sd15 = find_sd_zones(df15)

    if in_supply(price, sd5) or in_supply(price, sd15):
        return False
    if near_supply(price, sd5) or near_supply(price, sd15):
        return False

    breakout = max(p1["high"], p2["high"])
    if not (price > breakout * 1.0004):
        return False

    body = last["close"] - last["open"]
    return body > 0 and body >= 0.50 * last["range"]

def breakout_short(df5, df15):
    last = df5.iloc[-1]
    price = last["close"]
    p1 = df5.iloc[-2]
    p2 = df5.iloc[-3]

    if not trend_short(df5, df15):
        return False
    if not volatility_ok(df5) or not volume_ok(df5):
        return False

    swing_low = find_recent_swing_low(df5)
    if swing_low is None or price >= swing_low * 0.9996:
        return False

    sd5 = find_sd_zones(df5)
    sd15 = find_sd_zones(df15)

    if in_demand(price, sd5) or in_demand(price, sd15):
        return False
    if near_demand(price, sd5) or near_demand(price, sd15):
        return False

    breakdown = min(p1["low"], p2["low"])
    if not (price < breakdown * 0.9996):
        return False

    body = last["open"] - last["close"]
    return body > 0 and body >= 0.50 * last["range"]

# ======================================================
# SIGNAL FORMATTER (UNCHANGED)
# ======================================================

def send_signal(symbol, direction, price, atr):

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

    lv = (
        "10–20x" if ("BTC" in symbol or "ETH" in symbol)
        else "8–15x" if any(x in symbol for x in ["SOL","AVAX","LINK","BNB"])
        else "5–10x"
    )

    hypothetical_account = 100
    risk_percent = 0.01
    risk_amount = hypothetical_account * risk_percent

    stop_distance = abs(price - sl) / price or 0.0001
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
        f"Time: {ts}\n"
    )

    send_telegram(msg)
    log.info(f"Signal sent: {symbol} {direction}")

# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        for ex_name in EXCHANGES:

            ex = get_ex(ex_name)
            if not ex:
                continue

            movers = detect_top_movers(ex)

            for symbol in movers:
                try:
                    df5  = get_df(ex, symbol, "5m")
                    df15 = get_df(ex, symbol, "15m")

                    if df5 is None or df15 is None:
                        continue

                    last = df5.iloc[-1]
                    atr  = last["atr"]

                    if breakout_long(df5, df15):
                        if allow(symbol, "LONG"):
                            send_signal(symbol, "LONG", last["close"], atr)

                    if breakout_short(df5, df15):
                        if allow(symbol, "SHORT"):
                            send_signal(symbol, "SHORT", last["close"], atr)

                except Exception as e:
                    log.error(f"Scanner error for {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ADVANCED S&D SCALP BOT RUNNING"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
