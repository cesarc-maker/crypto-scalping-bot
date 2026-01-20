# ======================================================
# CRT FAST SIGNAL BOT — SPAM MODE (INFO ONLY)
# OKX + KUCOIN FUTURES • VERY FREQUENT SIGNALS (TARGET: 100–300+/DAY)
#
# STRUCTURE: SAME AS YOUR FIRST BOT
# - Multi-chat Telegram
# - Exchange cache
# - Quality universe + top movers
# - Scanner loop + tracker loop
# - Cooldowns + stop-penalty
# - TP/SL tracking
# - Win/Loss report every 20 CLOSED trades
#
# STRATEGY (FAST + LOOSE):
# - EXEC: 1m (OKX-safe)
# - TREND: 5m
# - CONTEXT: 1h
# - LONG: 1h bullish + 5m bullish, and 1m pullback near EMA20 + (optional) VWAP reclaim
# - SHORT: 1h bearish + 5m bearish, and 1m pullback near EMA20 + (optional) VWAP reject
#
# TP: STRICT 1:1 ONLY
# SL: ATR-based (1m)
#
# ⚠️ INFO ONLY. NOT FINANCIAL ADVICE. NO EXECUTION.
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
from typing import Dict, Any, Optional, List

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("CRT_FAST_SPAM")

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

CHAT_ID1 = os.getenv("CHAT_ID", "").strip()
CHAT_ID2 = os.getenv("CHAT_ID2", "").strip()
RAW_CHAT_IDS = os.getenv("CHAT_IDS", "")

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

# Cadence
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 5))      # fast scan
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 5))

# Exchanges (ONLY OKX + KuCoin Futures)
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]  # hard clamp

# Universe (Spam mode)
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 300))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 60))

MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 2_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 25))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Timeframes
TF_EXEC = os.getenv("TF_EXEC", "1m")    # OKX-safe
TF_TREND = os.getenv("TF_TREND", "5m")
TF_CTX = os.getenv("TF_CTX", "1h")

# Indicators
EMA_FAST = int(os.getenv("EMA_FAST", 9))
EMA_MID = int(os.getenv("EMA_MID", 20))
EMA_SLOW = int(os.getenv("EMA_SLOW", 50))

ATR_LEN = int(os.getenv("ATR_LEN", 14))

# Entry looseness (Spam mode)
PULLBACK_TO_EMA20_PCT = float(os.getenv("PULLBACK_TO_EMA20_PCT", 0.010))  # 1.0%
BODY_PCT = float(os.getenv("BODY_PCT", 0.25))                              # weak momentum ok

# VWAP checks (optional)
REQUIRE_VWAP = os.getenv("REQUIRE_VWAP", "0") == "1"  # default off for spam
VWAP_BUFFER = float(os.getenv("VWAP_BUFFER", 0.0))

# Stops + strict 1:1 TP
STOP_ATR_MULT = float(os.getenv("STOP_ATR_MULT", 1.0))
STOP_MIN_PCT = float(os.getenv("STOP_MIN_PCT", 0.10))
STOP_MAX_PCT = float(os.getenv("STOP_MAX_PCT", 0.70))
RR = 1.0

# Cooldowns (Spam mode)
WINDOW = int(os.getenv("WINDOW", 30))                 # 30s cooldown per symbol+direction
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 600))  # 10m penalty

# Stats report
STATS_BATCH_SIZE = int(os.getenv("STATS_BATCH_SIZE", 20))

# ======================================================
# STATE
# ======================================================

recent_signals: Dict[str, float] = {}
penalty_cooldowns: Dict[str, float] = {}

open_trades: Dict[str, Dict[str, Any]] = {}
open_trades_lock = threading.Lock()

closed_trades: List[Dict[str, Any]] = []
stats_lock = threading.Lock()

# ======================================================
# TELEGRAM
# ======================================================

TELEGRAM_API = "https://api.telegram.org"

def send_telegram(text: str):
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing")
        return
    if not CHAT_IDS:
        log.warning("No chat IDs configured")
        return

    MAX_LEN = 3800
    chunks = [text[i:i+MAX_LEN] for i in range(0, len(text), MAX_LEN)]

    for cid in CHAT_IDS:
        for ch in chunks:
            try:
                url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
                requests.post(url, json={"chat_id": cid, "text": ch}, timeout=10)
            except Exception as e:
                log.error(f"Telegram error for {cid}: {e}")

def send_startup():
    msg = (
        "⚡ CRT FAST SIGNAL BOT — SPAM MODE (INFO ONLY)\n\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n"
        f"TFs: EXEC={TF_EXEC} | TREND={TF_TREND} | CTX={TF_CTX}\n"
        f"Universe: PAIR_LIMIT={PAIR_LIMIT} | TOP_MOVERS={TOP_MOVER_COUNT}\n"
        f"Filters: qv≥{MIN_QUOTE_VOL_USDT/1e6:.1f}M | spread≤{MAX_SPREAD_BPS}bps\n"
        f"Entry: EMA20 pullback≤{PULLBACK_TO_EMA20_PCT*100:.2f}% + light momentum\n"
        f"Stops: {STOP_ATR_MULT}x ATR({ATR_LEN}) | TP: strict 1:1\n"
        f"Cooldown: {WINDOW}s | Stop-penalty: {STOP_PENALTY_WINDOW}s\n"
        f"VWAP required: {REQUIRE_VWAP}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

# ======================================================
# COOLDOWNS
# ======================================================

def _cd_key(ex_name: str, symbol: str, direction: str) -> str:
    return f"{ex_name}_{symbol}_{direction}"

def allow(ex_name: str, symbol: str, direction: str) -> bool:
    now = time.time()
    key = _cd_key(ex_name, symbol, direction)

    pen_exp = penalty_cooldowns.get(key)
    if pen_exp and now < pen_exp:
        return False

    last = recent_signals.get(key)
    if last is None or (now - last) > WINDOW:
        recent_signals[key] = now
        return True
    return False

def apply_stop_penalty(ex_name: str, symbol: str, direction: str):
    now = time.time()
    key = _cd_key(ex_name, symbol, direction)
    penalty_cooldowns[key] = now + STOP_PENALTY_WINDOW
    recent_signals[key] = now

# ======================================================
# INDICATORS
# ======================================================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_mid"]  = df["close"].ewm(span=EMA_MID, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()

    df["range"] = df["high"] - df["low"]

    # rolling VWAP approximation (if enabled)
    vwap_len = 60
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    df["vwap"] = pv.rolling(vwap_len).sum() / (df["volume"].rolling(vwap_len).sum() + 1e-12)

    df["vol_sma"] = df["volume"].rolling(20).mean()
    return df

def get_df(ex, symbol: str, tf: str, ex_name: str) -> Optional[pd.DataFrame]:
    try:
        # OKX-safe TF (avoid "Parameter bar error")
        if ex_name == "okx" and tf in ("2m", "3m"):
            tf = "1m"

        data = ex.fetch_ohlcv(symbol, tf, limit=220)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
    except Exception as e:
        log.error(f"Fetch error {symbol} {tf}: {e}")
        return None

# ======================================================
# EXCHANGES
# ======================================================

def get_ex(name: str):
    try:
        if name == "okx":
            return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        if name == "kucoin_futures":
            return ccxt.kucoinfutures({"enableRateLimit": True})
        return None
    except Exception as e:
        log.error(f"Exchange load error ({name}): {e}")
        return None

EX_INSTANCES: Dict[str, Any] = {}

def get_ex_cached(name: str):
    if name in EX_INSTANCES and EX_INSTANCES[name]:
        return EX_INSTANCES[name]
    ex = get_ex(name)
    EX_INSTANCES[name] = ex
    return ex

# ======================================================
# QUALITY UNIVERSE + MOVERS
# ======================================================

def build_quality_universe(ex) -> list:
    try:
        markets = ex.load_markets()
        tickers = ex.fetch_tickers()
    except Exception as e:
        log.error(f"Universe build error: {e}")
        return []

    out = []
    for symbol, t in tickers.items():
        m = markets.get(symbol)
        if not m:
            continue

        if ALLOW_ONLY_ACTIVE and m.get("active") is False:
            continue

        is_contract = bool(m.get("contract")) or bool(m.get("swap")) or bool(m.get("future"))
        if not is_contract:
            continue

        if m.get("quote") != "USDT":
            continue

        last = t.get("last") or t.get("close")
        if not last:
            continue
        try:
            last = float(last)
        except Exception:
            continue
        if last <= 0:
            continue

        qv = t.get("quoteVolume")
        if qv is None:
            bv = t.get("baseVolume")
            if bv is None:
                continue
            qv = float(bv) * last
        else:
            qv = float(qv)

        if qv < MIN_QUOTE_VOL_USDT:
            continue

        bid = t.get("bid")
        ask = t.get("ask")
        if bid and ask and float(bid) > 0:
            spread_bps = ((float(ask) - float(bid)) / float(bid)) * 10_000
            if spread_bps > MAX_SPREAD_BPS:
                continue

        out.append((symbol, qv))

    out.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in out[:PAIR_LIMIT]]

def detect_top_movers(ex, ex_name: str):
    movers = []
    pairs = build_quality_universe(ex)

    for s in pairs:
        df = get_df(ex, s, TF_TREND, ex_name)
        if df is None or len(df) < 40:
            continue
        base = float(df["close"].iloc[-4])
        last = float(df["close"].iloc[-1])
        if base <= 0:
            continue
        pct = abs((last - base) / base * 100.0)
        movers.append((s, pct))

    movers.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers[:TOP_MOVER_COUNT]]

# ======================================================
# STRATEGY (LONG + SHORT, loose)
# ======================================================

def ctx_bullish(df_ctx) -> bool:
    last = df_ctx.iloc[-1]
    return float(last["ema_mid"]) > float(last["ema_slow"]) and float(last["close"]) > float(last["ema_mid"])

def ctx_bearish(df_ctx) -> bool:
    last = df_ctx.iloc[-1]
    return float(last["ema_mid"]) < float(last["ema_slow"]) and float(last["close"]) < float(last["ema_mid"])

def trend_bullish(df_trend) -> bool:
    last = df_trend.iloc[-1]
    return float(last["ema_fast"]) > float(last["ema_mid"]) > float(last["ema_slow"])

def trend_bearish(df_trend) -> bool:
    last = df_trend.iloc[-1]
    return float(last["ema_fast"]) < float(last["ema_mid"]) < float(last["ema_slow"])

def pullback_ok(df_exec) -> bool:
    last = df_exec.iloc[-1]
    price = float(last["close"])
    ema20 = float(last["ema_mid"])
    if ema20 <= 0:
        return False
    dist = abs(price - ema20) / ema20
    return dist <= PULLBACK_TO_EMA20_PCT

def momentum_ok(df_exec, direction: str) -> bool:
    last = df_exec.iloc[-1]
    rng = float(last["range"])
    if rng <= 0:
        return False
    if direction == "LONG":
        body = float(last["close"]) - float(last["open"])
    else:
        body = float(last["open"]) - float(last["close"])
    if body <= 0:
        return False
    return (body / rng) >= BODY_PCT

def vwap_ok(df_exec, direction: str) -> bool:
    if not REQUIRE_VWAP:
        return True
    last = df_exec.iloc[-1]
    close = float(last["close"])
    vwap = float(last["vwap"])
    if direction == "LONG":
        return close >= vwap * (1.0 + VWAP_BUFFER)
    return close <= vwap * (1.0 - VWAP_BUFFER)

# ======================================================
# BUILD + SEND TRADE (STRICT 1:1)
# ======================================================

def build_trade(ex_name: str, symbol: str, direction: str, entry: float, atr: float) -> Optional[Dict[str, Any]]:
    if atr <= 0:
        return None

    stop = entry - STOP_ATR_MULT * atr if direction == "LONG" else entry + STOP_ATR_MULT * atr
    stop_pct = abs(entry - stop) / entry * 100.0
    if stop_pct < STOP_MIN_PCT or stop_pct > STOP_MAX_PCT:
        return None

    tp = entry + (entry - stop) * RR if direction == "LONG" else entry - (stop - entry) * RR

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": direction,
        "entry": float(entry),
        "stop": float(stop),
        "tp": float(tp),
        "status": "ACTIVE",
        "start_ts": now,
        "created_ts": now,
    }

def send_signal(trade: Dict[str, Any]):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (
        f"📌 FAST {trade['direction']} (1m) — STRICT 1:1 (INFO ONLY)\n\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n"
        f"Entry: {trade['entry']:.6f}\n"
        f"Stop: {trade['stop']:.6f}\n"
        f"TP (1:1): {trade['tp']:.6f}\n\n"
        f"Time: {ts}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

    trade_key = f"{trade['ex_name']}|{trade['symbol']}|{trade['direction']}|{int(time.time())}"
    with open_trades_lock:
        open_trades[trade_key] = trade

# ======================================================
# TRACKER + STATS
# ======================================================

def _record_closed(trade: Dict[str, Any], outcome: str, exit_price: float):
    with stats_lock:
        closed_trades.append({
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "outcome": outcome,
            "exit_price": float(exit_price),
            "closed_ts": int(time.time())
        })

        if len(closed_trades) % STATS_BATCH_SIZE == 0:
            last_n = closed_trades[-STATS_BATCH_SIZE:]
            wins = sum(1 for x in last_n if x["outcome"] == "WIN")
            losses = sum(1 for x in last_n if x["outcome"] == "LOSS")
            total = max(1, len(last_n))
            send_telegram(
                "📊 PERFORMANCE (LAST 20 CLOSED)\n\n"
                f"Wins: {wins} ({wins/total*100:.1f}%)\n"
                f"Losses: {losses} ({losses/total*100:.1f}%)\n\n"
                "⚠️ Info only."
            )

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
                px = float(ticker.get("last") or ticker.get("close") or 0.0)
                if px <= 0:
                    continue

                direction = t["direction"]
                stop = float(t["stop"])
                tp = float(t["tp"])

                stop_hit = (px <= stop) if direction == "LONG" else (px >= stop)
                tp_hit = (px >= tp) if direction == "LONG" else (px <= tp)

                if stop_hit:
                    send_telegram(f"❌ SL HIT — {t['symbol']} {direction} ({t['ex_name']})")
                    apply_stop_penalty(t["ex_name"], t["symbol"], direction)
                    _record_closed(t, "LOSS", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if tp_hit:
                    send_telegram(f"✅ TP HIT (1:1) — {t['symbol']} {direction} ({t['ex_name']})")
                    _record_closed(t, "WIN", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        for ex_name in EXCHANGES:
            ex = get_ex_cached(ex_name)
            if not ex:
                continue

            movers = detect_top_movers(ex, ex_name)

            for symbol in movers:
                try:
                    df_exec = get_df(ex, symbol, TF_EXEC, ex_name)
                    df_trend = get_df(ex, symbol, TF_TREND, ex_name)
                    df_ctx = get_df(ex, symbol, TF_CTX, ex_name)

                    if df_exec is None or df_trend is None or df_ctx is None:
                        continue
                    if len(df_exec) < 80 or len(df_trend) < 80 or len(df_ctx) < 80:
                        continue

                    last_exec = df_exec.iloc[-1]
                    atr = float(last_exec["atr"]) if not pd.isna(last_exec["atr"]) else 0.0
                    if atr <= 0:
                        continue

                    # LONG
                    if ctx_bullish(df_ctx) and trend_bullish(df_trend):
                        if pullback_ok(df_exec) and momentum_ok(df_exec, "LONG") and vwap_ok(df_exec, "LONG"):
                            if allow(ex_name, symbol, "LONG"):
                                entry = float(last_exec["close"])
                                trade = build_trade(ex_name, symbol, "LONG", entry, atr)
                                if trade:
                                    send_signal(trade)

                    # SHORT
                    if ctx_bearish(df_ctx) and trend_bearish(df_trend):
                        if pullback_ok(df_exec) and momentum_ok(df_exec, "SHORT") and vwap_ok(df_exec, "SHORT"):
                            if allow(ex_name, symbol, "SHORT"):
                                entry = float(last_exec["close"])
                                trade = build_trade(ex_name, symbol, "SHORT", entry, atr)
                                if trade:
                                    send_signal(trade)

                except Exception as e:
                    log.error(f"Scanner error {ex_name} {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "CRT FAST SPAM BOT RUNNING (INFO ONLY) — OKX + KUCOIN"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
