import asyncio
import json
import aiohttp
import pandas as pd
import numpy as np
import ccxt
from flask import Flask
import requests
import threading

# ==============================================================
# CONFIG
# ==============================================================

TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/spot"

MAX_CANDLES = 300  # keep memory small
TIMEFRAME_1M = "1"
TIMEFRAME_5M = "5"

# ============================================================== 
# TELEGRAM
# ==============================================================

def tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})


# ==============================================================
# STRICT BLOFIN FILTER
# ==============================================================

blofin = ccxt.blofin()

def load_blofin_pairs():
    markets = blofin.load_markets()
    valid = set()

    for symbol, info in markets.items():
        if not symbol.endswith("USDT"):
            continue
        if info.get("type") != "spot":
            continue
        if not info.get("active", False):
            continue
        if info.get("limits", {}).get("amount", {}).get("min") is None:
            continue
        
        base = info.get("base", "").upper()
        if any(x in base for x in ["3L","3S","5L","5S","UP","DOWN"]):
            continue
        
        valid.add(symbol.replace("/", ""))  # convert to BTCUSDT format

    return valid

BLOFIN_PAIRS = load_blofin_pairs()
print("Loaded Blofin Symbols:", len(BLOFIN_PAIRS))


# ==============================================================
# INDICATOR UTILS
# ==============================================================

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


# ==============================================================
# CANDLE DATA STORE
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
# BREAKOUT EVALUATION
# ==============================================================

async def evaluate(symbol):
    if symbol not in candles_1m or symbol not in candles_5m:
        return
    if len(candles_1m[symbol]) < 40 or len(candles_5m[symbol]) < 40:
        return

    df1 = pd.DataFrame(candles_1m[symbol])
    df5 = pd.DataFrame(candles_5m[symbol])

    c1 = df1["close"].values
    h1 = df1["high"].values
    l1 = df1["low"].values
    v1 = df1["volume"].values

    c5 = df5["close"].values
    h5 = df5["high"].values
    l5 = df5["low"].values

    # =======================================================
    # TREND FILTER (5m)
    # =======================================================
    ema20 = ema(c5, 20)
    ema50 = ema(c5, 50)

    trend_long = ema20[-1] > ema50[-1]
    trend_short = ema20[-1] < ema50[-1]

    # =======================================================
    # ATR EXPLOSION (5m)
    # =======================================================
    atr_val = atr(h5, l5, c5, 14)[-1]
    atr_prev = atr(h5, l5, c5, 14)[-2]
    atr_exp = atr_val >= atr_prev * 1.20

    # =======================================================
    # VOLUME EXPANSION (1m)
    # =======================================================
    vol_sma20 = pd.Series(v1).rolling(20).mean().values
    vol_exp = v1[-1] > vol_sma20[-1] * 1.5

    # =======================================================
    # MICRO BREAKOUT (1m)
    # =======================================================
    last3high = max(h1[-4:-1])
    last3low = min(l1[-4:-1])
    close_now = c1[-1]

    # =======================================================
    # POWER CANDLE (1m)
    # =======================================================
    body = df1["close"] - df1["open"]
    rng = df1["high"] - df1["low"]
    power = (abs(body) / rng) > 0.65

    # =======================================================
    # FINAL SIGNALS
    # =======================================================

    # LONG SIGNAL
    if (
        close_now > last3high and
        atr_exp and
        vol_exp and
        power.iloc[-1] and
        trend_long
    ):
        send_signal(symbol, "LONG", close_now, atr_val)

    # SHORT SIGNAL
    if (
        close_now < last3low and
        atr_exp and
        vol_exp and
        power.iloc[-1] and
        trend_short
    ):
        send_signal(symbol, "SHORT", close_now, atr_val)


# ==============================================================
# SEND SIGNAL
# ==============================================================

def send_signal(symbol, side, price, atr_value):
    msg = (
        f"🚨 BREAKOUT SIGNAL\n"
        f"{symbol}\n"
        f"Type: {side}\n"
        f"Entry: {price:.4f}\n"
        f"ATR: {atr_value:.4f}\n\n"
        f"🎯 TP1: {price + 2*atr_value if side=='LONG' else price - 2*atr_value:.4f}\n"
        f"🎯 TP2: {price + 4*atr_value if side=='LONG' else price - 4*atr_value:.4f}\n"
        f"🎯 TP3: {price + 6*atr_value if side=='LONG' else price - 6*atr_value:.4f}\n"
        f"🎯 TP4: {price + 10*atr_value if side=='LONG' else price - 10*atr_value:.4f}\n\n"
        f"🛑 SL: {price - 2*atr_value if side=='LONG' else price + 2*atr_value:.4f}"
    )
    tg(msg)


# ==============================================================
# BYBIT WEBSOCKET HANDLING
# ==============================================================

async def ws_handler():
    symbols = list(BLOFIN_PAIRS)

    # Construct subscription payload
    subs = []
    for s in symbols:
        subs.append(f"kline.{TIMEFRAME_1M}.{s}")
        subs.append(f"kline.{TIMEFRAME_5M}.{s}")

    payload = {
        "op": "subscribe",
        "args": subs
    }

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(BYBIT_WS_URL) as ws:
            await ws.send_json(payload)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await handle_ws(data)


async def handle_ws(message):
    if "topic" not in message or "data" not in message:
        return

    topic = message["topic"]
    parts = topic.split(".")
    tf = parts[1]
    symbol = parts[2]

    k = message["data"][0]

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

    elif tf == TIMEFRAME_5M:
        store_candle(candles_5m, symbol, candle)


# ==============================================================
# ASYNC STARTER
# ==============================================================

def start_async():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(ws_handler())


# ==============================================================
# FLASK (RENDER)
# ==============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Breakout Bot Running (Websocket + Async)"


# Run async bot in a thread so Flask can run too
threading.Thread(target=start_async, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
