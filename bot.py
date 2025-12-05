import asyncio
import aiohttp
import json
import pandas as pd
import numpy as np
import ccxt
import time
import requests
from flask import Flask
import threading

# ==============================================================
# CONFIGURATION
# ==============================================================

TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

BYBIT_WS = "wss://stream.bybit.com/v5/public/spot"
MAX_CANDLES = 300

TIMEFRAME_1M = "1"
TIMEFRAME_5M = "5"

# Anti-duplicate settings
MAX_ALERTS_2H = 2
DUPLICATE_WINDOW_SECONDS = 2 * 60 * 60  # 2 hours

last_breakout_level = {}     # symbol → last structure breakout price
signal_timestamps = {}       # symbol → list of timestamps


# ==============================================================
# TELEGRAM SENDER
# ==============================================================

def tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except:
        pass


# ==============================================================
# STRICT BLOFIN FUTURES FILTER (AUTO-DETECT FORMAT)
# ==============================================================

blofin = ccxt.blofin()

def normalize_symbol(symbol):
    """
    Converts Blofin weird formats into BTCUSDT, ETHUSDT, etc.
    """
    clean = symbol.replace("/", "").replace(":", "").replace("-", "")
    clean = clean.replace("USDTUSDT", "USDT")
    return clean.upper()


def load_blofin_futures():
    markets = blofin.load_markets()
    valid = set()

    for symbol, info in markets.items():

        # Must be futures or swap
        if info.get("type") not in ["future", "swap"]:
            continue

        # Must relate to USDT
        if "USDT" not in symbol:
            continue

        # Must be active
        if not info.get("active", False):
            continue

        # Remove leveraged tokens
        base = info.get("base", "").upper()
        if any(x in base for x in ["3L","3S","5L","5S","UP","DOWN"]):
            continue

        clean = normalize_symbol(symbol)
        valid.add(clean)

    return valid


BLOFIN_PAIRS = load_blofin_futures()
print(f"[INIT] Loaded Blofin Futures Pairs: {len(BLOFIN_PAIRS)}")


# ==============================================================
# INDICATOR UTILITIES
# ==============================================================

def ema(arr, length):
    return pd.Series(arr).ewm(span=length, adjust=False).mean().values

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


# ==============================================================
# CANDLE STORAGE
# ==============================================================

candles_1m = {}
candles_5m = {}

def store_candle(store, symbol, candle):
    if symbol not in store:
        store[symbol] = []
    store[symbol].append(candle)
    if len(store[symbol]) > MAX_CANDLES:
        store[symbol] = store[symbol][-MAX_CANDLES:]


# ==============================================================
# ANTI-DUPLICATE BREAKOUT LOGIC (OPTION C)
# ==============================================================

def allow_signal(symbol, breakout_level):
    """
    Only allow signal if:
    1. Breakout level != last_breakout_level
    2. Max 2 signals in 2 hours
    """
    now = time.time()

    # Initialize
    if symbol not in signal_timestamps:
        signal_timestamps[symbol] = []
    if symbol not in last_breakout_level:
        last_breakout_level[symbol] = None

    # Check breakout level difference
    if last_breakout_level[symbol] == breakout_level:
        return False  # same structure level

    # Clean timestamps older than 2 hours
    signal_timestamps[symbol] = [
        ts for ts in signal_timestamps[symbol]
        if now - ts < DUPLICATE_WINDOW_SECONDS
    ]

    # Check limit
    if len(signal_timestamps[symbol]) >= MAX_ALERTS_2H:
        return False

    # Passed checks → store info
    last_breakout_level[symbol] = breakout_level
    signal_timestamps[symbol].append(now)

    return True


# ==============================================================
# SEND BREAKOUT SIGNAL
# ==============================================================

def send_signal(symbol, side, price, atr_val):
    msg = (
        f"🚨 BREAKOUT SIGNAL\n"
        f"Symbol: {symbol}\n"
        f"Side: {side}\n"
        f"Entry: {price:.4f}\n"
        f"ATR(5m): {atr_val:.4f}\n\n"
        f"🎯 TP1: {(price + 2*atr_val) if side=='LONG' else (price - 2*atr_val):.4f}\n"
        f"🎯 TP2: {(price + 4*atr_val) if side=='LONG' else (price - 4*atr_val):.4f}\n"
        f"🎯 TP3: {(price + 6*atr_val) if side=='LONG' else (price - 6*atr_val):.4f}\n"
        f"🎯 TP4: {(price +10*atr_val) if side=='LONG' else (price -10*atr_val):.4f}\n\n"
        f"🛑 SL: {(price - 2*atr_val) if side=='LONG' else (price + 2*atr_val):.4f}"
    )
    tg(msg)


# ==============================================================
# BREAKOUT ENGINE (EVALUATE ON EVERY 1M CANDLE)
# ==============================================================

async def evaluate(symbol):
    if symbol not in candles_1m or symbol not in candles_5m:
        return
    if len(candles_1m[symbol]) < 40 or len(candles_5m[symbol]) < 40:
        return

    df1 = pd.DataFrame(candles_1m[symbol])
    df5 = pd.DataFrame(candles_5m[symbol])

    # Extract arrays
    c1, h1, l1, v1 = df1["close"].values, df1["high"].values, df1["low"].values, df1["volume"].values
    c5, h5, l5 = df5["close"].values, df5["high"].values, df5["low"].values

    # Trend Filter (5m)
    ema20 = ema(c5, 20)
    ema50 = ema(c5, 50)
    trend_long = ema20[-1] > ema50[-1]
    trend_short = ema20[-1] < ema50[-1]

    # ATR Explosion (5m)
    atr_vals = atr(h5, l5, c5, 14)
    atr_val = atr_vals[-1]
    atr_prev = atr_vals[-2]
    atr_exp = atr_val >= atr_prev * 1.20

    # Volume Expansion (1m)
    vol_sma20 = pd.Series(v1).rolling(20).mean().values
    vol_exp = v1[-1] > vol_sma20[-1] * 1.5

    # Power Candle (1m)
    body = df1["close"] - df1["open"]
    rng = df1["high"] - df1["low"]
    power = (abs(body) / rng) > 0.65

    # Microstructure breakout (1m)
    last3high = max(h1[-4:-1])
    last3low = min(l1[-4:-1])
    close_now = c1[-1]

    # ===========================
    # LONG BREAKOUT
    # ===========================
    if close_now > last3high and atr_exp and vol_exp and power.iloc[-1] and trend_long:
        
        breakout_level = last3high
        
        if allow_signal(symbol, breakout_level):
            send_signal(symbol, "LONG", close_now, atr_val)

    # ===========================
    # SHORT BREAKOUT
    # ===========================
    if close_now < last3low and atr_exp and vol_exp and power.iloc[-1] and trend_short:
        
        breakout_level = last3low
        
        if allow_signal(symbol, breakout_level):
            send_signal(symbol, "SHORT", close_now, atr_val)


# ==============================================================
# WEBSOCKET HANDLER
# ==============================================================

async def handle_ws_message(msg):
    if "topic" not in msg or "data" not in msg:
        return

    topic = msg["topic"]
    parts = topic.split(".")
    tf = parts[1]      # "1" or "5"
    symbol = parts[2]  # ex: BTCUSDT

    k = msg["data"][0]

    candle = {
        "time": int(k["start"]),
        "open": float(k["open"]),
        "high": float(k["high"]),
        "low": float(k["low"]),
        "close": float(k["close"]),
        "volume": float(k["volume"])
    }

    if tf == TIMEFRAME_1M:
        store_candle(candles_1m, symbol, candle)
        await evaluate(symbol)

    if tf == TIMEFRAME_5M:
        store_candle(candles_5m, symbol, candle)


async def ws_loop():
    subs = []
    for s in BLOFIN_PAIRS:
        subs.append(f"kline.{TIMEFRAME_1M}.{s}")
        subs.append(f"kline.{TIMEFRAME_5M}.{s}")

    payload = {"op": "subscribe", "args": subs}

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BYBIT_WS) as ws:
            await ws.send_json(payload)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await handle_ws_message(data)


# ==============================================================
# START ASYNC LOOP
# ==============================================================

def start_async():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ws_loop())


# ==============================================================
# FLASK SERVER (REQUIRED FOR RENDER)
# ==============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Breakout Bot is Running (Async + Websocket + Futures + Anti-Repeats)"


threading.Thread(target=start_async, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
