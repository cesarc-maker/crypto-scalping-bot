import ccxt
import pandas as pd
import ta
import time
from telegram import Bot

# ============================
# CONFIGURATION
# ============================
BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"   # Where alerts will be sent
EXCHANGES = ["binance"]             # Start with binance
SCAN_INTERVAL = 60                  # 1 minute
PAIR_LIMIT = 40                     # Scan top 40 liquid pairs

bot = Bot(token=BOT_TOKEN)

# ============================
# HELPER FUNCTIONS
# ============================

def fetch_symbols(exchange):
    markets = exchange.load_markets()
    # USDT spot pairs only
    symbols = [s for s in markets if s.endswith("/USDT") and markets[s]["active"]]
    return symbols[:PAIR_LIMIT]     # Limit scanning for performance

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
    if (last["ema20"] > last["ema50"] and last["rsi"] > 50 and last["macd_hist"] > 0):
        return "UP"
    if (last["ema20"] < last["ema50"] and last["rsi"] < 50 and last["macd_hist"] < 0):
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

    if near_ema and rsi_ok and macd_flip and vol_ok:
        return True
    return False

def check_short_setup(df5):
    last = df5.iloc[-1]
    prev = df5.iloc[-2]

    price = last["close"]
    near_ema = (abs(price - last["ema20"]) / price < 0.003 or
                abs(price - last["ema50"]) / price < 0.003)

    rsi_ok = last["rsi"] < 55
    macd_flip = prev["macd_hist"] > 0 and last["macd_hist"] < 0

    vol_ok = last["volume"] > last["vol_sma20"] * 0.8

    if near_ema and rsi_ok and macd_flip and vol_ok:
        return True
    return False

def send_alert(symbol, direction, price, atr):
    message = f"""
🔥 SCALPING SETUP DETECTED

Pair: {symbol}
Direction: {direction}

Current Price: {price}
ATR (volatility): {round(atr, 4)}

Structure:
- EMA pullback detected
- RSI & MACD confirm momentum
- Volume conditions satisfied

⚠️ No financial advice. Market conditions change quickly.
"""

    bot.send_message(chat_id=CHAT_ID, text=message)

# ============================
# MAIN LOOP
# ============================

def main():
    print("Bot started. Scanning crypto markets every minute...")

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

                    except Exception as e:
                        continue

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            time.sleep(10)

if __name__ == "__main__":
    main()
