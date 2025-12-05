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
# TELEGRAM ALERTS
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
# MARKET DATA + INDICATORS
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
# SUPPORT/RESISTANCE LEVELS
# ======================================================

def find_recent_swing_high(df, lookback=12):
    return df.tail(lookback)["high"].max()

def find_recent_swing_low(df, lookback=12):
    return df.tail(lookback)["low"].min()


# ======================================================
# LESS-STRICT SMART-MONEY LONG LOGIC
# ======================================================

def check_long_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    prev2 = df5.iloc[-3]

    # 1️⃣ Liquidity sweep stays
    swept_low = last["low"] < prev["low"]
    reclaimed = last["close"] > last["ema20"]
    liquidity_sweep_ok = swept_low and reclaimed

    # 2️⃣ EMA acceleration softened
    ema_slope = (last["ema20"] - prev["ema20"]) > (prev["ema20"] - prev2["ema20"]) * 1.05

    # 3️⃣ Removed swing breakout requirement
    breakout_ok = True

    # 4️⃣ Price above EMA20 by 0.2%
    ema_position = last["close"] > last["ema20"] * 1.002

    # 5️⃣ Wider RSI for momentum
    rsi_ok = 50 < last["rsi"] < 72

    # 6️⃣ Candle strength relaxed to 55%
    body = last["close"] - last["open"]
    range_ = last["high"] - last["low"]
    bullish_strong = body > 0 and body > 0.55 * range_

    # 7️⃣ ATR expansion softened
    atr_ok = last["atr"] > prev["atr"] * 1.05

    # 8️⃣ Expected move range loosened
    expected_ok = last["atr"] * 3 < last["close"] * 0.025

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
# LESS-STRICT SMART-MONEY SHORT LOGIC
# ======================================================

def check_short_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    prev2 = df5.iloc[-3]

    # 1️⃣ Liquidity sweep stays
    swept_high = last["high"] > prev["high"]
    rejected = last["close"] < last["ema20"]
    liquidity_sweep_ok = swept_high and rejected

    # 2️⃣ EMA acceleration softened
    ema_slope = (prev["ema20"] - last["ema20"]) > (prev2["ema20"] - prev["ema20"]) * 1.05

    # 3️⃣ Remove mandatory breakdown
    breakdown_ok = True

    # 4️⃣ Close below EMA20 by 0.2%
    ema_position = last["close"] < last["ema20"] * 0.998

    # 5️⃣ RSI softened for bearish momentum
    rsi_ok = 28 < last["rsi"] < 48

    # 6️⃣ Candle strength relaxed to 55%
    body = last["open"] - last["close"]
    range_ = last["high"] - last["low"]
    bearish_strong = body > 0 and body > 0.55 * range_

    # 7️⃣ ATR expansion softened
    atr_ok = last["atr"] > prev["atr"] * 1.05

    # 8️⃣ Expected move loosened
    expected_ok = last["atr"] * 3 < last["close"] * 0.025

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
# EXCHANGE HANDLING
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
# SIGNAL DISPATCH (TP/SL)
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
        f"🔥 SIGNAL ({direction})\n\n"
        f"Pair: {symbol}\n"
        f"Price: {price}\n"
        f"ATR: {round(atr,4)}\n\n"
        f"SL: {round(sl,4)}\n"
        f"TP1: {round(tp1,4)}\n"
        f"TP2: {round(tp2,4)} (1:2 RR)\n"
        f"TP3: {round(tp3,4)}\n\n"
        f"⚠ Informational only."
    )

    send_telegram_message(msg)
    print(f"Sent SIGNAL → {symbol} {direction}")


# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():
    send_telegram_message("🚀 Bot Activated (Less Strict High-Momentum Mode)")

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

                        # LONG conditions
                        if (
                            trend15 == "UP"
                            and trend1h == "UP"
                            and check_long_setup(df5)
                        ):
                            send_signal(symbol, "LONG", last5["close"], last5["atr"])

                        # SHORT conditions
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
# TELEGRAM WEBHOOKS
# ======================================================

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return "OK"

    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "")

    if text == "/start":
        send_telegram_message("Bot Online. Less Strict High-Momentum Mode.", chat_id)

    elif text == "/status":
        send_telegram_message("📡 Bot Running & Scanning.", chat_id)

    elif text == "/help":
        send_telegram_message(
            "/start - Activate Bot\n"
            "/status - Check Status\n"
            "/help - Commands",
            chat_id
        )

    return "OK"


# ======================================================
# START THREAD
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# WEB SERVER FOR RENDER
# ======================================================

@app.route("/")
def home():
    return "Bot Running (Less Strict High-Momentum Mode)"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
