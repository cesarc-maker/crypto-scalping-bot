# ======================================================
# RSI MEAN-REVERSION SCALPING BOT (PRO SCALPER VERSION)
# INFORMATIONAL ONLY • QUICK SCALPS • STRICT FILTERS
# STRUCTURE PRESERVED (Render-ready)
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
log = logging.getLogger("RSI_SCALPER")

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", 10000))

SCAN_INTERVAL = 10
TRACK_INTERVAL = 10
WINDOW = 900  # 15 min cooldown per pair+side

PAIR_LIMIT = 40
TOP_MOVER_COUNT = 15

EXCHANGES = ["binance", "bybit"]

recent_signals = {}
open_trades = {}
open_trades_lock = threading.Lock()

TP_ALLOCS = [100]  # scalping = single TP

# ======================================================
# TELEGRAM
# ======================================================

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=8)
    except:
        pass

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

def allow(symbol, side):
    key = f"{symbol}_{side}"
    now = time.time()
    if key not in recent_signals or now - recent_signals[key] > WINDOW:
        recent_signals[key] = now
        return True
    return False

# ======================================================
# INDICATORS
# ======================================================

def add_indicators(df):
    df["ema50"] = df["close"].ewm(span=50).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    df["range"] = df["high"] - df["low"]
    return df

def get_df(ex, symbol, tf):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=100)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
    except:
        return None

# ======================================================
# EXCHANGES
# ======================================================

def get_ex(name):
    try:
        return getattr(ccxt, name)()
    except:
        return None

# ======================================================
# PAIRS
# ======================================================

def get_pairs(ex):
    try:
        mk = ex.load_markets()
        return [s for s in mk if s.endswith("/USDT")][:PAIR_LIMIT]
    except:
        return []

# ======================================================
# RSI SCALPING LOGIC
# ======================================================

def long_setup(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    return (
        prev["rsi"] < 30 and
        last["rsi"] > 30 and
        last["close"] > last["open"] and
        last["close"] >= last["ema50"] and
        (last["high"] - last["close"]) <= 0.5 * last["range"]
    )

def short_setup(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    return (
        prev["rsi"] > 70 and
        last["rsi"] < 70 and
        last["close"] < last["open"] and
        last["close"] <= last["ema50"] and
        (last["close"] - last["low"]) <= 0.5 * last["range"]
    )

# ======================================================
# SIGNAL BUILDER
# ======================================================

def send_signal(ex_name, symbol, side, price):
    stop = price * (0.997 if side == "LONG" else 1.003)
    tp = price * (1.004 if side == "LONG" else 0.996)

    msg = (
        f"📌 RSI SCALP {side}\n\n"
        f"Exchange: {ex_name}\n"
        f"Pair: {symbol}\n"
        f"Entry: {round(price,6)}\n"
        f"Stop: {round(stop,6)}\n"
        f"Target: {round(tp,6)}\n"
        f"RR: ~1:2\n"
        f"Time: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    )

    send_telegram(msg)

    with open_trades_lock:
        open_trades[f"{symbol}_{side}_{time.time()}"] = {
            "symbol": symbol,
            "side": side,
            "entry": price,
            "stop": stop,
            "tp": tp
        }

# ======================================================
# TRACKER LOOP
# ======================================================

def tracker_loop():
    while True:
        time.sleep(TRACK_INTERVAL)
        with open_trades_lock:
            keys = list(open_trades.keys())

        for k in keys:
            t = open_trades.get(k)
            if not t:
                continue

            try:
                ex = get_ex("binance")
                price = ex.fetch_ticker(t["symbol"])["last"]

                if t["side"] == "LONG":
                    if price <= t["stop"]:
                        send_telegram(f"❌ STOP HIT {t['symbol']}")
                        open_trades.pop(k, None)
                    elif price >= t["tp"]:
                        send_telegram(f"✅ TARGET HIT {t['symbol']}")
                        open_trades.pop(k, None)
                else:
                    if price >= t["stop"]:
                        send_telegram(f"❌ STOP HIT {t['symbol']}")
                        open_trades.pop(k, None)
                    elif price <= t["tp"]:
                        send_telegram(f"✅ TARGET HIT {t['symbol']}")
                        open_trades.pop(k, None)
            except:
                pass

# ======================================================
# SCANNER LOOP
# ======================================================

def scanner_loop():
    send_telegram("🚀 RSI MEAN-REVERSION SCALPER STARTED")
    while True:
        for ex_name in EXCHANGES:
            ex = get_ex(ex_name)
            if not ex:
                continue

            for symbol in get_pairs(ex):
                df = get_df(ex, symbol, "1m")
                if df is None or len(df) < 20:
                    continue

                last = df.iloc[-1]

                if long_setup(df) and allow(symbol, "LONG"):
                    send_signal(ex_name, symbol, "LONG", last["close"])

                if short_setup(df) and allow(symbol, "SHORT"):
                    send_signal(ex_name, symbol, "SHORT", last["close"])

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "RSI SCALPING BOT RUNNING"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
