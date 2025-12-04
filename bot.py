import os
import time
import ccxt
import pandas as pd
from flask import Flask, request
import threading
import requests


# ======================================================
# CONFIGURATION
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
# TELEGRAM ALERT
# ======================================================

def send_telegram_message(text, chat_id=None):
    if chat_id is None:
        chat_id = CHAT_ID
    try:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={chat_id}&text={text}"
        )
    except:
        pass


# ======================================================
# OHLCV + INDICATORS
# ======================================================

def fetch_ohlcv_df(exchange, symbol, timeframe):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=150)
    df = pd.DataFrame(
        data,
        columns=["timestamp","open","high","low","close","volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def compute_rsi(series, length=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100/(1+rs))


def compute_atr(df):
    df["H-L"] = df["high"] - df["low"]
    df["H-PC"] = abs(df["high"] - df["close"].shift(1))
    df["L-PC"] = abs(df["low"] - df["close"].shift(1))
    tr = df[["H-L","H-PC","L-PC"]].max(axis=1)
    return tr.rolling(14).mean()


def add_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df)
    return df


# ======================================================
# TREND DETECTION
# ======================================================

def detect_trend(df):
    last = df.iloc[-1]
    if last["ema20"] > last["ema50"]:
        return "UP"
    if last["ema20"] < last["ema50"]:
        return "DOWN"
    return "NONE"


def detect_trend_1h(df):
    last = df.iloc[-1]
    if last["ema20"] > last["ema50"]:
        return "UP"
    if last["ema20"] < last["ema50"]:
        return "DOWN"
    return "NONE"


# ======================================================
# SUPPORT / RESISTANCE (SWING POINTS)
# ======================================================

def find_recent_swing_high(df, lookback=12):
    return df.tail(lookback)["high"].max()

def find_recent_swing_low(df, lookback=12):
    return df.tail(lookback)["low"].min()


# ======================================================
# STRICT SMART-MONEY LONG LOGIC
# ======================================================

def check_long_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    prev2 = df5.iloc[-3]

    # 1️⃣ LIQUIDITY SWEEP (stop-hunt)
    swept_low = last["low"] < prev["low"]
    reclaimed = last["close"] > last["ema20"]
    liquidity_sweep_ok = swept_low and reclaimed

    # 2️⃣ EMA20 acceleration
    ema_slope = (last["ema20"] - prev["ema20"]) > (prev["ema20"] - prev2["ema20"]) * 1.2

    # 3️⃣ Breakout beyond prior swing high
    swing_high = find_recent_swing_high(df5)
    breakout_ok = last["close"] > swing_high * 1.001

    # 4️⃣ Clear close above EMA20 (0.3% buffer)
    ema_position = last["close"] > last["ema20"] * 1.003

    # 5️⃣ RSI momentum
    rsi_ok = 55 < last["rsi"] < 70

    # 6️⃣ Strong bullish candle body
    body = last["close"] - last["open"]
    range_ = last["high"] - last["low"]
    bullish_strong = body > 0 and body > 0.7 * range_

    # 7️⃣ ATR expansion
    atr_ok = last["atr"] > prev["atr"] * 1.15

    # 8️⃣ Expected move potential
    expected_ok = last["atr"] * 3 < last["close"] * 0.02

    return (
        liquidity_sweep_ok
        and ema_slope
        and breakout_ok
        and ema_position
        and rsi_ok
        and bullish_strong
        and atr_ok
        and expected_ok
    )


# ======================================================
# STRICT SMART-MONEY SHORT LOGIC
# ======================================================

def check_short_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    prev2 = df5.iloc[-3]

    # 1️⃣ LIQUIDITY SWEEP
    swept_high = last["high"] > prev["high"]
    rejected = last["close"] < last["ema20"]
    liquidity_sweep_ok = swept_high and rejected

    # 2️⃣ EMA20 acceleration downward
    ema_slope = (prev["ema20"] - last["ema20"]) > (prev2["ema20"] - prev["ema20"]) * 1.2

    # 3️⃣ Breakdown below swing low
    swing_low = find_recent_swing_low(df5)
    breakdown_ok = last["close"] < swing_low * 0.999

    # 4️⃣ Clear move below EMA20
    ema_position = last["close"] < last["ema20"] * 0.997

    # 5️⃣ RSI momentum
    rsi_ok = 30 < last["rsi"] < 45

    # 6️⃣ Strong bearish candle body
    body = last["open"] - last["close"]
    range_ = last["high"] - last["low"]
    bearish_strong = body > 0 and body > 0.7 * range_

    # 7️⃣ ATR expansion
    atr_ok = last["atr"] > prev["atr"] * 1.15

    # 8️⃣ Expected move potential
    expected_ok = last["atr"] * 3 < last["close"] * 0.02

    return (
        liquidity_sweep_ok
        and ema_slope
        and breakdown_ok
        and ema_position
        and rsi_ok
        and bearish_strong
        and atr_ok
        and expected_ok
    )


# ======================================================
# EXCHANGES
# ======================================================

def get_exchange(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options": {"defaultType":"future"}})
        if name == "bybit":
            return ccxt.bybit({"options": {"defaultType":"linear"}})
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
# SIGNAL DISPATCH
# ======================================================

def send_signal(symbol, direction, price, atr):

    atr = float(atr)

    if direction == "LONG":
        sl = price - (1.5 * atr)
        tp1 = price + atr
        tp2 = price + (2 * atr)
        tp3 = price + (3 * atr)
    else:
        sl = price + (1.5 * atr)
        tp1 = price - atr
        tp2 = price - (2 * atr)
        tp3 = price - (3 * atr)

    msg = (
        f"🔥 ADVANCED {direction} Signal\n\n"
        f"Pair: {symbol}\n"
        f"Price: {price}\n"
        f"ATR: {round(atr,4)}\n\n"
        f"SL: {round(sl,4)}\n"
        f"TP1: {round(tp1,4)}\n"
        f"TP2: {round(tp2,4)} (1:2 RR)\n"
        f"TP3: {round(tp3,4)}\n\n"
        f"⚠️ Informational only."
    )

    send_telegram_message(msg)
    print(f"Sent SIGNAL → {symbol} {direction}")


# ======================================================
# SCANNER LOOP
# ======================================================

def scanner_loop():
    send_telegram_message("🚀 ADVANCED SMC Bot Activated")

    while True:
        try:
            for ex_name in EXCHANGES:

                ex = get_exchange(ex_name)
                if ex is None:
                    continue

                for symbol in fetch_usdt_pairs(ex):

                    try:
                        df5 = add_indicators(fetch_ohlcv_df(ex, symbol, "5m"))
                        df15 = add_indicators(fetch_ohlcv_df(ex, symbol, "15m"))
                        df1h = add_indicators(fetch_ohlcv_df(ex, symbol, "1h"))

                        trend15 = detect_trend(df15)
                        trend1h = detect_trend_1h(df1h)

                        last5 = df5.iloc[-1]

                        # LONG: 5m + 15m + 1h all UP
                        if (
                            trend15 == "UP"
                            and trend1h == "UP"
                            and check_long_setup(df5)
                        ):
                            send_signal(symbol, "LONG", last5["close"], last5["atr"])

                        # SHORT: 5m + 15m + 1h all DOWN
                        if (
                            trend15 == "DOWN"
                            and trend1h == "DOWN"
                            and check_short_setup(df5)
                        ):
                            send_signal(symbol, "SHORT", last5["close"], last5["atr"])

                    except Exception as error:
                        print(f"Error on {symbol}: {error}")

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print("Scanner error:", e)
            time.sleep(10)


# ======================================================
# WEBHOOK COMMANDS
# ======================================================

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return "OK"

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")

    if text == "/start":
        send_telegram_message("Bot Online. Advanced Mode Enabled.", chat_id)
    elif text == "/status":
        send_telegram_message("📡 Bot Running. Scanning Markets.", chat_id)
    elif text == "/help":
        send_telegram_message(
            "/start - Activate bot\n"
            "/status - Check bot health\n"
            "/help - Show commands",
            chat_id
        )

    return "OK"


# ======================================================
# THREAD START
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# RENDER SERVER
# ======================================================

@app.route("/")
def home():
    return "Advanced Smart-Money Bot Running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
