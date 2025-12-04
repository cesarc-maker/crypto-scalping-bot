import os
import time
import ccxt
import pandas as pd
from flask import Flask
import threading
import requests


# ======================================================
# CONFIGURATION
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 60     # Scan once per minute
PAIR_LIMIT = 40        # Number of top USDT pairs to scan
EXCHANGES = ["binance"]


# ======================================================
# SIMPLE TELEGRAM MESSAGE SENDER
# ======================================================

def send_telegram_message(text):
    try:
        url = (
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            f"?chat_id={CHAT_ID}&text={text}"
        )
        requests.get(url)
    except Exception as e:
        print(f"Telegram send error: {e}")


# ======================================================
# OHLCV FETCHING + INDICATORS
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
# TREND + SIGNAL LOGIC (MODERATE MODE)
# ======================================================

def detect_trend(df15):
    last = df15.iloc[-1]
    if last["ema20"] > last["ema50"]:
        return "UP"
    if last["ema20"] < last["ema50"]:
        return "DOWN"
    return "NONE"


def check_long_setup(df5):
    last = df5.iloc[-1]

    ema_pullback = (
        last["close"] > last["ema20"] or
        last["close"] > last["ema50"]
    )

    rsi_ok = last["rsi"] < 80

    return ema_pullback and rsi_ok


def check_short_setup(df5):
    last = df5.iloc[-1]

    ema_pullback = (
        last["close"] < last["ema20"] or
        last["close"] < last["ema50"]
    )

    rsi_ok = last["rsi"] > 20

    return ema_pullback and rsi_ok


# ======================================================
# FETCH SYMBOLS (USDT PAIRS)
# ======================================================

def fetch_symbols(exchange):
    try:
        markets = exchange.load_markets()
        return [s for s in markets if s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return
