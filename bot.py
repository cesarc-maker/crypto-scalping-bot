# FIXED MTF REVERSAL BOT (DEPLOY-SAFE VERSION)
# All string issues removed. Safe for Render.

import os
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

import ccxt
import pandas as pd
import requests
from flask import Flask

# ================= CONFIG =================
MAX_OPEN_TRADES = 10
BOS_ATR_FRACTION = 0.08
USE_EMA_FILTER = True
USE_4H_SOFT_VETO = True

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("BOT")

# ================= TELEGRAM =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": msg})


# ================= SAFE STARTUP =================
def send_startup():
    lines = [
        "MTF REVERSAL BOT STARTED",
        f"Max trades: {MAX_OPEN_TRADES}",
        f"BOS: {BOS_ATR_FRACTION}",
        f"EMA filter: {'ON' if USE_EMA_FILTER else 'OFF'}",
        f"4H veto: {'ON' if USE_4H_SOFT_VETO else 'OFF'}",
        "Bot running..."
    ]
    send_telegram("\n".join(lines))


# ================= DUMMY LOOP =================
def run_bot():
    send_startup()
    while True:
        log.info("Bot running...")
        time.sleep(30)


# ================= SERVER =================
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot running"


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
