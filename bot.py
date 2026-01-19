# ======================================================
# CRT 15-MINUTE DEMAND + PUMP + FIB BOT (INFO ONLY)
# OKX + KUCOIN FUTURES • TOP MOVERS • 15m EXEC ONLY • 1h CONTEXT
# DEMAND ZONE (15m displacement) → PUMP (5–6% in 1–3 candles) → FIB 0.382–0.618
# ENTRY: bullish close ABOVE demand zone after retrace into (zone ∩ fib window)
# SL: choose ONE method (STRUCT or ATR) • TP: STRICT 1:1 ONLY (no partials)
# ONE TRADE PER ZONE • FIRST TAP ONLY • ZONE INVALID IF 15m CLOSE BELOW ZONE
# FILTERS: 1h bullish structure + (optional) low-volume filter + news blackout windows
# TRACKING: after 20 CLOSED TRADES → send win/loss rate summary
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
from typing import Optional, Dict, Any, List, Tuple

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("CRT_15M_BOT")

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Multi-chat (same pattern as your first bot)
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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 30))    # seconds
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 15))  # seconds

# Exchanges (ONLY OKX + KuCoin Futures)
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]  # hard clamp

# Universe / movers
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 160))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 18))
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 8_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 25))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Strategy TFs
TF_EXEC = "15m"   # fixed
TF_CONTEXT = "1h" # fixed

# Demand zone detection (15m)
BASE_LOOKBACK = int(os.getenv("BASE_LOOKBACK", 4))               # candles before impulse considered "base"
BASE_MAX_BODY_PCT = float(os.getenv("BASE_MAX_BODY_PCT", 0.45))  # base candles should be relatively small bodies
DISP_BODY_PCT_MIN = float(os.getenv("DISP_BODY_PCT_MIN", 0.60))  # displacement candle body >= 60% of range
DISP_RANGE_MULT = float(os.getenv("DISP_RANGE_MULT", 1.8))       # displacement range >= range_sma * mult
RANGE_SMA_LEN = int(os.getenv("RANGE_SMA_LEN", 20))

# Zone tap / validity
FIRST_TAP_ONLY = True
ZONE_INVALID_CLOSE_BELOW = True  # fixed per your rules

# Pump detection (15m)
PUMP_MIN_PCT = float(os.getenv("PUMP_MIN_PCT", 5.0))
PUMP_MAX_PCT = float(os.getenv("PUMP_MAX_PCT", 6.0))
PUMP_MAX_CANDLES = int(os.getenv("PUMP_MAX_CANDLES", 3))         # 1–3 candles
BREAK_LOOKBACK = int(os.getenv("BREAK_LOOKBACK", 20))            # "minor high" lookback

# Fibonacci window
FIB_MIN = float(os.getenv("FIB_MIN", 0.382))
FIB_MAX = float(os.getenv("FIB_MAX", 0.618))

# Entry rules
ENTRY_REQUIRES_BULLISH = True
ENTRY_CLOSE_ABOVE_ZONE_TOP = True

# Stop method (choose ONE and keep consistent)
# "STRUCT" = below demand zone bottom (lowest wick)
# "ATR"    = entry - 1x ATR(15m)
STOP_METHOD = os.getenv("STOP_METHOD", "STRUCT").strip().upper()
if STOP_METHOD not in ("STRUCT", "ATR"):
    STOP_METHOD = "STRUCT"

# ATR
ATR_LEN = int(os.getenv("ATR_LEN", 14))

# Strict 1:1 TP only
RR = 1.0

# One trade per zone
ONE_TRADE_PER_ZONE = True

# Filters
# 1h bullish structure (simple implementation): EMA20 > EMA50 AND close > EMA20
CTX_EMA_FAST = int(os.getenv("CTX_EMA_FAST", 20))
CTX_EMA_SLOW = int(os.getenv("CTX_EMA_SLOW", 50))

# Low volume session filter (optional)
ENABLE_LOW_VOL_FILTER = os.getenv("ENABLE_LOW_VOL_FILTER", "1") == "1"
LOW_VOL_MULT = float(os.getenv("LOW_VOL_MULT", 0.7))  # last vol must be >= LOW_VOL_MULT * vol_sma

# High-impact news filter:
# Provide blackout windows as UTC ranges:
# NEWS_BLACKOUT_UTC="2026-01-19T13:00/2026-01-19T15:00,2026-01-20T18:00/2026-01-20T19:00"
NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "").strip()

# Cooldowns (symbol+direction)
WINDOW = int(os.getenv("WINDOW", 1800))
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 7200))

# Position sizing (info)
ACCOUNT_USDT = float(os.getenv("ACCOUNT_USDT", 1000))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", 0.5))
MAX_NOTIONAL_USDT = float(os.getenv("MAX_NOTIONAL_USDT", 5000))
MIN_NOTIONAL_USDT = float(os.getenv("MIN_NOTIONAL_USDT", 25))

# Post-run stats
STATS_BATCH_SIZE = int(os.getenv("STATS_BATCH_SIZE", 20))

# ======================================================
# STATE
# ======================================================

recent_signals: Dict[str, float] = {}
penalty_cooldowns: Dict[str, float] = {}

open_trades: Dict[str, Dict[str, Any]] = {}
open_trades_lock = threading.Lock()

# Zone + setup state per (exchange,symbol)
# We keep the "active zone" and the "active pump/fib" until either traded or invalidated.
symbol_state: Dict[str, Dict[str, Any]] = {}

# Closed trade stats
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
                resp = requests.post(url, json={"chat_id": cid, "text": ch}, timeout=10)
                if resp.status_code != 200:
                    log.error(f"Telegram send failed ({resp.status_code}) for {cid}: {resp.text[:300]}")
            except Exception as e:
                log.error(f"Telegram error for {cid}: {e}")

def send_startup():
    msg = (
        "🧠 CRT 15m Strategy Bot (INFO ONLY)\n\n"
        "TFs: EXEC=15m (entries/exits only) | CONTEXT=1h\n"
        "Demand Zone: 15m bullish displacement after base\n"
        f"Pump: {PUMP_MIN_PCT:.1f}%–{PUMP_MAX_PCT:.1f}% in 1–{PUMP_MAX_CANDLES} candles, breaks minor high\n"
        f"Fib window: {FIB_MIN:.3f}–{FIB_MAX:.3f} retrace into (Demand Zone ∩ Fib)\n"
        "Entry: bullish candle close above demand zone\n"
        f"Stop method: {STOP_METHOD} | TP: strict 1:1 only\n"
        "Constraints: first tap only, one trade per zone, zone invalid if 15m close below zone\n"
        f"Filters: 1h bullish structure | low-vol filter={ENABLE_LOW_VOL_FILTER} | news blackout windows={bool(NEWS_BLACKOUT_UTC)}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

# ======================================================
# HELPERS: cooldowns
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
    if last is None:
        recent_signals[key] = now
        return True

    if now - last > WINDOW:
        recent_signals[key] = now
        return True

    return False

def apply_stop_penalty(ex_name: str, symbol: str, direction: str):
    now = time.time()
    key = _cd_key(ex_name, symbol, direction)
    penalty_cooldowns[key] = now + STOP_PENALTY_WINDOW
    recent_signals[key] = now

# ======================================================
# HELPERS: news blackout windows (UTC)
# ======================================================

def _parse_blackouts(raw: str) -> List[Tuple[int, int]]:
    """
    Returns list of (start_ts, end_ts) unix seconds (UTC).
    Input: "YYYY-MM-DDTHH:MM/YYYY-MM-DDTHH:MM, ..."
    """
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
# INDICATORS
# ======================================================

def add_indicators_15m(df: pd.DataFrame) -> pd.DataFrame:
    # Range + range SMA
    df["range"] = df["high"] - df["low"]
    df["range_sma"] = df["range"].rolling(RANGE_SMA_LEN).mean()

    # True range / ATR
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()

    # Volume SMA for low-vol filter
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
        name = name.strip()
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
    name = name.strip()
    if name in EX_INSTANCES and EX_INSTANCES[name]:
        return EX_INSTANCES[name]
    ex = get_ex(name)
    EX_INSTANCES[name] = ex
    return ex

# ======================================================
# QUALITY UNIVERSE
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
            try:
                qv = float(bv) * last
            except Exception:
                continue
        else:
            try:
                qv = float(qv)
            except Exception:
                continue

        if qv < MIN_QUOTE_VOL_USDT:
            continue

        bid = t.get("bid")
        ask = t.get("ask")
        if bid and ask:
            try:
                bid = float(bid)
                ask = float(ask)
            except Exception:
                bid, ask = None, None
        if bid and ask and bid > 0:
            spread_bps = ((ask - bid) / bid) * 10_000
            if spread_bps > MAX_SPREAD_BPS:
                continue

        out.append((symbol, qv))

    out.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in out[:PAIR_LIMIT]]

# ======================================================
# TOP MOVERS (for scanning efficiency, not strategy logic)
# ======================================================

def detect_top_movers(ex) -> list:
    movers = []
    pairs = build_quality_universe(ex)

    for s in pairs:
        df = get_df(ex, s, "1h")
        if df is None or len(df) < 30:
            continue
        # short-term mover proxy: last 3 hours change
        base = float(df["close"].iloc[-4])
        last = float(df["close"].iloc[-1])
        if base <= 0:
            continue
        pct_change = abs((last - base) / base * 100.0)
        movers.append((s, pct_change))

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
    """
    Finds the most recent valid demand zone based on:
    - base = last BASE_LOOKBACK candles before displacement
    - displacement candle = strong bullish candle with range expansion
    - zone top = highest close in base
    - zone bottom = lowest low (wick) in base
    """
    if len(df_15m) < (BASE_LOOKBACK + RANGE_SMA_LEN + 10):
        return None

    i = len(df_15m) - 1  # last candle index
    disp = df_15m.iloc[i]

    # displacement must be bullish and strong
    if float(disp["close"]) <= float(disp["open"]):
        return None
    if _candle_body_pct(disp) < DISP_BODY_PCT_MIN:
        return None

    # range expansion relative to baseline
    if pd.isna(disp["range_sma"]) or float(disp["range_sma"]) <= 0:
        return None
    if float(disp["range"]) < float(disp["range_sma"]) * DISP_RANGE_MULT:
        return None

    # base candles: immediately before displacement candle
    base_df = df_15m.iloc[i-BASE_LOOKBACK:i]
    if len(base_df) < BASE_LOOKBACK:
        return None

    # base should look like consolidation / small candles (loosely enforced)
    body_pcts = base_df.apply(_candle_body_pct, axis=1)
    if body_pcts.mean() > BASE_MAX_BODY_PCT:
        return None

    zone_top = float(base_df["close"].max())   # highest close before displacement
    zone_bottom = float(base_df["low"].min())  # lowest wick in the base

    if zone_top <= zone_bottom:
        return None

    return {
        "created_ts": int(disp["ts"]),
        "top": zone_top,
        "bottom": zone_bottom,
        "tapped": False,
        "tap_ts": None,
        "invalidated": False,
        "traded": False,
        "first_tap_only": True,
    }

def zone_invalidated(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    if not ZONE_INVALID_CLOSE_BELOW:
        return False
    last_close = float(df_15m["close"].iloc[-1])
    return last_close < float(zone["bottom"])

def detect_zone_tap(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    """
    Tap = price returns and at least one wick touches the zone,
    and candle does not close below zone bottom.
    """
    last = df_15m.iloc[-1]
    low = float(last["low"])
    close = float(last["close"])

    touched = low <= float(zone["top"]) and close >= float(zone["bottom"])
    not_closed_below = close >= float(zone["bottom"])
    return bool(touched and not_closed_below)

def detect_pump(df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """
    Pump = bullish move of 5–6% within 1–3 candles on 15m,
    measured from lowest low before pump to highest high of pump,
    must break previous minor high (BREAK_LOOKBACK).
    We search the most recent window ending at the latest candle.
    """
    if len(df_15m) < max(80, BREAK_LOOKBACK + 10):
        return None

    end = len(df_15m) - 1
    prev_minor_high = float(df_15m["high"].iloc[max(0, end - BREAK_LOOKBACK):end].max())

    best = None
    for n in range(1, PUMP_MAX_CANDLES + 1):
        start = end - (n - 1)
        if start < 2:
            continue
        window = df_15m.iloc[start:end+1]
        low_before = float(df_15m["low"].iloc[start-2:start+1].min())  # "lowest low before pump"
        high_of_pump = float(window["high"].max())

        if low_before <= 0:
            continue
        move_pct = (high_of_pump - low_before) / low_before * 100.0

        # must be in 5–6%
        if move_pct < PUMP_MIN_PCT or move_pct > PUMP_MAX_PCT:
            continue

        # must be bullish-ish window
        if float(window["close"].iloc[-1]) <= float(window["open"].iloc[0]):
            continue

        # must break minor high / internal liquidity
        if high_of_pump <= prev_minor_high:
            continue

        best = {
            "start_idx": start,
            "end_idx": end,
            "swing_low": low_before,
            "swing_high": high_of_pump,
            "move_pct": move_pct,
            "pump_ts": int(df_15m["ts"].iloc[end]),
        }
        break

    return best

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

    # retrace into demand zone
    in_zone = l <= float(zone["top"]) and c >= float(zone["bottom"])  # wick touch and no close below
    if not in_zone:
        return False

    # fib confluence: close must be inside 0.382–0.618 window
    if not price_in_fib_window(c, fib):
        return False

    # bullish confirmation candle
    if ENTRY_REQUIRES_BULLISH and c <= o:
        return False

    # must close above demand zone (interpreted as above zone top)
    if ENTRY_CLOSE_ABOVE_ZONE_TOP and c <= float(zone["top"]):
        return False

    return True

# ======================================================
# TRADE BUILDING + REPORTING
# ======================================================

def recommended_position_size(entry: float, stop: float):
    stop_dist = abs(entry - stop)
    if entry <= 0 or stop_dist <= 0:
        return None
    stop_pct = (stop_dist / entry) * 100.0
    risk_usdt = ACCOUNT_USDT * (RISK_PCT_PER_TRADE / 100.0)
    notional = risk_usdt * (entry / stop_dist)
    notional = max(MIN_NOTIONAL_USDT, min(notional, MAX_NOTIONAL_USDT))
    return float(notional), float(notional), float(risk_usdt), float(stop_pct)  # notional, (placeholder), risk, stop%

def build_trade(ex_name: str, symbol: str, entry: float, zone: Dict[str, Any], df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    last = df_15m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None

    if STOP_METHOD == "STRUCT":
        stop = float(zone["bottom"])  # below lowest wick of zone (we'll subtract tiny buffer)
        stop = stop * (1.0 - 0.0002)  # 0.02% buffer to be "below"
    else:
        stop = entry - 1.0 * atr

    if stop <= 0 or stop >= entry:
        return None

    risk_dist = entry - stop
    tp = entry + RR * risk_dist  # strict 1:1

    pos = recommended_position_size(entry, stop)
    if pos:
        notional, _, risk_usdt, stop_pct = pos
    else:
        notional, risk_usdt, stop_pct = 0.0, 0.0, 0.0

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "LONG",
        "entry": float(entry),
        "stop": float(stop),
        "tp": float(tp),
        "status": "ACTIVE",
        "created_ts": now,
        "start_ts": now,
        "filled_ts": now,
        "notional_info": float(notional),
        "risk_usdt_info": float(risk_usdt),
        "stop_pct": float(stop_pct),
        "zone_created_ts": int(zone["created_ts"]),
    }

def send_signal(trade: Dict[str, Any], zone: Dict[str, Any], pump: Dict[str, Any], fib: Dict[str, float]):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (
        "📌 CRT 15m LONG (INFO ONLY)\n\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n\n"
        f"Demand Zone: top={zone['top']:.6f} | bottom={zone['bottom']:.6f}\n"
        f"Pump: {pump['move_pct']:.2f}% (1–{PUMP_MAX_CANDLES} candles) | swing low={pump['swing_low']:.6f} high={pump['swing_high']:.6f}\n"
        f"Fib: 0.382={fib['0.382']:.6f} | 0.5={fib['0.500']:.6f} | 0.618={fib['0.618']:.6f}\n\n"
        f"Entry (15m close): {trade['entry']:.6f}\n"
        f"Stop ({STOP_METHOD}): {trade['stop']:.6f} ({trade['stop_pct']:.2f}%)\n"
        f"TP (1:1 only): {trade['tp']:.6f}\n\n"
        f"Position (info): ~{trade['notional_info']:.0f} USDT notional\n"
        f"Risk (info): ~{trade['risk_usdt_info']:.2f} USDT (@{RISK_PCT_PER_TRADE:.2f}%)\n\n"
        f"Time: {ts}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} CRT15m LONG")

def _register_trade(trade: Dict[str, Any]):
    trade_key = f"{trade['ex_name']}|{trade['symbol']}|{trade['direction']}|{int(time.time())}"
    with open_trades_lock:
        open_trades[trade_key] = trade

# ======================================================
# TRADE TRACKER + STATS
# ======================================================

def _record_closed_trade(trade: Dict[str, Any], outcome: str, exit_price: float):
    rec = {
        "ex": trade["ex_name"],
        "symbol": trade["symbol"],
        "dir": trade["direction"],
        "entry": trade["entry"],
        "stop": trade["stop"],
        "tp": trade["tp"],
        "outcome": outcome,  # "WIN" or "LOSS"
        "exit_price": exit_price,
        "closed_ts": int(time.time()),
    }
    with stats_lock:
        closed_trades.append(rec)

        # Send stats every STATS_BATCH_SIZE closed trades
        if len(closed_trades) % STATS_BATCH_SIZE == 0:
            last_n = closed_trades[-STATS_BATCH_SIZE:]
            wins = sum(1 for x in last_n if x["outcome"] == "WIN")
            losses = sum(1 for x in last_n if x["outcome"] == "LOSS")
            total = max(1, len(last_n))
            win_rate = wins / total * 100.0
            loss_rate = losses / total * 100.0

            send_telegram(
                "📊 CRT BOT PERFORMANCE SNAPSHOT (LAST 20 CLOSED TRADES)\n\n"
                f"Closed trades counted: {total}\n"
                f"Wins: {wins} ({win_rate:.1f}%)\n"
                f"Losses: {losses} ({loss_rate:.1f}%)\n\n"
                "Notes:\n"
                "- This is based only on TP/SL hits tracked by the bot.\n"
                "- Because TP is strict 1:1, expectancy depends heavily on win rate.\n"
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
                last_price = float(ticker.get("last") or ticker.get("close") or 0.0)
                if last_price <= 0:
                    continue

                entry = float(t["entry"])
                stop = float(t["stop"])
                tp = float(t["tp"])

                # stop / tp checks (LONG only)
                if last_price <= stop:
                    send_telegram(
                        "❌ SL HIT\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Entry: {entry:.6f}\n"
                        f"Stop: {stop:.6f}\n"
                        f"Price: {last_price:.6f}\n\n"
                        "⚠️ Info only."
                    )
                    apply_stop_penalty(t["ex_name"], t["symbol"], "LONG")
                    _record_closed_trade(t, "LOSS", last_price)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if last_price >= tp:
                    send_telegram(
                        "✅ TP HIT (1:1)\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Entry: {entry:.6f}\n"
                        f"TP: {tp:.6f}\n"
                        f"Price: {last_price:.6f}\n\n"
                        "⚠️ Info only."
                    )
                    _record_closed_trade(t, "WIN", last_price)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# MAIN LOOP (scanner)
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

            symbols = detect_top_movers(ex)

            for symbol in symbols:
                try:
                    # Pull data
                    df_15m = get_df(ex, symbol, "15m")
                    df_1h = get_df(ex, symbol, "1h")
                    if df_15m is None or df_1h is None:
                        continue
                    if len(df_15m) < 120 or len(df_1h) < 80:
                        continue

                    # Filters
                    if not ctx_bullish_1h(df_1h):
                        continue
                    if not low_vol_ok(df_15m):
                        continue

                    skey = f"{ex_name}|{symbol}"
                    st = symbol_state.get(skey, {})

                    # 1) Zone invalidation
                    zone = st.get("zone")
                    if zone and not zone.get("invalidated", False):
                        if zone_invalidated(df_15m, zone):
                            zone["invalidated"] = True
                            st["zone"] = zone
                            st.pop("pump", None)
                            st.pop("fib", None)
                            symbol_state[skey] = st
                            continue

                    # 2) If no active zone, attempt to detect one (most recent displacement candle)
                    if not zone or zone.get("invalidated", False) or (ONE_TRADE_PER_ZONE and zone.get("traded", False)):
                        new_zone = detect_demand_zone(df_15m)
                        if new_zone:
                            st = {"zone": new_zone}
                            symbol_state[skey] = st
                        continue

                    # 3) First tap only
                    if not zone.get("tapped", False):
                        if detect_zone_tap(df_15m, zone):
                            zone["tapped"] = True
                            zone["tap_ts"] = int(df_15m["ts"].iloc[-1])
                            st["zone"] = zone
                            symbol_state[skey] = st
                        else:
                            continue
                    else:
                        # if first tap only and already tapped, we keep zone but do not allow another tap-based trade
                        if FIRST_TAP_ONLY and not st.get("pump"):
                            # We still allow pump detection after tap, but we will not "retap"
                            pass

                    # 4) Detect pump (5–6% in 1–3 candles)
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

                    # 5) Entry check (demand zone ∩ fib window + bullish close above zone)
                    if entry_conditions(df_15m, zone, fib):
                        if not allow(ex_name, symbol, "LONG"):
                            continue

                        entry_price = float(df_15m["close"].iloc[-1])  # entry at close of confirmation candle (15m)
                        trade = build_trade(ex_name, symbol, entry_price, zone, df_15m)
                        if not trade:
                            continue

                        # enforce "one trade per demand zone"
                        zone["traded"] = True
                        st["zone"] = zone
                        symbol_state[skey] = st

                        send_signal(trade, zone, pump, fib)
                        _register_trade(trade)

                except Exception as e:
                    log.error(f"Scanner error {ex_name} {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "CRT 15m BOT RUNNING (INFO ONLY) — OKX + KUCOIN FUTURES"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
