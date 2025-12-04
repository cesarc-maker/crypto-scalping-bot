import ccxt
import pandas as pd
import ta
import time
import threading
from telegram import Bot
from flask import Flask

# ============================
# CONFIGURATION
# ============================

import os
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 60          # scan every minute
PAIR_LIMIT = 40             # top 40 pairs
EXCHANGES = ["binance"]     # start with binance

bot = Bot(token=BOT_TOKEN)

# ============================
# FLASK WEB SERVER (for Render)
# ============================

app = Flask(__name__)

@app.route('/')
def home():
    return "Crypto Scalping Bot is running!"

# ============================
# BOT LOGIC
# ============================

def fetch_symbols(exchange):
    markets = exchange.load_markets()
    symbols = [s for s in markets if s.endswith("/USDT") and markets[s]["active"]]
    return symbols[:PAIR_LIMIT]

def fetch_ohlcv_df(exchange, symbol, tf="5m", limit=200):
    data = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume"])
    return df

def add_indicators(df):
    df["ema20"] = ta.trend.ema_indicator(df["close"], window=20)
    df["ema50"] = ta.trend.ema_indicator(df["close"], window=50)
    df["rsi"] = ta.momentum.rsi(df["close"])
    macd = ta.trend.MACD(df["close"])
    df["macd_hist"] = macd.macd_diff()
    df["atr"] = ta.volatility.average_true_range(df["high"], df["low"], df["close"])
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    return df

def detect_trend(df15):
    last = df15.iloc[-1]
    if last["ema20"] > last["ema50"] and last["rsi"] > 50 and last["macd_hist"] > 0:
        return "UP"
    if last["ema20"] < last["ema50"] and last["rsi"] < 50 and last["macd_hist"] < 0:
        return "DOWN"
    return "NONE"

def check_long_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    
    price = last["close"]
    near_ema = (abs(price - last["ema20"]) / price < 0.003 or 
                abs(price - last["ema50"]) / price < 0.003)

    rsi_ok = last["rsi"] > 45
    macd_flip = prev["macd_hist"] < 0 and last["macd_hist"] > 0
    vol_ok = last["volume"] > last["vol_sma20"] * 0.8

    return near_ema and rsi_ok and macd_flip and vol_ok

def check_short_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]
    
    price = last["close"]
    near_ema = (abs(price - last["ema20"]) / price < 0.003 or 
                abs(price - last["ema50"]) / price < 0.003)

    rsi_ok = last["rsi"] < 55
    macd_flip = prev["macd_hist"] > 0 and last["macd_hist"] < 0
    vol_ok = last["volume"] > last["vol_sma20"] * 0.8

    return near_ema and rsi_ok and macd_flip and vol_ok

def send_alert(symbol, direction, price, atr):
    message = f"""
⚡ SCALPING SETUP DETECTED

Pair: {symbol}
Direction: {direction}

Price: {price}
ATR (volatility): {round(atr, 4)}

Conditions:
- EMA pullback detected
- Momentum confirmed (RSI / MACD)
- Volume supports move

(No financial advice. Market changes fast.)
"""
    try:
        bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        print(f"Error sending message: {e}")

def scanner_loop():
    print("Bot scanner started...")
    bot.send_message(chat_id=CHAT_ID, text="Bot is running successfully 🎉")

    while True:
        try:
            for ex_name in EXCHANGES:
                exchange = getattr(ccxt, ex_name)()
                symbols = fetch_symbols(exchange)

                for symbol in symbols:
                    try:
                        df15 = add_indicators(fetch_ohlcv_df(exchange, symbol, "15m"))
                        df5 = add_indicators(fetch_ohlcv_df(exchange, symbol, "5m"))

                        trend = detect_trend(df15)
                        if trend == "NONE":
                            continue

                        last5 = df5.iloc[-1]

                        if trend == "UP" and check_long_setup(df5):
                            send_alert(symbol, "LONG", last5["close"], last5["atr"])

                        if trend == "DOWN" and check_short_setup(df5):
                            send_alert(symbol, "SHORT", last5["close"], last5["atr"])

                    except Exception:
                        continue

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(10)

# ============================
# RUN SCANNER IN BACKGROUND THREAD
# ============================

threading.Thread(target=scanner_loop, daemon=True).start()

# ============================
# RUN FLASK SERVER
# ============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)

