import asyncio
import aiohttp
import json
import pandas as pd
import numpy as np
import ccxt
import time
import requests
from flask import Flask, jsonify
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

HEARTBEAT_INTERVAL = 60  # seconds (Chosen: 1 minute)
START_TIME = time.time()

RECONNECT_COUNT = 0
LAST_10_SIGNALS = []


# ==============================================================
# TELEGRAM
# ==============================================================

def tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except:
        pass


# Send bot started message
tg("🚀 Bot Started and Running on Render!")


# ==============================================================
# BLOFIN FUTURES FILTER (AUTO-DETECT)
# ==============================================================

blofin = ccxt.blofin()

def normalize_symbol(symbol):
    clean = symbol.replace("/", "").replace(":", "").replace("-", "")
    clean = clean.replace("USDTUSDT", "USDT")
    return clean.upper()


def load_blofin_futures():
    markets = blofin.load_markets()
    valid = set()

    for symbol, info in markets.items():
        if info.get("type") not in ["future", "swap"]:
            continue

        if "USDT" not in symbol:
            continue

        if not info.get("active", False):
            continue

        base = info.get("base", "").upper()
        if any(x in base for x in ["3L","3S","5L","5S","UP","DOWN"]):
            continue

        clean = normalize_symbol(symbol)
        valid.add(clean)

    return valid


BLOFIN_PAIRS = load_blofin_futures()
print(f"[INIT] Blofin Futures Loaded: {len(BLOFIN_PAIRS)} symbols")


# ==============================================================
# INDICATOR UTILITIES
# ==============================================================

def ema(arr, length):
    return pd.Series(arr).ewm(span=length, adjust=False).mean().values

def true_range(h, l, c):
    tr = []
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])))
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
# ANTI-DUPLICATE BREAKOUT SYSTEM
# ==============================================================

last_breakout_level = {}
signal_timestamps = {}

MAX_ALERTS_2H = 2
DUPLICATE_WINDOW = 2 * 60 * 60  # 2 HOURS


def allow_signal(symbol, breakout_level):
    now = time.time()

    if symbol not in last_breakout_level:
        last_breakout_level[symbol] = None
    if symbol not in signal_timestamps:
        signal_timestamps[symbol] = []

    # Reject duplicate breakout level
    if last_breakout_level[symbol] == breakout_level:
        return False

    # Clean old timestamps
    signal_timestamps[symbol] = [
        ts for ts in signal_timestamps[symbol]
        if now - ts < DUPLICATE_WINDOW
    ]

    # Reject if exceeded limit
    if len(signal_timestamps[symbol]) >= MAX_ALERTS_2H:
        return False

    # Save new breakout info
    last_breakout_level[symbol] = breakout_level
    signal_timestamps[symbol].append(now)

    return True


# ==============================================================
# SIGNAL SENDER
# ==============================================================

def send_signal(symbol, side, price, atr_val):
    global LAST_10_SIGNALS

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

    LAST_10_SIGNALS.append({
        "time": time.time(),
        "symbol": symbol,
        "side": side,
        "price": price
    })
    LAST_10_SIGNALS = LAST_10_SIGNALS[-10:]

    tg(msg)


# ==============================================================
# BREAKOUT ENGINE
# ==============================================================

async def evaluate(symbol):
    if symbol not in candles_1m or symbol not in candles_5m:
        return
    if len(candles_1m[symbol]) < 40 or len(candles_5m[symbol]) < 40:
        return

    df1 = pd.DataFrame(candles_1m[symbol])
    df5 = pd.DataFrame(candles_5m[symbol])

    c1, h1, l1, v1 = df1["close"].values, df1["high"].values, df1["low"].values, df1["volume"].values
    c5, h5, l5 = df5["close"].values, df5["high"].values, df5["low"].values

    ema20 = ema(c5, 20)
    ema50 = ema(c5, 50)
    trend_long = ema20[-1] > ema50[-1]
    trend_short = ema20[-1] < ema50[-1]

    atr_vals = atr(h5, l5, c5, 14)
    atr_val, atr_prev = atr_vals[-1], atr_vals[-2]
    atr_exp = atr_val >= atr_prev * 1.20

    vol_sma20 = pd.Series(v1).rolling(20).mean().values
    vol_exp = v1[-1] > vol_sma20[-1] * 1.5

    body = df1["close"] - df1["open"]
    rng = df1["high"] - df1["low"]
    power = (abs(body) / rng) > 0.65

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
# WEBSOCKET AUTO-RECONNECT ENGINE
# ==============================================================

async def ws_loop():
    global RECONNECT_COUNT

    while True:
        try:
            print("[WS] Connecting to Bybit…")
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(BYBIT_WS) as ws:

                    subs = []
                    for s in BLOFIN_PAIRS:
                        subs.append(f"kline.{TIMEFRAME_1M}.{s}")
                        subs.append(f"kline.{TIMEFRAME_5M}.{s}")

                    await ws.send_json({"op": "subscribe", "args": subs})
                    print("[WS] Subscribed successfully")

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            await handle_ws(data)

        except Exception as e:
            RECONNECT_COUNT += 1
            print(f"[WS ERROR] {e}")
            print("[WS] Reconnecting in 3 seconds…")
            await asyncio.sleep(3)


async def handle_ws(data):
    if "topic" not in data or "data" not in data:
        return

    topic = data["topic"]
    tf = topic.split(".")[1]
    symbol = topic.split(".")[2]

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
        store_candle(candles_1m, symbol, candle)
        await evaluate(symbol)

    elif tf == TIMEFRAME_5M:
        store_candle(candles_5m, symbol, candle)


# ==============================================================
# HEARTBEAT LOGGER (EVERY 60 SECONDS)
# ==============================================================

async def heartbeat_loop():
    while True:
        uptime = int(time.time() - START_TIME)
        signals_2h = sum(
            len(ts) for ts in signal_timestamps.values()
        )

        print(
            f"\n[HEARTBEAT] OK\n"
            f"Uptime: {uptime//60}m\n"
            f"Symbols loaded: {len(BLOFIN_PAIRS)}\n"
            f"Signals fired (last 2h): {signals_2h}\n"
            f"Reconnects: {RECONNECT_COUNT}\n"
        )
        await asyncio.sleep(HEARTBEAT_INTERVAL)


# ==============================================================
# MAIN ASYNC LAUNCHER
# ==============================================================

def start_async():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(ws_loop())
    loop.create_task(heartbeat_loop())
    loop.run_forever()


# ==============================================================
# FLASK DASHBOARD
# ==============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Breakout Bot Running (Async + WS + Anti-Dup + Dashboard)"

@app.route("/status")
def status():
    uptime = int(time.time() - START_TIME)
    return jsonify({
        "uptime_minutes": uptime // 60,
        "blofin_symbols": len(BLOFIN_PAIRS),
        "last_signals": LAST_10_SIGNALS,
        "reconnects": RECONNECT_COUNT,
        "active_1m_symbols": len(candles_1m),
        "active_5m_symbols": len(candles_5m)
    })


# Start async engine
threading.Thread(target=start_async, daemon=True).start()


# Flask server for Render
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
