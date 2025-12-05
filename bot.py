import asyncio
import json
import aiohttp
from aiohttp import ClientSession
import ccxt
import pandas as pd
import numpy as np
import time
import threading
from flask import Flask
import requests

# ================================
# CONFIG
# ================================
TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

TIMEFRAME_1M = "1"
TIMEFRAME_5M = "5"

# How long to keep historical candles in memory
MAX_CANDLES = 300

# ================================
# TELEGRAM HELPER
# ================================
def tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})


# ================================
# STRICT BLOFIN FILTER
# ================================
blofin = ccxt.blofin()

def load_blofin_pairs():
    markets = blofin.load_markets()
    valid = set()
    for symbol, data in markets.items():
        if not symbol.endswith("USDT"): 
            continue
        if data.get("type") != "spot":
            continue
        if not data.get("active", False):
            continue
        if data.get("limits", {}).get("amount", {}).get("min") is None:
            continue

        base = data.get("base", "").upper()
        if any(x in base for x in ["3L","3S","5L","5S","UP","DOWN"]):
            continue

        valid.add(symbol.replace("/", ""))  # Convert to BTCUSDT style
    return valid

BLOFIN_PAIRS = load_blofin_pairs()
print(f"Loaded {len(BLOFIN_PAIRS)} Blofin-valid symbols")

# ================================
# INDICATOR UTILS
# ================================
def ema(values, length):
    return pd.Series(values).ewm(span=length, adjust=False).mean().values

def true_range(h, l, c):
    tr = []
    for i in range(1, len(c)):
        tr.append(max(
            h[i] - l[i],
            abs(h[i] - c[i-1]),
            abs(l[i] - c[i-1])
        ))
    return np.array(tr)

def atr(h, l, c, period=14):
    tr = true_range(h, l, c)
    return pd.Series(tr).rolling(period).mean().values


# ================================
# DATA STORAGE
# ================================
candles_1m = {}  # symbol -> list of candles (dicts)
candles_5m = {}

def append_candle(store, symbol, candle):
    if symbol not in store:
        store[symbol] = []
    store[symbol].append(candle)
    if len(store[symbol]) > MAX_CANDLES:
        store[symbol] = store[symbol][-MAX_CANDLES:]


# ================================
# BYBIT WEBSOCKET STREAM
# ================================
BYBIT_WS = "wss://stream.bybit.com/v5/public/spot"

async def subscribe_ws(session, symbols):
    async with session.ws_connect(BYBIT_WS) as ws:
        # Subscribe to 1m and 5m streams
        subs = []
        for sym in symbols:
            subs.append({"op": "subscribe", "args": [f"kline.{TIMEFRAME_1M}.{sym}"]})
            subs.append({"op": "subscribe", "args": [f"kline.{TIMEFRAME_5M}.{sym}"]})

        for sub in subs:
            await ws.send_json(sub)
            await asyncio.sleep(0.02)

        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                asyncio.create_task(handle_ws(data))


# ================================
# HANDLE WEBSOCKET MESSAGE
# ================================
async def handle_ws(data):
    if "topic" not in data or "data" not in data:
        return

    topic = data["topic"]
    parts = topic.split(".")
    tf = parts[1]
    symbol = parts[2]

    k = data["data"][0]

    candle = {
        "time": int(k["start"]),
        "open": float(k["open"]),
        "high": float(k["high"]),
        "low": float(k["low"]),
        "close": float(k["close"]),
        "volume": float(k["volume"])
    }

    if tf == TIMEFRAME_1M:
        append_candle(candles_1m, symbol, candle)
        await evaluate(symbol)
    elif tf == TIMEFRAME_5M:
        append_candle(candles_5m, symbol, candle)


# ================================
# STRATEGY LOGIC (EARLY 1M BREAKOUT)
# ================================
async def evaluate(symbol):
    if symbol not in candles_1m or symbol not in candles_5m:
        return
    if len(candles_1m[symbol]) < 50 or len(candles_5m[symbol]) < 50:
        return

    # --- EXTRACT DATA ---
    df1 = pd.DataFrame(candles_1m[symbol])
    df5 = pd.DataFrame(candles_5m[symbol])

    c1 = df1["close"].values
    h1 = df1["high"].values
    l1 = df1["low"].values
    v1 = df1["volume"].values

    c5 = df5["close"].values
    h5 = df5["high"].values
    l5 = df5["low"].values
    v5 = df5["volume"].values

    # TREND FILTER (5M)
    ema20 = ema(c5, 20)
    ema50 = ema(c5, 50)

    trend_long = ema20[-1] > ema50[-1]
    trend_short = ema20[-1] < ema50[-1]

    # ATR (5M)
    atr14_5m = atr(h5, l5, c5, 14)
    atr_val = atr14_5m[-1]

    # Volume SMA20 (1M)
    vol_sma20 = pd.Series(v1).rolling(20).mean().values

    # Power Candle (1M)
    body = df1["close"] - df1["open"]
    rng = df1["high"] - df1["low"]
    power = (abs(body) / rng) > 0.65

    # ATR Explosion (using 5m)
    atr_exp = atr14_5m[-1] >= atr14_5m[-2] * 1.20

    # Volume Expansion (1m)
    vol_exp = v1[-1] > vol_sma20[-1] * 1.5

    # Microstructure breakout (1m)
    last3high = max(h1[-4:-1])
    last3low = min(l1[-4:-1])
    close_now = c1[-1]

    # --- LONG ---
    if (
        close_now > last3high and
        power.iloc[-1] and
        atr_exp and
        vol_exp and
        trend_long
    ):
        send_signal(symbol, "LONG", close_now, atr_val)

    # --- SHORT ---
    if (
        close_now < last3low and
        power.iloc[-1] and
        atr_exp and
        vol_exp and
        trend_short
    ):
        send_signal(symbol, "SHORT", close_now, atr_val)


# ================================
# SEND SIGNAL
# ================================
def send_signal(symbol, side, price, atr):
    msg = (
        f"🚨 BREAKOUT SIGNAL\n"
        f"Symbol: {symbol}\n"
        f"Side: {side}\n"
        f"Price: {price:.4f}\n"
        f"ATR (5m): {atr:.4f}\n\n"
        f"🎯 TP1: {price + (2*atr) if side=='LONG' else price - (2*atr):.4f}\n"
        f"🎯 TP2: {price + (4*atr) if side=='LONG' else price - (4*atr):.4f}\n"
        f"🎯 TP3: {price + (6*atr) if side=='LONG' else price - (6*atr):.4f}\n"
        f"🎯 TP4: {price + (10*atr) if side=='LONG' else price - (10*atr):.4f}\n\n"
        f"🛑 SL: {price - (2*atr) if side=='LONG' else price + (2*atr):.4f}"
    )
    tg(msg)


# ================================
# MAIN ASYNC LOOP
# ================================
async def main():
    symbols = list(BLOFIN_PAIRS)
    async with ClientSession() as session:
        await subscribe_ws(session, symbols)


# ================================
# FLASK FOR RENDER
# ================================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot running (Websocket + Async + Breakout Engine)"


def start_async():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())


threading.Thread(target=start_async, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
