# ======================================================
# CRT 15-MINUTE STRATEGY BOT (INFO ONLY) — 3–5 SIGNALS/DAY TUNED
# OKX + KUCOIN FUTURES • SAME STRUCTURE AS YOUR LAST BOT
#
# TF SETUP:
# - EXECUTION: 15m (ALL entries/exits/validations happen on 15m only)
# - CONTEXT:   1h (filter only)
#
# TUNED FOR ~3–5 SIGNALS/DAY (MINIMAL LOOSENING, STILL CRT-STYLE):
# - Pump widened: 4.5%–7.0% and up to 4 candles
# - Demand displacement loosened a bit
# - Base loosened a bit
# - Wider universe + lower vol floor (helps KuCoin)
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
from typing import Dict, Any, Optional, List, Tuple

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("CRT_15M_TUNED_3_5")

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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 10))

# Exchanges (ONLY OKX + KuCoin Futures)
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]  # hard clamp

# Universe (TUNED wider)
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 260))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 35))
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 5_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 25))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Timeframes (fixed to spec)
TF_EXEC = "15m"
TF_CTX = "1h"

# Demand Zone detection tuning (TUNED)
BASE_LOOKBACK = int(os.getenv("BASE_LOOKBACK", 5))                # was 4
BASE_MAX_BODY_PCT = float(os.getenv("BASE_MAX_BODY_PCT", 0.55))   # was 0.45
DISP_BODY_PCT_MIN = float(os.getenv("DISP_BODY_PCT_MIN", 0.55))   # was 0.60
RANGE_SMA_LEN = int(os.getenv("RANGE_SMA_LEN", 20))
DISP_RANGE_MULT = float(os.getenv("DISP_RANGE_MULT", 1.5))        # was 1.8

# Tap + reaction
FIRST_TAP_ONLY = True
REACTION_R_MULT = float(os.getenv("REACTION_R_MULT", 1.0))        # keep 1R reaction requirement

# Pump detection (TUNED)
PUMP_MIN_PCT = float(os.getenv("PUMP_MIN_PCT", 4.5))              # was 5.0
PUMP_MAX_PCT = float(os.getenv("PUMP_MAX_PCT", 7.0))              # was 6.0
PUMP_MAX_CANDLES = int(os.getenv("PUMP_MAX_CANDLES", 4))          # was 3
BREAK_LOOKBACK = int(os.getenv("BREAK_LOOKBACK", 20))

# Fib window
FIB_MIN = float(os.getenv("FIB_MIN", 0.382))
FIB_MAX = float(os.getenv("FIB_MAX", 0.618))

# Entry rules
ENTRY_REQUIRES_BULLISH = True
ENTRY_CLOSE_ABOVE_ZONE_TOP = True

# Stop method (choose ONE and keep consistent)
STOP_METHOD = os.getenv("STOP_METHOD", "STRUCT").strip().upper()   # STRUCT or ATR
if STOP_METHOD not in ("STRUCT", "ATR"):
    STOP_METHOD = "STRUCT"

ATR_LEN = int(os.getenv("ATR_LEN", 14))

# TP: strict 1:1
RR = 1.0

# Filters
CTX_EMA_FAST = int(os.getenv("CTX_EMA_FAST", 20))
CTX_EMA_SLOW = int(os.getenv("CTX_EMA_SLOW", 50))

ENABLE_LOW_VOL_FILTER = os.getenv("ENABLE_LOW_VOL_FILTER", "1") == "1"
LOW_VOL_MULT = float(os.getenv("LOW_VOL_MULT", 0.7))

NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "").strip()

# Cooldowns
WINDOW = int(os.getenv("WINDOW", 1800))
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 7200))

# Stats
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

symbol_state: Dict[str, Dict[str, Any]] = {}  # zone/tap/reaction/pump/fib per ex|symbol

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
        "🧠 CRT 15m Strategy Bot (INFO ONLY) — tuned for ~3–5/day\n\n"
        "TFs: EXEC=15m (entries/exits only) | CONTEXT=1h (filter)\n"
        "Demand Zone: bullish displacement after base\n"
        "Tap: first tap only + must NOT close below zone + requires >=1R reaction\n"
        f"Pump: {PUMP_MIN_PCT:.1f}%–{PUMP_MAX_PCT:.1f}% in 1–{PUMP_MAX_CANDLES} (15m)\n"
        "Fib: 0.382/0.5/0.618 | Entry on bullish 15m close above zone top\n"
        f"Stop method: {STOP_METHOD} | TP: strict 1:1 only\n"
        f"Universe: pair_limit={PAIR_LIMIT} movers={TOP_MOVER_COUNT} qv≥{MIN_QUOTE_VOL_USDT/1e6:.1f}M\n\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

# ======================================================
# NEWS BLACKOUT HELPERS (UTC)
# ======================================================

def _parse_blackouts(raw: str) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    if not raw:
        return out
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    for p in parts:
        try:
            a, b = p.split("/")
            dt_a = datetime.strptime(a, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
            dt_b = datetime.strptime(b, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
            sa = int(dt_a.timestamp())
            sb = int(dt_b.timestamp())
            if sb > sa:
                out.append((sa, sb))
        except Exception:
            continue
    return out

BLACKOUTS = _parse_blackouts(NEWS_BLACKOUT_UTC)

def in_news_blackout() -> bool:
    if not BLACKOUTS:
        return False
    now = int(datetime.now(timezone.utc).timestamp())
    for a, b in BLACKOUTS:
        if a <= now <= b:
            return True
    return False

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

def add_indicators_15m(df: pd.DataFrame) -> pd.DataFrame:
    df["range"] = df["high"] - df["low"]
    df["range_sma"] = df["range"].rolling(RANGE_SMA_LEN).mean()

    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()

    df["vol_sma"] = df["volume"].rolling(20).mean()
    return df

def add_indicators_1h(df: pd.DataFrame) -> pd.DataFrame:
    df["ema_fast"] = df["close"].ewm(span=CTX_EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=CTX_EMA_SLOW, adjust=False).mean()
    return df

def get_df(ex, symbol: str, tf: str) -> Optional[pd.DataFrame]:
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=260)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        if tf == "15m":
            return add_indicators_15m(df)
        if tf == "1h":
            return add_indicators_1h(df)
        return df
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

def detect_top_movers(ex) -> list:
    movers = []
    pairs = build_quality_universe(ex)

    for s in pairs:
        df_1h = get_df(ex, s, "1h")
        if df_1h is None or len(df_1h) < 30:
            continue
        base = float(df_1h["close"].iloc[-4])
        last = float(df_1h["close"].iloc[-1])
        if base <= 0:
            continue
        pct = abs((last - base) / base * 100.0)
        movers.append((s, pct))

    movers.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers[:TOP_MOVER_COUNT]]

# ======================================================
# CORE STRATEGY FUNCTIONS
# ======================================================

def ctx_bullish_1h(df_1h: pd.DataFrame) -> bool:
    last = df_1h.iloc[-1]
    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]):
        return False
    return float(last["ema_fast"]) > float(last["ema_slow"]) and float(last["close"]) > float(last["ema_fast"])

def low_vol_ok(df_15m: pd.DataFrame) -> bool:
    if not ENABLE_LOW_VOL_FILTER:
        return True
    last = df_15m.iloc[-1]
    if pd.isna(last["vol_sma"]) or float(last["vol_sma"]) <= 0:
        return False
    return float(last["volume"]) >= float(last["vol_sma"]) * LOW_VOL_MULT

def _candle_body_pct(row) -> float:
    rng = float(row["high"] - row["low"])
    if rng <= 0:
        return 0.0
    body = abs(float(row["close"] - row["open"]))
    return body / rng

def detect_demand_zone(df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df_15m) < (BASE_LOOKBACK + RANGE_SMA_LEN + 10):
        return None

    i = len(df_15m) - 1
    disp = df_15m.iloc[i]

    # bullish displacement candle
    if float(disp["close"]) <= float(disp["open"]):
        return None
    if _candle_body_pct(disp) < DISP_BODY_PCT_MIN:
        return None
    if pd.isna(disp["range_sma"]) or float(disp["range_sma"]) <= 0:
        return None
    if float(disp["range"]) < float(disp["range_sma"]) * DISP_RANGE_MULT:
        return None

    base_df = df_15m.iloc[i-BASE_LOOKBACK:i]
    if len(base_df) < BASE_LOOKBACK:
        return None

    body_pcts = base_df.apply(_candle_body_pct, axis=1)
    if body_pcts.mean() > BASE_MAX_BODY_PCT:
        return None

    zone_top = float(base_df["close"].max())
    zone_bottom = float(base_df["low"].min())
    if zone_top <= zone_bottom:
        return None

    return {
        "created_ts": int(disp["ts"]),
        "top": zone_top,
        "bottom": zone_bottom,
        "tapped": False,
        "tap_ts": None,
        "reacted": False,
        "reaction_high": None,
        "invalidated": False,
        "traded": False,
    }

def zone_invalidated(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    last_close = float(df_15m["close"].iloc[-1])
    return last_close < float(zone["bottom"])

def detect_zone_tap(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    last = df_15m.iloc[-1]
    low = float(last["low"])
    close = float(last["close"])
    touched = low <= float(zone["top"])
    not_closed_below = close >= float(zone["bottom"])
    return bool(touched and not_closed_below)

def update_reaction(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> Dict[str, Any]:
    if not zone.get("tapped") or zone.get("reacted"):
        return zone

    zone_top = float(zone["top"])
    zone_bottom = float(zone["bottom"])
    R = max(0.0, zone_top - zone_bottom)
    if R <= 0:
        return zone

    tap_ts = zone.get("tap_ts")
    if not tap_ts:
        return zone

    df_after = df_15m[df_15m["ts"] >= tap_ts]
    if df_after.empty:
        return zone

    mx = float(df_after["high"].max())
    zone["reaction_high"] = mx

    target = zone_top + (REACTION_R_MULT * R)
    if mx >= target:
        zone["reacted"] = True

    return zone

def detect_pump(df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df_15m) < max(80, BREAK_LOOKBACK + 10):
        return None

    end = len(df_15m) - 1
    prev_minor_high = float(df_15m["high"].iloc[max(0, end - BREAK_LOOKBACK):end].max())

    for n in range(1, PUMP_MAX_CANDLES + 1):
        start = end - (n - 1)
        if start < 3:
            continue

        window = df_15m.iloc[start:end+1]
        low_before = float(df_15m["low"].iloc[start-2:start+1].min())
        high_of_pump = float(window["high"].max())
        if low_before <= 0:
            continue

        move_pct = (high_of_pump - low_before) / low_before * 100.0
        if move_pct < PUMP_MIN_PCT or move_pct > PUMP_MAX_PCT:
            continue

        if float(window["close"].iloc[-1]) <= float(window["open"].iloc[0]):
            continue

        if high_of_pump <= prev_minor_high:
            continue

        return {
            "start_idx": int(start),
            "end_idx": int(end),
            "swing_low": float(low_before),
            "swing_high": float(high_of_pump),
            "move_pct": float(move_pct),
            "pump_ts": int(df_15m["ts"].iloc[end]),
        }

    return None

def fib_levels(swing_low: float, swing_high: float) -> Dict[str, float]:
    diff = swing_high - swing_low
    return {
        "0.382": swing_high - 0.382 * diff,
        "0.500": swing_high - 0.500 * diff,
        "0.618": swing_high - 0.618 * diff,
    }

def price_in_fib_window(price: float, fib: Dict[str, float]) -> bool:
    lo = min(float(fib["0.382"]), float(fib["0.618"]))
    hi = max(float(fib["0.382"]), float(fib["0.618"]))
    return lo <= price <= hi

def entry_conditions(df_15m: pd.DataFrame, zone: Dict[str, Any], fib: Dict[str, float]) -> bool:
    last = df_15m.iloc[-1]
    o = float(last["open"])
    c = float(last["close"])
    l = float(last["low"])

    in_zone = (l <= float(zone["top"])) and (c >= float(zone["bottom"]))
    if not in_zone:
        return False

    if not price_in_fib_window(c, fib):
        return False

    if ENTRY_REQUIRES_BULLISH and c <= o:
        return False

    if ENTRY_CLOSE_ABOVE_ZONE_TOP and c <= float(zone["top"]):
        return False

    return True

# ======================================================
# TRADE BUILDING + REPORTING
# ======================================================

def build_trade(ex_name: str, symbol: str, entry: float, zone: Dict[str, Any], df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    last = df_15m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None

    if STOP_METHOD == "STRUCT":
        stop = float(zone["bottom"]) * (1.0 - 0.0002)
    else:
        stop = entry - 1.0 * atr

    if stop <= 0 or stop >= entry:
        return None

    risk_dist = entry - stop
    tp = entry + RR * risk_dist

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "LONG",
        "entry": float(entry),
        "stop": float(stop),
        "tp": float(tp),
        "status": "ACTIVE",
        "start_ts": now,
        "created_ts": now,
        "zone_created_ts": int(zone["created_ts"]),
    }

def send_signal(trade: Dict[str, Any], zone: Dict[str, Any], pump: Dict[str, Any], fib: Dict[str, float]):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (
        "📌 CRT 15m LONG (INFO ONLY)\n\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n\n"
        f"Demand Zone: top={zone['top']:.6f} | bottom={zone['bottom']:.6f}\n"
        f"Tap: first tap only | Reaction: {'OK' if zone.get('reacted') else 'PENDING'}\n"
        f"Pump: {pump['move_pct']:.2f}% (1–{PUMP_MAX_CANDLES} candles)\n"
        f"Fib: 0.382={fib['0.382']:.6f} | 0.5={fib['0.500']:.6f} | 0.618={fib['0.618']:.6f}\n\n"
        f"Entry (15m close): {trade['entry']:.6f}\n"
        f"Stop ({STOP_METHOD}): {trade['stop']:.6f}\n"
        f"TP (1:1 only): {trade['tp']:.6f}\n\n"
        f"Time: {ts}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} CRT15m LONG")

    trade_key = f"{trade['ex_name']}|{trade['symbol']}|{trade['direction']}|{int(time.time())}"
    with open_trades_lock:
        open_trades[trade_key] = trade

# ======================================================
# TRACKER + STATS
# ======================================================

def _record_closed(trade: Dict[str, Any], outcome: str, exit_price: float):
    with stats_lock:
        closed_trades.append({
            "ex": trade["ex_name"],
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "outcome": outcome,
            "exit_price": float(exit_price),
            "closed_ts": int(time.time()),
        })

        if len(closed_trades) % STATS_BATCH_SIZE == 0:
            last_n = closed_trades[-STATS_BATCH_SIZE:]
            wins = sum(1 for x in last_n if x["outcome"] == "WIN")
            losses = sum(1 for x in last_n if x["outcome"] == "LOSS")
            total = max(1, len(last_n))
            send_telegram(
                "📊 CRT BOT PERFORMANCE (LAST 20 CLOSED TRADES)\n\n"
                f"Closed: {total}\n"
                f"Wins: {wins} ({wins/total*100:.1f}%)\n"
                f"Losses: {losses} ({losses/total*100:.1f}%)\n\n"
                "⚠️ Info only. Not financial advice."
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

                stop = float(t["stop"])
                tp = float(t["tp"])

                if px <= stop:
                    send_telegram(f"❌ SL HIT — {t['symbol']} (LONG) ({t['ex_name']})")
                    apply_stop_penalty(t["ex_name"], t["symbol"], "LONG")
                    _record_closed(t, "LOSS", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if px >= tp:
                    send_telegram(f"✅ TP HIT (1:1) — {t['symbol']} (LONG) ({t['ex_name']})")
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
        if in_news_blackout():
            time.sleep(SCAN_INTERVAL)
            continue

        for ex_name in EXCHANGES:
            ex = get_ex_cached(ex_name)
            if not ex:
                continue

            movers = detect_top_movers(ex)

            for symbol in movers:
                try:
                    df_15m = get_df(ex, symbol, "15m")
                    df_1h = get_df(ex, symbol, "1h")
                    if df_15m is None or df_1h is None:
                        continue
                    if len(df_15m) < 140 or len(df_1h) < 80:
                        continue

                    if not ctx_bullish_1h(df_1h):
                        continue
                    if not low_vol_ok(df_15m):
                        continue

                    skey = f"{ex_name}|{symbol}"
                    st = symbol_state.get(skey, {})

                    zone = st.get("zone")

                    if zone and not zone.get("invalidated", False):
                        if zone_invalidated(df_15m, zone):
                            zone["invalidated"] = True
                            st["zone"] = zone
                            st.pop("pump", None)
                            st.pop("fib", None)
                            symbol_state[skey] = st
                            continue

                    if not zone or zone.get("invalidated") or zone.get("traded"):
                        new_zone = detect_demand_zone(df_15m)
                        if new_zone:
                            symbol_state[skey] = {"zone": new_zone}
                        continue

                    if not zone.get("tapped", False):
                        if detect_zone_tap(df_15m, zone):
                            zone["tapped"] = True
                            zone["tap_ts"] = int(df_15m["ts"].iloc[-1])
                            zone["reaction_high"] = float(df_15m["high"].iloc[-1])
                            st["zone"] = zone
                            symbol_state[skey] = st
                        else:
                            continue

                    zone = update_reaction(df_15m, zone)
                    st["zone"] = zone
                    symbol_state[skey] = st
                    if not zone.get("reacted", False):
                        continue

                    pump = st.get("pump")
                    if not pump:
                        pump = detect_pump(df_15m)
                        if pump:
                            st["pump"] = pump
                            st["fib"] = fib_levels(pump["swing_low"], pump["swing_high"])
                            symbol_state[skey] = st
                        else:
                            continue

                    fib = st.get("fib")
                    if not fib:
                        continue

                    if entry_conditions(df_15m, zone, fib):
                        if not allow(ex_name, symbol, "LONG"):
                            continue

                        entry_price = float(df_15m["close"].iloc[-1])
                        trade = build_trade(ex_name, symbol, entry_price, zone, df_15m)
                        if not trade:
                            continue

                        zone["traded"] = True
                        st["zone"] = zone
                        symbol_state[skey] = st

                        send_signal(trade, zone, pump, fib)

                except Exception as e:
                    log.error(f"Scanner error {ex_name} {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "CRT 15m STRATEGY BOT RUNNING (INFO ONLY) — OKX + KUCOIN (TUNED)"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
