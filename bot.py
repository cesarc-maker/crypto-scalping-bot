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

SCAN_INTERVAL = 30   # Every 30 seconds
TIMEFRAME = "1m"
LIMIT = 50            # Number of candles to fetch


# ============================================================
# TELEGRAM SENDER
# ============================================================

def tg(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(
            url, 
            data={
                "chat_id": TELEGRAM_CHAT_ID, 
                "text": msg, 
                "parse_mode": "Markdown"
            }
        )
    except:
        pass


# ============================================================
# STARTUP MESSAGE
# ============================================================

def send_startup_message():
    msg = (
        "✅ *Breakout Bot Started Successfully!*\n\n"
        "🟢 Exchanges Loaded: OKX, KuCoin, Bitget, MEXC\n"
        f"🟡 Scan Interval: {SCAN_INTERVAL} seconds\n"
        "🔵 Timeframe: 1-minute early signals\n"
        "🟥 Short Mode: Aggressive TP (3×, 5×, 8×, 12× ATR)\n"
        "🟩 Long Mode: Standard TP (2×, 4×, 6×, 10× ATR)\n"
        "🚀 Now scanning for breakout opportunities..."
    )
    tg(msg)


# ============================================================
# EXCHANGES THAT WORK ON RENDER
# ============================================================

okx = ccxt.okx()
kucoin = ccxt.kucoin()
bitget = ccxt.bitget()
mexc = ccxt.mexc()

EXCHANGES = [okx, kucoin, bitget, mexc]


# ============================================================
# LOAD ALL USDT SYMBOLS
# ============================================================

def load_all_symbols():
    symbols = set()

    for ex in EXCHANGES:
        try:
            markets = ex.load_markets()
            for sym in markets:
                if "USDT" in sym:
                    clean = sym.replace("/", "").replace(":USDT", "")
                    symbols.add(clean)
        except Exception as e:
            print(f"[MARKET ERROR] {type(ex).__name__}: {e}")

    return symbols


ALL_PAIRS = load_all_symbols()
print(f"[INIT] Loaded {len(ALL_PAIRS)} tradable symbols.")

# Send Telegram startup message
send_startup_message()


# ============================================================
# INDICATORS
# ============================================================

def ema(values, length):
    return pd.Series(values).ewm(span=length, adjust=False).mean().values

def true_range(h, l, c):
    tr = []
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])))
    return np.array(tr)

def atr(h, l, c, period=14):
    tr = true_range(h, l, c)
    return pd.Series(tr).rolling(period).mean().values


# ============================================================
# ANTI-DUPLICATE SYSTEM
# ============================================================

last_breakout_level = {}
signal_times = {}

MAX_DUPES = 2
WINDOW = 7200  # 2 hours


def allow_signal(symbol, breakout_level):
    now = time.time()

    if symbol not in last_breakout_level:
        last_breakout_level[symbol] = None
    if symbol not in signal_times:
        signal_times[symbol] = []

    # Reject identical breakout level
    if last_breakout_level[symbol] == breakout_level:
        return False

    # Remove timestamps older than 2 hours
    signal_times[symbol] = [ts for ts in signal_times[symbol] if now - ts < WINDOW]

    # Enforce max 2 signals per 2 hours
    if len(signal_times[symbol]) >= MAX_DUPES:
        return False

    # Accept new breakout & record
    last_breakout_level[symbol] = breakout_level
    signal_times[symbol].append(now)
    return True


# ============================================================
# FETCH OHLCV FROM ANY WORKING EXCHANGE
# ============================================================

def fetch_candles(symbol):
    for ex in EXCHANGES:
        try:
            return ex.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=LIMIT)
        except:
            continue
    return None


# ============================================================
# BREAKOUT LOGIC (LONG + ADVANCED SHORT)
# ============================================================

def check_symbol(symbol):
    ohlcv = fetch_candles(symbol)
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

    # Trend Filter
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)

    trend_long = ema20[-1] > ema50[-1]
    trend_short = ema20[-1] < ema50[-1]

    # ATR Explosion
    atr_vals = atr(high, low, close, 14)
    atr_now = atr_vals[-1]
    atr_prev = atr_vals[-2]
    atr_exp = atr_now >= atr_prev * 1.20

    # Volume Expansion
    vol_sma20 = pd.Series(vol).rolling(20).mean().values
    vol_exp_long = vol[-1] > vol_sma20[-1] * 1.5
    vol_exp_short = vol[-1] > vol_sma20[-1] * 1.3  # Lighter for shorts

    # Power Candle
    body = close - op
    rng = high - low

    power_long = (abs(body) / rng) > 0.65
    power_short = (abs(body) / rng) > 0.70  # More strict

    # SHORT wick dominance
    upper_wick = high[-1] - max(op[-1], close[-1])
    lower_wick = min(op[-1], close[-1]) - low[-1]
    bearish_wick = upper_wick >= (lower_wick * 2)

    # Microstructure Levels
    last3high = max(high[-4:-1])
    last3low = min(low[-4:-1])
    price = close[-1]

    # ======================
    # 🟢 LONG BREAKOUT
    # ======================
    if (
        price > last3high and
        atr_exp and
        vol_exp_long and
        power_long and
        trend_long
    ):
        if allow_signal(symbol, last3high):
            send_long_signal(symbol, price, atr_now)

    # ======================
    # 🔴 SHORT BREAKDOWN (ADVANCED)
    # ======================
    if (
        price < last3low and
        atr_exp and
        vol_exp_short and
        power_short and
        trend_short and
        bearish_wick
    ):
        if allow_signal(symbol, last3low):
            send_short_signal(symbol, price, atr_now)


# ============================================================
# LONG SIGNAL
# ============================================================

def send_long_signal(symbol, price, atr_val):
    msg = (
        f"🟢 *LONG BREAKOUT*\n"
        f"Symbol: `{symbol}`\n\n"
        f"Entry: *{price:.4f}*\n"
        f"ATR: {atr_val:.4f}\n\n"
        f"TP1: {price + 2*atr_val:.4f}\n"
        f"TP2: {price + 4*atr_val:.4f}\n"
        f"TP3: {price + 6*atr_val:.4f}\n"
        f"TP4: {price +10*atr_val:.4f}\n\n"
        f"SL: {price - 2*atr_val:.4f}"
    )
    tg(msg)
    print(f"[LONG] {symbol}")


# ============================================================
# SHORT SIGNAL (AGGRESSIVE)
# ============================================================

def send_short_signal(symbol, price, atr_val):
    msg = (
        f"🔴 *SHORT BREAKDOWN*\n"
        f"Symbol: `{symbol}`\n\n"
        f"Entry: *{price:.4f}*\n"
        f"ATR: {atr_val:.4f}\n\n"
        f"TP1: {price - 3*atr_val:.4f}\n"
        f"TP2: {price - 5*atr_val:.4f}\n"
        f"TP3: {price - 8*atr_val:.4f}\n"
        f"TP4: {price -12*atr_val:.4f}\n\n"
        f"SL: {price + 1.8*atr_val:.4f}"
    )
    tg(msg)
    print(f"[SHORT] {symbol}")


# ============================================================
# MAIN SCANNER
# ============================================================

def scanner_loop():
    while True:
        print("[SCAN] Starting cycle...")

        for symbol in ALL_PAIRS:
            try:
                check_symbol(symbol)
            except Exception as e:
                print(f"[ERROR] {symbol}: {e}")

        print("[SCAN] Cycle complete.\n")
        time.sleep(SCAN_INTERVAL)


# ============================================================
# FLASK FOR RENDER
# ============================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "Breakout Bot Running (REST + Multi-Exchange + Enhanced Logic)"


threading.Thread(target=scanner_loop, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
