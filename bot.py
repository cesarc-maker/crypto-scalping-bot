# ======================================================
# STRICT MOMENTUM LIMIT BOT (STRUCTURE PRESERVED)
# ======================================================

import os
import time
import ccxt
import pandas as pd
import threading
import requests
import logging
from flask import Flask
from datetime import datetime, timezone

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("STRICTBOT")

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
    for c in RAW_CHAT_IDS.split(","):
        if c.strip():
            CHAT_IDS.add(c.strip())
CHAT_IDS = list(CHAT_IDS)

PORT = int(os.getenv("PORT", 10000))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))
WINDOW = int(os.getenv("WINDOW", 1800))

EXCHANGES = ["binance", "binance_futures", "bybit", "kucoin", "okx"]

recent_signals = {}

# ======================================================
# TELEGRAM
# ======================================================

def send_telegram(text: str):
    if not BOT_TOKEN or not CHAT_IDS:
        log.warning("Telegram not configured")
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
        "🚀 STRICT LIMIT SIGNAL BOT ONLINE\n\n"
        "Mode: Strict (Limit + Scalp Only)\n"
        "Risk: Auto-classified (LOW / MEDIUM / HIGH)\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n"
        f"Scan Interval: {SCAN_INTERVAL}s\n\n"
        "Momentum-confirmed • Limit execution • Informational only"
    )
    send_telegram(msg)
    log.info(f"Startup message sent → {CHAT_IDS}")

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
# RISK / CLASSIFICATION HELPERS
# ======================================================

def stop_pct(entry, stop):
    return abs(entry - stop) / entry * 100

def trade_type_from_stop(pct):
    if pct <= 0.25:
        return "SCALP"
    if pct <= 1.0:
        return "LIMIT"
    return None

def leverage_from_stop(pct):
    if pct < 0.30: return 100
    if pct < 0.60: return 50
    if pct < 1.00: return 25
    return 15

def risk_from_leverage(lv):
    if lv <= 20: return "LOW"
    if lv <= 40: return "MEDIUM"
    return "HIGH"

# ======================================================
# SIGNAL SENDER (UPGRADED ONLY)
# ======================================================

def send_signal(symbol, side, entry, stop, tps):
    pct = stop_pct(entry, stop)
    trade_type = trade_type_from_stop(pct)
    if not trade_type:
        return

    lv = leverage_from_stop(pct)
    risk = risk_from_leverage(lv)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    msg = (
        f"📌 {trade_type} {side}\n\n"
        f"Pair: {symbol}\n"
        f"Entry: {round(entry,6)}\n"
        f"Stop: {round(stop,6)}\n\n"
    )

    for i, tp in enumerate(tps, 1):
        msg += f"TP{i}: {round(tp,6)}\n"

    msg += (
        f"\nLeverage: {lv}x\n"
        f"Risk Level: {risk}\n"
        f"Time: {ts}"
    )

    send_telegram(msg)
    log.info(f"Signal sent → {symbol} {side} {trade_type}")

# ======================================================
# PLACEHOLDER EXECUTION (HOOK YOUR EXISTING LOGIC HERE)
# ======================================================

def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        # ⬇️ THIS IS WHERE YOUR EXISTING BREAKOUT LOGIC PLUGS IN ⬇️
        # For demo, static example:

        symbol = "SOL/USDT"
        price = 142.0
        stop = 141.6
        tps = [143.2, 144.5, 147.0]

        if allow(symbol, "LONG"):
            send_signal(symbol, "LONG", price, stop, tps)

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "STRICT LIMIT SIGNAL BOT RUNNING"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
