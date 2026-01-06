# ======================================================
# ADVANCED S&D SCALPING BOT — 70% MODE (HIGH RETURN + TP TRACKING)
# LIMIT-ONLY • HIGH ACCURACY • R-BASED TARGETS • TP HIT UPDATES
# STRUCTURE PRESERVED (Render-ready)
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
log = logging.getLogger("SDBOT_70_R")

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

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 80))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 12))
WINDOW = int(os.getenv("WINDOW", 1800))

# Track open trades for TP/SL hits
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 15))

EXCHANGES = ["binance", "binance_futures", "bybit", "kucoin", "okx"]

recent_signals = {}

# open trades registry (informational)
open_trades = {}
open_trades_lock = threading.Lock()

# Use coherent allocations + coherent TP math
TP_ALLOCS = [40, 40, 20]  # TP1/TP2/TP3 size splits (percent)

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
    """Notify chats when bot starts."""
    msg = (
        "🚀 *ADVANCED S&D BOT — 70% MODE (HIGH RETURN + TRACKING)*\n\n"
        "Mode: LIMIT ONLY (informational)\n"
        "Filters: Strong Displacement + Structure + Trend\n"
        "Targets: R-based (TP1=1R, TP2=2R, TP3=3R)\n"
        f"TP Splits: {TP_ALLOCS[0]}% / {TP_ALLOCS[1]}% / {TP_ALLOCS[2]}%\n"
        "Updates: TP/SL hit + Profit% + Time Period\n\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n"
        f"Scan Interval: {SCAN_INTERVAL}s | Track Interval: {TRACK_INTERVAL}s\n"
    )
    send_telegram(msg)
    log.info(f"Startup message sent → chats: {CHAT_IDS}")

# ======================================================
# DUPLICATE PROTECTION
# ======================================================

def allow(symbol, direction):
    now = time.time()
    key = f"{symbol}_{direction}"

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

def add_indicators(df):
    df["ema9"]  = df["close"].ewm(span=9).mean()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    df["vol_sma"] = df["volume"].rolling(20).mean()

    df["atr_raw"] = df["high"] - df["low"]
    df["atr"] = df["atr_raw"].rolling(14).mean()
    df["atr_sma"] = df["atr"].rolling(14).mean()

    df["range"] = df["high"] - df["low"]
    return df

def get_df(ex, symbol, tf):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=120)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
    except Exception as e:
        log.error(f"Fetch error {symbol} {tf}: {e}")
        return None

# ======================================================
# EXCHANGES
# ======================================================

def get_ex(name):
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

def get_ex_cached(name):
    if name in EX_INSTANCES and EX_INSTANCES[name]:
        return EX_INSTANCES[name]
    ex = get_ex(name)
    EX_INSTANCES[name] = ex
    return ex

def get_pairs(ex):
    try:
        mk = ex.load_markets()
        return [s for s in mk if s.endswith("USDT")][:PAIR_LIMIT]
    except:
        return []

# ======================================================
# TOP MOVERS
# ======================================================

def detect_top_movers(ex):
    movers = []
    pairs = get_pairs(ex)

    for s in pairs:
        df = get_df(ex, s, "15m")
        if df is None or len(df) < 20:
            continue

        pct_change = (df["close"].iloc[-1] - df["close"].iloc[-4]) / df["close"].iloc[-4] * 100
        vol_ratio  = df["volume"].iloc[-1] / (df["vol_sma"].iloc[-1] + 1e-10)

        score = pct_change * 0.55 + vol_ratio * 0.45
        movers.append((s, score))

    movers_sorted = sorted(movers, key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers_sorted[:TOP_MOVER_COUNT]]

# ======================================================
# CORE STRATEGY (UNCHANGED)
# ======================================================

def trend_long(df5, df15):
    return (
        df5["ema9"].iloc[-1] > df5["ema20"].iloc[-1] > df5["ema50"].iloc[-1] and
        df15["ema9"].iloc[-1] > df15["ema20"].iloc[-1] > df15["ema50"].iloc[-1]
    )

def trend_short(df5, df15):
    return (
        df5["ema9"].iloc[-1] < df5["ema20"].iloc[-1] < df5["ema50"].iloc[-1] and
        df15["ema9"].iloc[-1] < df15["ema20"].iloc[-1] < df15["ema50"].iloc[-1]
    )

def volatility_ok(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return last["atr"] > last["atr_sma"] and last["atr"] > prev["atr"] * 1.02

def volume_ok(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    return last["volume"] > last["vol_sma"] * 2.0 and last["volume"] > prev["volume"]

def find_recent_swing_high(df):
    for i in range(len(df)-3, 2, -1):
        if df["high"].iloc[i] > df["high"].iloc[i-1] and df["high"].iloc[i] > df["high"].iloc[i+1]:
            return df["high"].iloc[i]
    return None

def find_recent_swing_low(df):
    for i in range(len(df)-3, 2, -1):
        if df["low"].iloc[i] < df["low"].iloc[i-1] and df["low"].iloc[i] < df["low"].iloc[i+1]:
            return df["low"].iloc[i]
    return None

def breakout_long(df5, df15):
    last = df5.iloc[-1]
    price = last["close"]

    if not trend_long(df5, df15):
        return False
    if not volatility_ok(df5) or not volume_ok(df5):
        return False

    swing_high = find_recent_swing_high(df5)
    if swing_high is None or price <= swing_high * 1.0005:
        return False

    body = last["close"] - last["open"]
    return body > 0 and body >= 0.55 * last["range"]

def breakout_short(df5, df15):
    last = df5.iloc[-1]
    price = last["close"]

    if not trend_short(df5, df15):
        return False
    if not volatility_ok(df5) or not volume_ok(df5):
        return False

    swing_low = find_recent_swing_low(df5)
    if swing_low is None or price >= swing_low * 0.9995:
        return False

    body = last["open"] - last["close"]
    return body > 0 and body >= 0.55 * last["range"]

# ======================================================
# 70% MODE FILTER
# ======================================================

def strong_displacement(df):
    last = df.iloc[-1]
    return (
        last["atr"] >= last["atr_sma"] * 2.0 and
        last["volume"] >= last["vol_sma"] * 2.0
    )

# ======================================================
# TRACKING HELPERS
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

def calc_profit_pct(entry: float, price: float, direction: str, leverage: int) -> float:
    # informational estimate; ignores fees/funding/slippage
    if direction == "LONG":
        raw = (price - entry) / entry * 100.0
    else:
        raw = (entry - price) / entry * 100.0
    return raw * leverage

def weighted_realized_pct(full_profit_pct: float, alloc_pct: int) -> float:
    return full_profit_pct * (alloc_pct / 100.0)

def build_r_based_tps(entry: float, stop: float, direction: str):
    R = abs(entry - stop)
    if R <= 0:
        return None
    if direction == "LONG":
        return [entry + 1.0 * R, entry + 2.0 * R, entry + 3.0 * R]
    return [entry - 1.0 * R, entry - 2.0 * R, entry - 3.0 * R]

# ======================================================
# SIGNAL BUILDER (R-BASED, HIGH RETURN) + REGISTER FOR TRACKING
# ======================================================

def send_signal(ex_name: str, symbol: str, direction: str, entry_price: float, atr: float):
    # Stop anchored to ATR (your existing approach)
    stop = entry_price - 1.2 * atr if direction == "LONG" else entry_price + 1.2 * atr
    stop_pct = abs(entry_price - stop) / entry_price * 100

    # 70% mode stop window enforcement
    if stop_pct < 0.35 or stop_pct > 0.90:
        return

    # Leverage mapping (lower risk => higher leverage)
    leverage = 60 if stop_pct < 0.6 else 30
    risk = "LOW" if leverage >= 50 else "MEDIUM"

    # R-based targets (coherent, not random)
    tps = build_r_based_tps(entry_price, stop, direction)
    if not tps:
        return
    tp1, tp2, tp3 = tps

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (
        f"📌 LIMIT {direction} (70% MODE)\n\n"
        f"Exchange: {ex_name}\n"
        f"Pair: {symbol}\n"
        f"Entry: {round(entry_price,6)}\n"
        f"Stop: {round(stop,6)}\n\n"
        f"TP1: {round(tp1,6)} ({TP_ALLOCS[0]}%)\n"
        f"TP2: {round(tp2,6)} ({TP_ALLOCS[1]}%)\n"
        f"TP3: {round(tp3,6)} ({TP_ALLOCS[2]}%)\n\n"
        f"Leverage: {leverage}x\n"
        f"Risk Level: {risk}\n"
        f"Time: {ts}"
    )

    send_telegram(msg)
    log.info(f"Signal sent → {ex_name} {symbol} {direction}")

    # Register trade for TP/SL tracking
    entry_ts = int(time.time())
    trade_key = f"{ex_name}|{symbol}|{direction}|{entry_ts}"

    with open_trades_lock:
        open_trades[trade_key] = {
            "ex_name": ex_name,
            "symbol": symbol,
            "direction": direction,
            "entry": float(entry_price),
            "stop": float(stop),
            "tps": [float(tp1), float(tp2), float(tp3)],
            "tp_allocs": TP_ALLOCS[:],
            "tp_hits": [False, False, False],
            "leverage": int(leverage),
            "start_ts": entry_ts,
            "realized_pct": 0.0,
        }

# ======================================================
# TRACKER LOOP — TP/SL updates with Profit% + Duration
# ======================================================

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

                direction = t["direction"]
                entry = t["entry"]
                leverage = t["leverage"]

                elapsed = int(time.time() - t["start_ts"])
                duration = format_duration(elapsed)

                # STOP HIT?
                stop = t["stop"]
                stop_hit = (last_price <= stop) if direction == "LONG" else (last_price >= stop)
                if stop_hit:
                    pnl = calc_profit_pct(entry, last_price, direction, leverage)
                    send_telegram(
                        f"❌ STOP HIT\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Side: {direction}\n"
                        f"Profit (full-size): {pnl:.1f}%\n"
                        f"Cumulative realized: {t.get('realized_pct',0.0):.1f}%\n"
                        f"Time Period: {duration}\n"
                        f"Price: {last_price}"
                    )
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # TP HITS
                tps = t["tps"]
                allocs = t.get("tp_allocs", TP_ALLOCS)
                hit_any = False

                for i, tp in enumerate(tps):
                    if t["tp_hits"][i]:
                        continue

                    tp_hit = (last_price >= tp) if direction == "LONG" else (last_price <= tp)
                    if tp_hit:
                        t["tp_hits"][i] = True
                        hit_any = True

                        # Profit% measured at the TP level for clean reporting
                        full_pnl_at_tp = calc_profit_pct(entry, tp, direction, leverage)
                        realized_add = weighted_realized_pct(full_pnl_at_tp, allocs[i])
                        t["realized_pct"] = float(t.get("realized_pct", 0.0)) + realized_add

                        label = "TP1" if i == 0 else ("TP2" if i == 1 else "TP3")

                        send_telegram(
                            f"✅ {label} HIT ({allocs[i]}%)\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {direction}\n"
                            f"Profit (full-size): {full_pnl_at_tp:.1f}%\n"
                            f"Realized add: {realized_add:.1f}%\n"
                            f"Cumulative realized: {t['realized_pct']:.1f}%\n"
                            f"Time Period: {duration}\n"
                            f"Hit Price: {tp}"
                        )

                if hit_any:
                    with open_trades_lock:
                        if k in open_trades:
                            open_trades[k]["tp_hits"] = t["tp_hits"]
                            open_trades[k]["realized_pct"] = t["realized_pct"]

                if all(t["tp_hits"]):
                    send_telegram(
                        f"🏁 ALL TARGETS HIT\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Side: {direction}\n"
                        f"Cumulative realized: {t['realized_pct']:.1f}%\n"
                        f"Time Period: {duration}"
                    )
                    with open_trades_lock:
                        open_trades.pop(k, None)

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# MAIN LOOP
# ======================================================

def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        for ex_name in EXCHANGES:
            ex = get_ex_cached(ex_name)
            if not ex:
                continue

            movers = detect_top_movers(ex)

            for symbol in movers:
                try:
                    df5 = get_df(ex, symbol, "5m")
                    df15 = get_df(ex, symbol, "15m")
                    if df5 is None or df15 is None:
                        continue

                    last = df5.iloc[-1]
                    atr = float(last["atr"])

                    if not strong_displacement(df5):
                        continue

                    if breakout_long(df5, df15):
                        if allow(symbol, "LONG"):
                            send_signal(ex_name, symbol, "LONG", float(last["close"]), atr)

                    if breakout_short(df5, df15):
                        if allow(symbol, "SHORT"):
                            send_signal(ex_name, symbol, "SHORT", float(last["close"]), atr)

                except Exception as e:
                    log.error(f"Scanner error {ex_name} {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "ADVANCED S&D BOT — 70% MODE + TP TRACKING RUNNING"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
