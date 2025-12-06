import os
import time
import ccxt
import pandas as pd
from flask import Flask, request
import threading
import requests

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 60
PAIR_LIMIT = 50

EXCHANGES = [
    "binance",
    "binance_futures",
    "kucoin",
    "bybit",
    "okx"
]

# ======================================================
# TELEGRAM SENDER
# ======================================================

def send_telegram_message(text, chat_id=None):
    if chat_id is None:
        chat_id = CHAT_ID

    try:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={chat_id}&text={text}"
        )
    except Exception as e:
        print("Telegram error:", e)


# ======================================================
# STARTUP MESSAGE
# ======================================================

def send_startup_message():
    send_telegram_message(
        "✅ HIGH-RETURN BREAKOUT BOT ACTIVE\n"
        "Exchanges: Binance, Binance Futures, KuCoin, Bybit, OKX\n"
        "Timeframe: 5m\n"
        "Duplicate Protection: ENABLED (max 2 per 2h, unique breakout levels only)"
    )


# ======================================================
# ANTI-DUPLICATE SYSTEM
# ======================================================

last_signal_level = {}     # Stores the last breakout level per symbol
signal_times = {}          # Stores timestamps of last two signals
MAX_DUPES = 2
WINDOW = 7200  # 2 hours in seconds

def allow_signal(symbol, breakout_level):
    now = time.time()

    if symbol not in last_signal_level:
        last_signal_level[symbol] = None
    if symbol not in signal_times:
        signal_times[symbol] = []

    # 1. Prevent duplicate at same breakout level
    if last_signal_level[symbol] == breakout_level:
        return False

    # 2. Remove timestamps older than 2 hours
    signal_times[symbol] = [
        ts for ts in signal_times[symbol]
        if now - ts < WINDOW
    ]

    # 3. Allow max 2 messages in 2 hours
    if len(signal_times[symbol]) >= MAX_DUPES:
        return False

    # 4. Accept the signal
    last_signal_level[symbol] = breakout_level
    signal_times[symbol].append(now)
    return True


# ======================================================
# INDICATORS
# ======================================================

def fetch_ohlcv_df(exchange, symbol, timeframe="5m"):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=150)
    df = pd.DataFrame(data, columns=[
        "timestamp","open","high","low","close","volume"
    ])
    return add_indicators(df)


def compute_rsi(series, length=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def compute_atr(df):
    df["H-L"]  = df["high"] - df["low"]
    df["H-PC"] = abs(df["high"] - df["close"].shift(1))
    df["L-PC"] = abs(df["low"] - df["close"].shift(1))
    tr = df[["H-L","H-PC","L-PC"]].max(axis=1)
    return tr.rolling(14).mean()


def add_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"]   = compute_rsi(df["close"])
    df["atr"]   = compute_atr(df)
    df["vol_sma"] = df["volume"].rolling(20).mean()
    return df


# ======================================================
# BREAKOUT LOGIC
# ======================================================

def check_breakout_long(df):
    last = df.iloc[-1]
    p1 = df.iloc[-2]
    p2 = df.iloc[-3]

    # ATR Explosion
    if not (last["atr"] >= p1["atr"] * 1.20):
        return False

    # Volume Expansion
    if not (last["volume"] > last["vol_sma"] * 1.5):
        return False

    # Break above last 3 highs
    breakout_lvl = max(p1["high"], p2["high"], df.iloc[-4]["high"])
    if not (last["close"] > breakout_lvl):
        return False

    # Power Candle
    body = last["close"] - last["open"]
    rng = last["high"] - last["low"]
    if not (body > 0 and body >= 0.65 * rng):
        return False

    return True


def check_breakout_short(df):
    last = df.iloc[-1]
    p1 = df.iloc[-2]
    p2 = df.iloc[-3]

    # ATR Explosion
    if not (last["atr"] >= p1["atr"] * 1.20):
        return False

    # Volume Expansion
    if not (last["volume"] > last["vol_sma"] * 1.5):
        return False

    # Break below last 3 lows
    breakdown_lvl = min(p1["low"], p2["low"], df.iloc[-4]["low"])
    if not (last["close"] < breakdown_lvl):
        return False

    # Power Candle
    body = last["open"] - last["close"]
    rng = last["high"] - last["low"]
    if not (body > 0 and body >= 0.65 * rng):
        return False

    return True


# ======================================================
# SIGNAL SENDER
# ======================================================

def send_signal(symbol, direction, price, atr):

    if direction == "LONG":
        sl  = price - (2 * atr)
        tp1 = price + (2 * atr)
        tp2 = price + (4 * atr)
        tp3 = price + (6 * atr)
        tp4 = price + (10 * atr)
    else:
        sl  = price + (2 * atr)
        tp1 = price - (2 * atr)
        tp2 = price - (4 * atr)
        tp3 = price - (6 * atr)
        tp4 = price - (10 * atr)

    msg = (
        f"🔥 HIGH-RETURN {direction} BREAKOUT\n\n"
        f"Pair: {symbol}\n"
        f"Entry: {price}\n"
        f"ATR: {round(atr,4)}\n\n"
        f"SL:  {round(sl,4)}\n"
        f"TP1: {round(tp1,4)}\n"
        f"TP2: {round(tp2,4)}\n"
        f"TP3: {round(tp3,4)}\n"
        f"TP4: {round(tp4,4)}\n"
        f"⚠ Informational only."
    )

    send_telegram_message(msg)
    print("SIGNAL →", symbol, direction)


# ======================================================
# EXCHANGE HELPERS
# ======================================================

def get_exchange(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options":{"defaultType":"future"}})
        if name == "bybit":
            return ccxt.bybit({"options":{"defaultType":"linear"}})
        return getattr(ccxt, name)()
    except:
        return None


def fetch_usdt_pairs(exchange):
    try:
        markets = exchange.load_markets()
        return [s for s in markets if s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []


# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():

    send_startup_message()

    while True:
        try:
            for ex_name in EXCHANGES:

                ex = get_exchange(ex_name)
                if ex is None:
                    continue

                for symbol in fetch_usdt_pairs(ex):

                    try:
                        df = fetch_ohlcv_df(ex, symbol, "5m")
                        last = df.iloc[-1]

                        # LONG BREAKOUT
                        if check_breakout_long(df):
                            breakout_lvl = max(df.iloc[-2]["high"], df.iloc[-3]["high"], df.iloc[-4]["high"])
                            if allow_signal(symbol, breakout_lvl):
                                send_signal(symbol, "LONG", last["close"], last["atr"])

                        # SHORT BREAKOUT
                        if check_breakout_short(df):
                            breakdown_lvl = min(df.iloc[-2]["low"], df.iloc[-3]["low"], df.iloc[-4]["low"])
                            if allow_signal(symbol, breakdown_lvl):
                                send_signal(symbol, "SHORT", last["close"], last["atr"])

                    except Exception as err:
                        print("Error:", symbol, err)

            time.sleep(SCAN_INTERVAL)

        except Exception as err:
            print("Scanner crashed:", err)
            time.sleep(10)


# ======================================================
# TELEGRAM WEBHOOK
# ======================================================

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return "OK"

    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text","")

    if text == "/start":
        send_telegram_message("Bot is online and scanning markets.", chat_id)

    elif text == "/status":
        send_telegram_message("📡 Bot Running Smoothly.", chat_id)

    elif text == "/help":
        send_telegram_message(
            "/start — Activate Bot\n"
            "/status — Bot Status\n"
            "/help — Commands",
            chat_id
        )

    return "OK"


# ======================================================
# START THREAD
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# RENDER SERVER
# ======================================================

@app.route("/")
def home():
    return "HIGH-RETURN BREAKOUT BOT RUNNING"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
