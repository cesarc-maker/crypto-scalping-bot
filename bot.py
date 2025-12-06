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

SCAN_INTERVAL = 30  # seconds (you chose 30 seconds)
TIMEFRAME = "1m"    # 1-minute early signals
LIMIT = 50          # enough for indicators

# Exchanges
bybit = ccxt.bybit()

# ============================================================
# TELEGRAM ALERT
# ============================================================

def tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg})
    except:
        pass


# ============================================================
# BLOFIN FUTURES FILTER (AUTO-DETECT)
# ============================================================

blofin = ccxt.blofin()

def normalize_symbol(symbol):
    clean = symbol.replace("/", "").replace(":", "").replace("-", "")
    clean = clean.replace("USDTUSDT", "USDT")
    return clean.upper()

def load_blofin_futures():
    markets = blofin.load_markets()
    valid = set()

    for sym, m in markets.items():
        # Only futures / swap
        if m.get("type") not in ["future", "swap"]:
            continue

        if "USDT" not in sym:
            continue

        if not m.get("active", False):
            continue

        base = m.get("base", "").upper()
        if any(x in base for x in ["3L","3S","5L","5S","UP","DOWN"]):
            continue

        valid.add(normalize_symbol(sym))

    return valid

BLOFIN_PAIRS = load_blofin_futures()
print(f"[INIT] Loaded Blofin FUTURES: {len(BLOFIN_PAIRS)} symbols")


# ============================================================
# INDICATOR FUNCTIONS
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
# ANTI-DUPLICATE SYSTEM (OPTION C)
# ============================================================

last_breakout = {}       # symbol → breakout level
signal_times = {}        # symbol → timestamps list
MAX_DUPES = 2            # max 2 alerts
WINDOW = 7200            # 2 hours

def allow_signal(symbol, breakout_level):
    now = time.time()

    # Init tracking
    if symbol not in last_breakout:
        last_breakout[symbol] = None
    if symbol not in signal_times:
        signal_times[symbol] = []

    # Reject same breakout zone
    if last_breakout[symbol] == breakout_level:
        return False

    # Clean old timestamps
    signal_times[symbol] = [ts for ts in signal_times[symbol] if now - ts < WINDOW]

    # Limit reached
    if len(signal_times[symbol]) >= MAX_DUPES:
        return False

    # Store new breakout
    last_breakout[symbol] = breakout_level
    signal_times[symbol].append(now)
    return True


# ============================================================
# SIGNAL LOGIC
# ============================================================

def check_symbol(symbol):
    try:
        ohlcv = bybit.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT)
    except Exception as e:
        print(f"[ERROR] {symbol}: {e}")
        return

    df = pd.DataFrame(ohlcv, columns=["time","open","high","low","close","volume"])

    if len(df) < 30:
        return

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    vol = df["volume"].values

    # Trend (use 1m EMA but still valid)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    trend_long = ema20[-1] > ema50[-1]
    trend_short = ema20[-1] < ema50[-1]

    # ATR (use 1m for early signals)
    atr_vals = atr(high, low, close, 14)
    atr_now = atr_vals[-1]
    atr_prev = atr_vals[-2]
    atr_exp = atr_now >= atr_prev * 1.20

    # Volume expansion
    vol_sma20 = pd.Series(vol).rolling(20).mean().values
    vol_exp = vol[-1] > vol_sma20[-1] * 1.5

    # Power candle
    body = df["close"] - df["open"]
    rng = df["high"] - df["low"]
    power = (abs(body) / rng) > 0.65

    # Microstructure breakout
    last3high = max(high[-4:-1])
    last3low = min(low[-4:-1])
    price = close[-1]

    # --------------------
    # LONG
    # --------------------
    if price > last3high and atr_exp and vol_exp and power.iloc[-1] and trend_long:

        if allow_signal(symbol, last3high):
            send_breakout(symbol, "LONG", price, atr_now)

    # --------------------
    # SHORT
    # --------------------
    if price < last3low and atr_exp and vol_exp and power.iloc[-1] and trend_short:

        if allow_signal(symbol, last3low):
            send_breakout(symbol, "SHORT", price, atr_now)


# ============================================================
# SEND BREAKOUT ALERT
# ============================================================

def send_breakout(symbol, side, price, atr_val):
    msg = (
        f"🚨 BREAKOUT SIGNAL\n"
        f"Symbol: {symbol}\n"
        f"Side: {side}\n"
        f"Price: {price:.4f}\n"
        f"ATR: {atr_val:.4f}\n\n"
        f"TP1: {(price + 2*atr_val) if side=='LONG' else (price - 2*atr_val):.4f}\n"
        f"TP2: {(price + 4*atr_val) if side=='LONG' else (price - 4*atr_val):.4f}\n"
        f"TP3: {(price + 6*atr_val) if side=='LONG' else (price - 6*atr_val):.4f}\n"
        f"TP4: {(price +10*atr_val) if side=='LONG' else (price -10*atr_val):.4f}\n\n"
        f"SL: {(price - 2*atr_val) if side=='LONG' else (price + 2*atr_val):.4f}"
    )

    tg(msg)
    print(f"[SIGNAL SENT] {symbol} {side}")


# ============================================================
# MAIN LOOP (REST SCANNER)
# ============================================================

def scanner_loop():
    while True:
        print("[SCAN] Starting scan...")

        for symbol in BLOFIN_PAIRS:
            try:
                check_symbol(symbol)
            except Exception as e:
                print(f"[SCAN ERROR] {symbol} → {e}")

        print("[SCAN] Done.\n")
        time.sleep(SCAN_INTERVAL)


# ============================================================
# FLASK SERVER FOR RENDER
# ============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Breakout Bot Running (REST Version)"


# Start scanner in background
threading.Thread(target=scanner_loop, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
