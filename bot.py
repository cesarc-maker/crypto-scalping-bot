# ======================================================
# RSI MEAN-REVERSION SCALPING BOT (LOOSER PRO VERSION)
# INFORMATIONAL ONLY • QUICK SCALPS • MULTI-CHAT TELEGRAM
# SAME STRUCTURE AS YOUR ORIGINAL BOT (Render-ready)
#
# ✅ NOW INCLUDES YOUR PREVIOUS EXCHANGES:
# Binance (spot), Binance Futures, Bybit, KuCoin, OKX
# ======================================================

import os
import time
import ccxt
import pandas as pd
import threading
from flask import Flask
import requests
import logging
from datetime import datetime, timezone

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("RSI_SCALPER_LOOSE_MULTIEX")

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Single ID variables
CHAT_ID1 = os.getenv("CHAT_ID", "").strip()
CHAT_ID2 = os.getenv("CHAT_ID2", "").strip()

# Comma-separated ID list
RAW_CHAT_IDS = os.getenv("CHAT_IDS", "")

# Build final chat ID list (dedupe)
CHAT_IDS = set()
if CHAT_ID1:
    CHAT_IDS.add(CHAT_ID1)
if CHAT_ID2:
    CHAT_IDS.add(CHAT_ID2)
if RAW_CHAT_IDS:
    for cid in RAW_CHAT_IDS.split(","):
        cid = cid.strip()
        if cid:
            CHAT_IDS.add(cid)
CHAT_IDS = list(CHAT_IDS)

PORT = int(os.getenv("PORT", 10000))

# How often we scan + track
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 10))      # seconds
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 10))    # seconds

# Universe / performance knobs
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 60))
WINDOW = int(os.getenv("WINDOW", 600))  # cooldown seconds (looser: 10 min)

# ✅ Your previous exchange set (same names as your old bot)
EXCHANGES = ["binance", "binance_futures", "bybit", "kucoin", "okx"]

# Strategy knobs (LOOSER)
TF_EXEC = os.getenv("TF_EXEC", "1m")

RSI_PERIOD = int(os.getenv("RSI_PERIOD", 14))
RSI_LOW = float(os.getenv("RSI_LOW", 32))     # looser than 30
RSI_HIGH = float(os.getenv("RSI_HIGH", 68))   # looser than 70

EMA_PERIOD = int(os.getenv("EMA_PERIOD", 50))

# Allow price to be near EMA (looser)
EMA_NEAR_PCT = float(os.getenv("EMA_NEAR_PCT", 0.001))  # 0.1%

# Wick filter (looser)
MAX_WICK_FRAC = float(os.getenv("MAX_WICK_FRAC", 0.65))  # wick <= 65% of range

# Stop/TP (scalping defaults, percentage-based; informational)
STOP_PCT = float(os.getenv("STOP_PCT", 0.003))   # 0.30%
TP_PCT = float(os.getenv("TP_PCT", 0.006))       # 0.60% (≈ 1:2)

# Recent signal protection
recent_signals = {}

# Open trades registry (informational)
open_trades = {}
open_trades_lock = threading.Lock()

# ======================================================
# TELEGRAM
# ======================================================

def send_telegram(text: str):
    """Send Telegram messages to ALL configured chat IDs."""
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing")
        return
    if not CHAT_IDS:
        log.warning("No chat IDs configured")
        return

    encoded = requests.utils.quote(text)

    for cid in CHAT_IDS:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage?chat_id={cid}&text={encoded}"
            requests.get(url, timeout=8)
        except Exception as e:
            log.error(f"Telegram error for {cid}: {e}")

def send_startup():
    msg = (
        "🚀 RSI MEAN-REVERSION SCALPER (LOOSER PRO)\n\n"
        "Mode: Informational only\n"
        f"Timeframe: {TF_EXEC}\n"
        f"RSI: {RSI_PERIOD} | Levels: {RSI_LOW}/{RSI_HIGH}\n"
        f"EMA: {EMA_PERIOD} | Near EMA allowed: {EMA_NEAR_PCT*100:.2f}%\n"
        f"Wick Filter: <= {int(MAX_WICK_FRAC*100)}% of candle range\n"
        f"Stop/TP (pct): {STOP_PCT*100:.2f}% / {TP_PCT*100:.2f}%\n\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n"
        f"Scan Interval: {SCAN_INTERVAL}s | Track Interval: {TRACK_INTERVAL}s\n"
        f"Cooldown: {WINDOW}s\n"
        f"Pairs scanned per exchange: up to {PAIR_LIMIT}\n"
        f"Chats: {len(CHAT_IDS)}\n"
    )
    send_telegram(msg)
    log.info(f"Startup message sent → chats: {CHAT_IDS}")

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

def allow(symbol: str, side: str) -> bool:
    now = time.time()
    key = f"{symbol}_{side}"

    if key not in recent_signals:
        recent_signals[key] = now
        return True

    if now - recent_signals[key] > WINDOW:
        recent_signals[key] = now
        return True

    return False

# ======================================================
# INDICATORS
# ======================================================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema"] = df["close"].ewm(span=EMA_PERIOD).mean()

    # RSI(14) (SMA method; stable + simple)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(RSI_PERIOD).mean()
    avg_loss = loss.rolling(RSI_PERIOD).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    df["range"] = df["high"] - df["low"]
    return df

def get_df(ex, symbol: str, tf: str):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=120)
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        return add_indicators(df)
    except Exception as e:
        log.error(f"Fetch error {symbol} {tf}: {e}")
        return None

# ======================================================
# EXCHANGES (same pattern as your old bot)
# ======================================================

def get_ex(name: str):
    try:
        if name == "binance_futures":
            return ccxt.binance({"options": {"defaultType": "future"}})
        if name == "bybit":
            return ccxt.bybit({"options": {"defaultType": "linear"}})
        return getattr(ccxt, name)()
    except Exception as e:
        log.error(f"Exchange load error ({name}): {e}")
        return None

EX_INSTANCES = {}

def get_ex_cached(name: str):
    if name in EX_INSTANCES and EX_INSTANCES[name]:
        return EX_INSTANCES[name]
    ex = get_ex(name)
    EX_INSTANCES[name] = ex
    return ex

# ======================================================
# PAIRS
# ======================================================

def get_pairs(ex):
    """
    Simple universe: all USDT pairs up to PAIR_LIMIT.
    Note: This includes both "BTC/USDT" and any symbol strings that end with "USDT".
    """
    try:
        mk = ex.load_markets()
        pairs = [s for s in mk if s.endswith("USDT")]
        return pairs[:PAIR_LIMIT]
    except Exception as e:
        log.error(f"Pair load error: {e}")
        return []

# ======================================================
# RSI SCALPING SETUPS (LOOSER PRO VERSION)
# ======================================================

def long_setup(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    prev = df.iloc[-2]

    rng = float(last["range"])
    if rng <= 0:
        return False

    # RSI extreme -> return above threshold
    rsi_ok = (prev["rsi"] < RSI_LOW) and (last["rsi"] > RSI_LOW)

    # Bullish candle
    candle_ok = last["close"] > last["open"]

    # EMA filter: above OR near (looser)
    ema = float(last["ema"])
    near_ema = abs(float(last["close"]) - ema) / ema <= EMA_NEAR_PCT
    trend_ok = (last["close"] >= ema) or near_ema

    # Wick quality: allow some wick, but avoid big rejection
    upper_wick = float(last["high"]) - float(last["close"])
    wick_ok = upper_wick <= MAX_WICK_FRAC * rng

    return rsi_ok and candle_ok and trend_ok and wick_ok

def short_setup(df: pd.DataFrame) -> bool:
    last = df.iloc[-1]
    prev = df.iloc[-2]

    rng = float(last["range"])
    if rng <= 0:
        return False

    # RSI extreme -> return below threshold
    rsi_ok = (prev["rsi"] > RSI_HIGH) and (last["rsi"] < RSI_HIGH)

    # Bearish candle
    candle_ok = last["close"] < last["open"]

    # EMA filter: below OR near (looser)
    ema = float(last["ema"])
    near_ema = abs(float(last["close"]) - ema) / ema <= EMA_NEAR_PCT
    trend_ok = (last["close"] <= ema) or near_ema

    # Wick quality: allow some wick, but avoid big rejection
    lower_wick = float(last["close"]) - float(last["low"])
    wick_ok = lower_wick <= MAX_WICK_FRAC * rng

    return rsi_ok and candle_ok and trend_ok and wick_ok

# ======================================================
# SIGNAL BUILDER + REGISTER FOR TRACKING
# ======================================================

def send_signal(ex_name: str, symbol: str, side: str, entry_price: float):
    # Simple percentage-based scalp stop/TP (informational)
    if side == "LONG":
        stop = entry_price * (1.0 - STOP_PCT)
        tp = entry_price * (1.0 + TP_PCT)
    else:
        stop = entry_price * (1.0 + STOP_PCT)
        tp = entry_price * (1.0 - TP_PCT)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    msg = (
        f"📌 RSI SCALP {side}\n\n"
        f"Exchange: {ex_name}\n"
        f"Pair: {symbol}\n"
        f"Entry: {round(entry_price, 6)}\n"
        f"Stop: {round(stop, 6)}\n"
        f"Target: {round(tp, 6)}\n"
        f"RR (approx): 1:2\n"
        f"Time: {ts}"
    )

    send_telegram(msg)
    log.info(f"Signal sent → {ex_name} {symbol} {side}")

    entry_ts = int(time.time())
    trade_key = f"{ex_name}|{symbol}|{side}|{entry_ts}"

    with open_trades_lock:
        open_trades[trade_key] = {
            "ex_name": ex_name,
            "symbol": symbol,
            "side": side,
            "entry": float(entry_price),
            "stop": float(stop),
            "tp": float(tp),
            "start_ts": entry_ts,
        }

# ======================================================
# TRACKER LOOP — TP/SL updates + Duration
# ======================================================

def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    mins = seconds // 60
    hrs = mins // 60
    mins = mins % 60
    if hrs > 0:
        return f"{hrs} hrs {mins} min"
    return f"{mins} min"

def tracker_loop():
    log.info("Tracker loop started.")
    while True:
        time.sleep(TRACK_INTERVAL)

        with open_trades_lock:
            keys = list(open_trades.keys())

        for k in keys:
            try:
                with open_trades_lock:
                    t = open_trades.get(k)
                if not t:
                    continue

                ex = get_ex_cached(t["ex_name"])
                if not ex:
                    continue

                ticker = ex.fetch_ticker(t["symbol"])
                last_price = float(ticker.get("last") or ticker.get("close") or 0)
                if last_price <= 0:
                    continue

                elapsed = int(time.time() - t["start_ts"])
                duration = format_duration(elapsed)

                side = t["side"]
                stop = t["stop"]
                tp = t["tp"]

                if side == "LONG":
                    if last_price <= stop:
                        send_telegram(
                            f"❌ STOP HIT\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {side}\n"
                            f"Time Period: {duration}\n"
                            f"Price: {last_price}"
                        )
                        with open_trades_lock:
                            open_trades.pop(k, None)
                        continue

                    if last_price >= tp:
                        send_telegram(
                            f"✅ TARGET HIT\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {side}\n"
                            f"Time Period: {duration}\n"
                            f"Hit Price: {tp}"
                        )
                        with open_trades_lock:
                            open_trades.pop(k, None)
                        continue
                else:
                    if last_price >= stop:
                        send_telegram(
                            f"❌ STOP HIT\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {side}\n"
                            f"Time Period: {duration}\n"
                            f"Price: {last_price}"
                        )
                        with open_trades_lock:
                            open_trades.pop(k, None)
                        continue

                    if last_price <= tp:
                        send_telegram(
                            f"✅ TARGET HIT\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {side}\n"
                            f"Time Period: {duration}\n"
                            f"Hit Price: {tp}"
                        )
                        with open_trades_lock:
                            open_trades.pop(k, None)
                        continue

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# SCANNER LOOP
# ======================================================

def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        for ex_name in EXCHANGES:
            ex = get_ex_cached(ex_name)
            if not ex:
                continue

            pairs = get_pairs(ex)
            for symbol in pairs:
                try:
                    df = get_df(ex, symbol, TF_EXEC)
                    if df is None or len(df) < (RSI_PERIOD + 5):
                        continue

                    last = df.iloc[-1]
                    price = float(last["close"])

                    if long_setup(df) and allow(symbol, "LONG"):
                        send_signal(ex_name, symbol, "LONG", price)

                    if short_setup(df) and allow(symbol, "SHORT"):
                        send_signal(ex_name, symbol, "SHORT", price)

                except Exception as e:
                    log.error(f"Scanner error {ex_name} {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "RSI SCALPING BOT (LOOSER PRO, MULTI-EXCHANGE) RUNNING"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
