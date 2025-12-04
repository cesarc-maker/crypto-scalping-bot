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
# TELEGRAM MESSAGE SENDER
# ======================================================

def send_telegram_message(text, chat_id=None):
    if chat_id is None:
        chat_id = CHAT_ID

    try:
        url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={chat_id}&text={text}"
        )
        requests.get(url)
    except Exception as e:
        print(f"Telegram send error: {e}")


# ======================================================
# OHLCV + INDICATORS
# ======================================================

def fetch_ohlcv_df(exchange, symbol, timeframe):
    data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=120)
    df = pd.DataFrame(
        data,
        columns=["timestamp", "open", "high", "low", "close", "volume"]
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
    return 100 - (100 / (1 + rs))


def compute_atr(df):
    df["H-L"] = df["high"] - df["low"]
    df["H-PC"] = abs(df["high"] - df["close"].shift(1))
    df["L-PC"] = abs(df["low"] - df["close"].shift(1))
    tr = df[["H-L", "H-PC", "L-PC"]].max(axis=1)
    return tr.rolling(14).mean()


def add_indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df)
    return df


# ======================================================
# TREND LOGIC (15m)
# ======================================================

def detect_trend(df15):
    last = df15.iloc[-1]
    if last["ema20"] > last["ema50"]:
        return "UP"
    if last["ema20"] < last["ema50"]:
        return "DOWN"
    return "NONE"


# ======================================================
# STRICT MODE SIGNAL LOGIC (very low noise, high confidence)
# ======================================================

def check_long_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    prev2 = df5.iloc[-3]

    # EMA20 must accelerate upward
    ema_slope = (last["ema20"] - prev["ema20"]) > (prev["ema20"] - prev2["ema20"])

    # Price must close clearly above EMA20 (0.2% buffer)
    ema_position = last["close"] > last["ema20"] * 1.002

    # RSI must be in strong upward momentum zone
    rsi_ok = 50 < last["rsi"] < 65

    # Candle body must be > 60% of its total range
    body = last["close"] - last["open"]
    candle_range = last["high"] - last["low"]
    bullish_strong = (body > 0) and (body > 0.6 * candle_range)

    # ATR must be expanding (volatility increasing)
    atr_ok = last["atr"] > prev["atr"]

    return ema_slope and ema_position and rsi_ok and bullish_strong and atr_ok


def check_short_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    prev2 = df5.iloc[-3]

    # EMA20 must accelerate downward
    ema_slope = (prev["ema20"] - last["ema20"]) > (prev2["ema20"] - prev["ema20"])

    # Price must close clearly below EMA20 (0.2% buffer)
    ema_position = last["close"] < last["ema20"] * 0.998

    # RSI must be in strong downward momentum zone
    rsi_ok = 35 < last["rsi"] < 50

    # Candle body must be > 60% of its range AND bearish
    body = last["open"] - last["close"]
    candle_range = last["high"] - last["low"]
    bearish_strong = (body > 0) and (body > 0.6 * candle_range)

    # ATR must be rising (strong movement potential)
    atr_ok = last["atr"] > prev["atr"]

    return ema_slope and ema_position and rsi_ok and bearish_strong and atr_ok


# ======================================================
# EXCHANGE FORMATTING
# ======================================================

def get_exchange(name):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options": {"defaultType": "future"}})
        if name == "bybit":
            return ccxt.bybit({"options": {"defaultType": "linear"}})
        return getattr(ccxt, name)()
    except:
        return None


def fetch_usdt_pairs(exchange):
    try:
        markets = exchange.load_markets()
        return [s for s in markets if isinstance(s, str) and s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []


# ======================================================
# SIGNAL SENDER (1:2 RR)
# ======================================================

def send_signal(symbol, direction, price, atr):
    atr = float(atr)

    if direction == "LONG":
        sl = price - (1.5 * atr)
        tp1 = price + (1 * atr)
        tp2 = price + (2 * atr)  # 1:2 RR
        tp3 = price + (3 * atr)
    else:
        sl = price + (1.5 * atr)
        tp1 = price - (1 * atr)
        tp2 = price - (2 * atr)  # 1:2 RR
        tp3 = price - (3 * atr)

    message = (
        f"🔥 STRICT {direction} Signal\n\n"
        f"Pair: {symbol}\n"
        f"Price: {price}\n"
        f"ATR: {round(atr, 4)}\n\n"
        f"📍 Stop Loss: {round(sl, 4)}\n\n"
        f"🎯 Take Profits:\n"
        f"• TP1: {round(tp1, 4)}\n"
        f"• TP2: {round(tp2, 4)} (1:2 RR)\n"
        f"• TP3: {round(tp3, 4)}\n\n"
        f"⚠️ Informational analysis only."
    )

    send_telegram_message(message)
    print(f"Sent STRICT alert → {symbol} {direction}")


# ======================================================
# SCANNER LOOP
# ======================================================

def scanner_loop():
    print("Bot scanner started...")
    send_telegram_message("STRICT MODE bot is running 🎉")

    while True:
        try:
            for ex_name in EXCHANGES:

                ex = get_exchange(ex_name)
                if ex is None:
                    continue

                symbols = fetch_usdt_pairs(ex)

                for symbol in symbols:
                    try:
                        df15 = add_indicators(fetch_ohlcv_df(ex, symbol, "15m"))
                        df5 = add_indicators(fetch_ohlcv_df(ex, symbol, "5m"))

                        trend = detect_trend(df15)
                        if trend == "NONE":
                            continue

                        last5 = df5.iloc[-1]

                        if trend == "UP" and check_long_setup(df5):
                            send_signal(symbol, "LONG", last5["close"], last5["atr"])

                        if trend == "DOWN" and check_short_setup(df5):
                            send_signal(symbol, "SHORT", last5["close"], last5["atr"])

                    except Exception as e:
                        print(f"Symbol error ({symbol}): {e}")
                        continue

            time.sleep(SCAN_INTERVAL)

        except Exception as err:
            print(f"Scanner error: {err}")
            time.sleep(10)


# ======================================================
# TELEGRAM WEBHOOK COMMANDS
# ======================================================

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()

    if not data:
        return "OK"

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = data["message"].get("text", "")

        if text == "/start":
            send_telegram_message("STRICT bot active. Use /status to check.", chat_id)

        if text == "/status":
            send_telegram_message("📡 STRICT MODE bot online, scanning high-quality setups only.", chat_id)

        if text == "/help":
            send_telegram_message(
                "Commands:\n"
                "/start - Activate bot\n"
                "/status - Check bot health\n"
                "/help - Show commands",
                chat_id
            )

    return "OK"


# ======================================================
# START SCANNER THREAD
# ======================================================

threading.Thread(target=scanner_loop, daemon=True).start()


# ======================================================
# ROOT ENDPOINT
# ======================================================

@app.route("/")
def home():
    return "STRICT MODE bot running — ultra-filtered signals."


# ======================================================
# RUN SERVER (RENDER)
# ======================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
