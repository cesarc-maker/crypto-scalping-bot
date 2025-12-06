import ccxt
import time
import pandas as pd
import numpy as np
import requests
from flask import Flask
import threading

# ============================================================
# CONFIG
# ============================================================

TELEGRAM_TOKEN = "YOUR_TELEGRAM_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

SCAN_INTERVAL = 30   # You chose 30 seconds
TIMEFRAME = "1m"     # 1-minute early signals
LIMIT = 50           # enough for indicators


# ============================================================
# TELEGRAM FUNCTION
# ============================================================

def tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except:
        pass


# ============================================================
# EXCHANGES (ALL MAJOR ONES)
# ============================================================

binance = ccxt.binance()
binance_futures = ccxt.binanceusdm()
bybit = ccxt.bybit()
kucoin = ccxt.kucoin()
okx = ccxt.okx()

EXCHANGES = [
    binance,
    binance_futures,
    bybit,
    kucoin,
    okx
]

print("[INIT] Loaded exchanges:", [type(x).__name__ for x in EXCHANGES])


# ============================================================
# SYMBOL LOADER — LOAD ALL USDT SYMBOLS FROM ALL EXCHANGES
# ============================================================

def load_all_symbols():
    symbols = set()

    for ex in EXCHANGES:
        try:
            markets = ex.load_markets()
            for s in markets:
                if "USDT" in s:
                    clean = s.replace("/", "").replace(":USDT","")
                    symbols.add(clean)
        except Exception as e:
            print(f"[ERROR loading markets from {type(ex).__name__}] {e}")

    return symbols

ALL_PAIRS = load_all_symbols()
print(f"[INIT] Loaded {len(ALL_PAIRS)} symbols across all exchanges")


# ============================================================
# INDICATORS
# ============================================================

def ema(values, length):
    return pd.Series(values).ewm(span=length, adjust=False).mean().values

def true_range(h, l, c):
    tr = []
    for i in range(1, len(c)):
        tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    return np.array(tr)

def atr(h, l, c, period=14):
    tr = true_range(h, l, c)
    return pd.Series(tr).rolling(period).mean().values


# ============================================================
# ANTI-DUPLICATE (OPTION C - NEW BREAKOUT LEVEL REQUIRED)
# ============================================================

last_breakout_level = {}     # symbol → price of last breakout zone
signal_timestamps = {}       # symbol → list of timestamps

MAX_ALERTS_2H = 2
WINDOW = 2 * 60 * 60  # 2 hours


def allow_signal(symbol, breakout_level):
    now = time.time()

    if symbol not in last_breakout_level:
        last_breakout_level[symbol] = None
    if symbol not in signal_timestamps:
        signal_timestamps[symbol] = []

    # Reject same breakout zone again
    if last_breakout_level[symbol] == breakout_level:
        return False

    # Purge old timestamps
    signal_timestamps[symbol] = [
        ts for ts in signal_timestamps[symbol]
        if now - ts < WINDOW
    ]

    # Check limit
    if len(signal_timestamps[symbol]) >= MAX_ALERTS_2H:
        return False

    # Approve new signal
    last_breakout_level[symbol] = breakout_level
    signal_timestamps[symbol].append(now)
    return True


# ============================================================
# BREAKOUT LOGIC
# ============================================================

def fetch_from_all_exchanges(symbol):
    """Try each exchange until data is found."""
    for ex in EXCHANGES:
        try:
            ohlcv = ex.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT)
            if ohlcv:
                return ohlcv
        except:
            continue
    return None


def check_symbol(symbol):
    ohlcv = fetch_from_all_exchanges(symbol)
    if ohlcv is None:
        return

    df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])

    if len(df) < 30:
        return

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    vol = df["volume"].values
    op = df["open"].values

    # Trend filter (EMA20/50 on 1m)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    trend_long = ema20[-1] > ema50[-1]
    trend_short = ema20[-1] < ema50[-1]

    # ATR
    atr_vals = atr(high, low, close, 14)
    atr_now = atr_vals[-1]
    atr_prev = atr_vals[-2]
    atr_exp = atr_now >= atr_prev * 1.20

    # Volume expansion
    vol_sma20 = pd.Series(vol).rolling(20).mean().values
    vol_exp = vol[-1] > vol_sma20[-1] * 1.5

    # Power candle
    body = close - op
    rng = high - low
    power = (abs(body) / rng) > 0.65

    # Microstructure breakout
    last3high = max(high[-4:-1])
    last3low = min(low[-4:-1])
    price = close[-1]

    # LONG
    if price > last3high and atr_exp and vol_exp and power.iloc[-1] and trend_long:
        if allow_signal(symbol, last3high):
            send_breakout(symbol, "LONG", price, atr_now)

    # SHORT
    if price < last3low and atr_exp and vol_exp and power.iloc[-1] and trend_short:
        if allow_signal(symbol, last3low):
            send_breakout(symbol, "SHORT", price, atr_now)


# ============================================================
# SEND ALERT
# ============================================================

def send_breakout(symbol, side, price, atr_val):
    msg = (
        f"🚨 BREAKOUT SIGNAL\n"
        f"{symbol} — {side}\n\n"
        f"Entry: {price:.4f}\n"
        f"ATR: {atr_val:.4f}\n\n"
        f"TP1: {(price + 2*atr_val) if side=='LONG' else (price - 2*atr_val):.4f}\n"
        f"TP2: {(price + 4*atr_val) if side=='LONG' else (price - 4*atr_val):.4f}\n"
        f"TP3: {(price + 6*atr_val) if side=='LONG' else (price - 6*atr_val):.4f}\n"
        f"TP4: {(price +10*atr_val) if side=='LONG' else (price -10*atr_val):.4f}\n\n"
        f"SL: {(price - 2*atr_val) if side=='LONG' else (price + 2*atr_val):.4f}"
    )

    tg(msg)
    print(f"[SIGNAL] {symbol} {side}")


# ============================================================
# SCANNER LOOP
# ============================================================

def scanner_loop():
    while True:
        print("[SCAN] Starting scan…")

        for symbol in ALL_PAIRS:
            try:
                check_symbol(symbol)
            except Exception as e:
                print(f"[SCAN ERROR] {symbol}: {e}")

        print("[SCAN] Done.\n")
        time.sleep(SCAN_INTERVAL)


# ============================================================
# FLASK FOR RENDER
# ============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Breakout Bot Running (REST + All Exchanges)"


# Start scanner
threading.Thread(target=scanner_loop, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
