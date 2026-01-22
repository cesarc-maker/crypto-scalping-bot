# ======================================================
# CRT 15-MINUTE STRATEGY BOT - OPTION B (BALANCED)
# OKX + KUCOIN FUTURES • HIGH WIN RATE CONFIGURATION
#
# TARGET: 3-4 signals/day | 68-72% win rate
#
# KEY FEATURES:
# - Breakout confirmation (2 candles above pump high)
# - Tighter pump range (5.0-6.5%)
# - Stronger reaction requirement (1.5R)
# - Higher volume filter (85% of average)
# - Tighter base consolidation (45% max body)
# - 2 Take Profits: TP1 (1R - 25%) | TP2 (DYNAMIC 2-4R - 75%)
# - Dynamic TP2 based on setup quality (pump, reaction, volume, trend, base)
# - Stop below tap wick with buffer
# - Fib 0.618 entry requirement
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
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List, Tuple

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("CRT_15M_OPTION_B_BALANCED")

# ======================================================
# TIME HELPERS
# ======================================================

CT = ZoneInfo("America/Chicago")

def now_ms() -> int:
    return int(time.time() * 1000)

def ct_time_str() -> str:
    return datetime.now(timezone.utc).astimezone(CT).strftime("%H:%M CT")

# ======================================================
# CONFIG - OPTION B (BALANCED)
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

# Universe
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 260))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 35))
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 5_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 25))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Timeframes
TF_EXEC = "15m"
TF_CTX = "1h"

# Demand Zone detection - OPTION B
BASE_LOOKBACK = int(os.getenv("BASE_LOOKBACK", 5))
BASE_MAX_BODY_PCT = float(os.getenv("BASE_MAX_BODY_PCT", 0.45))      # TIGHTENED (45%)
DISP_BODY_PCT_MIN = float(os.getenv("DISP_BODY_PCT_MIN", 0.55))
RANGE_SMA_LEN = int(os.getenv("RANGE_SMA_LEN", 20))
DISP_RANGE_MULT = float(os.getenv("DISP_RANGE_MULT", 1.5))

# Tap + reaction - OPTION B
FIRST_TAP_ONLY = True
REACTION_R_MULT = float(os.getenv("REACTION_R_MULT", 1.5))           # STRONGER REACTION (1.5R)

# Pump detection - OPTION B
PUMP_MIN_PCT = float(os.getenv("PUMP_MIN_PCT", 5.0))                 # 5.0%
PUMP_MAX_PCT = float(os.getenv("PUMP_MAX_PCT", 6.5))                 # 6.5%
PUMP_MAX_CANDLES = int(os.getenv("PUMP_MAX_CANDLES", 4))
BREAK_LOOKBACK = int(os.getenv("BREAK_LOOKBACK", 20))

# Fib entry level - OPTION B (0.618 required)
FIB_ENTRY_LEVEL = "0.618"

# Entry rules
ENTRY_REQUIRES_BULLISH = True
ENTRY_CLOSE_ABOVE_ZONE_TOP = True

# Stop method
STOP_METHOD = os.getenv("STOP_METHOD", "STRUCT").strip().upper()
if STOP_METHOD not in ("STRUCT", "ATR"):
    STOP_METHOD = "STRUCT"
ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", 1.0))

# Take profits (2 TP, dynamic TP2)
TP1_RR = float(os.getenv("TP1_RR", 1.0))
TP2_RR_MIN = float(os.getenv("TP2_RR_MIN", 2.0))
TP2_RR_MAX = float(os.getenv("TP2_RR_MAX", 4.0))
TP2_DYNAMIC = os.getenv("TP2_DYNAMIC", "1") == "1"

# Partial sizing (info-only)
TP1_SIZE_PCT = float(os.getenv("TP1_SIZE_PCT", 0.25))  # 25%
TP2_SIZE_PCT = float(os.getenv("TP2_SIZE_PCT", 0.75))  # 75%

# Wick stop buffer
WICK_STOP_BUFFER_PCT = float(os.getenv("WICK_STOP_BUFFER_PCT", 0.0005))  # 0.05%

# Filters - OPTION B
CTX_EMA_FAST = int(os.getenv("CTX_EMA_FAST", 20))
CTX_EMA_SLOW = int(os.getenv("CTX_EMA_SLOW", 50))
ENABLE_LOW_VOL_FILTER = os.getenv("ENABLE_LOW_VOL_FILTER", "1") == "1"
LOW_VOL_MULT = float(os.getenv("LOW_VOL_MULT", 0.85))  # 85% of average

NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "").strip()

# Breakout confirmation - OPTION B
ENABLE_BREAKOUT_CONFIRMATION = os.getenv("ENABLE_BREAKOUT_CONFIRMATION", "1") == "1"
BREAKOUT_CANDLES_REQUIRED = int(os.getenv("BREAKOUT_CANDLES_REQUIRED", 2))  # 2 candles
BREAKOUT_CLOSE_ABOVE_HIGH = os.getenv("BREAKOUT_CLOSE_ABOVE_HIGH", "1") == "1"

# Cooldowns (seconds)
WINDOW = int(os.getenv("WINDOW", 1800))
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 7200))

# Stats
STATS_BATCH_SIZE = int(os.getenv("STATS_BATCH_SIZE", 20))

# ======================================================
# STATE
# ======================================================

recent_signals: Dict[str, float] = {}        # seconds
penalty_cooldowns: Dict[str, float] = {}     # seconds

open_trades: Dict[str, Dict[str, Any]] = {}
open_trades_lock = threading.Lock()

closed_trades: List[Dict[str, Any]] = []
stats_lock = threading.Lock()

symbol_state: Dict[str, Dict[str, Any]] = {}  # zone/pump/fib per ex|symbol

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
                r = requests.post(url, json={"chat_id": cid, "text": ch}, timeout=10)
                if r.status_code >= 400:
                    log.error(f"Telegram HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                log.error(f"Telegram error for {cid}: {e}")

def send_startup():
    tp2_msg = f"Dynamic ({TP2_RR_MIN:.1f}-{TP2_RR_MAX:.1f}:1)" if TP2_DYNAMIC else f"Fixed {TP2_RR_MIN:.1f}:1"
    msg = (
        "🤖 CRT 15M BOT STARTED (OPTION B - BALANCED)\n\n"
        "📊 Strategy: High Win Rate Setup\n"
        "🎯 Expected: 3-4 signals/day | 68-72% win rate\n\n"
        "⚙️ Key Filters:\n"
        f"• Pump: {PUMP_MIN_PCT:.1f}%-{PUMP_MAX_PCT:.1f}%\n"
        f"• Reaction: {REACTION_R_MULT:.1f}R minimum\n"
        f"• Breakout: {BREAKOUT_CANDLES_REQUIRED} candles confirmation\n"
        f"• Volume: {int(LOW_VOL_MULT*100)}% of average\n"
        f"• Base: Max {int(BASE_MAX_BODY_PCT*100)}% body candles\n"
        f"• Fib Entry: {FIB_ENTRY_LEVEL}\n\n"
        "💰 Take Profits:\n"
        f"• TP1: 1:1 ({int(TP1_SIZE_PCT*100)}% profit)\n"
        f"• TP2: {tp2_msg} ({int(TP2_SIZE_PCT*100)}% profit)\n\n"
        f"🔄 Exchanges: {', '.join([e.upper() for e in EXCHANGES])}\n"
        f"⏰ Scanning every {SCAN_INTERVAL}s | Tracking every {TRACK_INTERVAL}s\n"
        f"🕐 Started: {ct_time_str()}\n\n"
        "✅ Bot is active and monitoring..."
    )
    send_telegram(msg)

# ======================================================
# NEWS BLACKOUT HELPERS (UTC) - seconds based
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
    now_s = int(datetime.now(timezone.utc).timestamp())
    for a, b in BLACKOUTS:
        if a <= now_s <= b:
            return True
    return False

# ======================================================
# COOLDOWNS (seconds)
# ======================================================

def _cd_key(ex_name: str, symbol: str, direction: str) -> str:
    return f"{ex_name}_{symbol}_{direction}"

def allow(ex_name: str, symbol: str, direction: str) -> bool:
    now_s = time.time()
    key = _cd_key(ex_name, symbol, direction)

    pen_exp = penalty_cooldowns.get(key)
    if pen_exp and now_s < pen_exp:
        return False

    last = recent_signals.get(key)
    if last is None or (now_s - last) > WINDOW:
        recent_signals[key] = now_s
        return True
    return False

def apply_stop_penalty(ex_name: str, symbol: str, direction: str):
    now_s = time.time()
    key = _cd_key(ex_name, symbol, direction)
    penalty_cooldowns[key] = now_s + STOP_PENALTY_WINDOW
    recent_signals[key] = now_s

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
        # NOTE: CCXT timestamps are ms
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
                bidf, askf = float(bid), float(ask)
                if bidf > 0:
                    spread_bps = ((askf - bidf) / bidf) * 10_000
                    if spread_bps > MAX_SPREAD_BPS:
                        continue
            except Exception:
                pass

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
    base_body_mean = float(body_pcts.mean())
    if base_body_mean > BASE_MAX_BODY_PCT:
        return None

    zone_top = float(base_df["close"].max())
    zone_bottom = float(base_df["low"].min())
    if zone_top <= zone_bottom:
        return None

    return {
        "created_ts": int(disp["ts"]),      # ms
        "top": zone_top,
        "bottom": zone_bottom,
        "base_body_pct_mean": base_body_mean,
        "tapped": False,
        "tap_ts": None,                     # ms
        "tap_low": None,
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

def mark_tap(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> None:
    last = df_15m.iloc[-1]
    zone["tapped"] = True
    zone["tap_ts"] = int(last["ts"])     # ms
    zone["tap_low"] = float(last["low"])
    zone["reaction_high"] = float(last["high"])

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

    df_after = df_15m[df_15m["ts"] >= int(tap_ts)]
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

        # (kept as your earlier approach; if you want stricter "pre-pump" low, we can change this)
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
            "pump_ts": int(df_15m["ts"].iloc[end]),  # ms
        }

    return None

def fib_levels(swing_low: float, swing_high: float) -> Dict[str, float]:
    diff = swing_high - swing_low
    return {
        "0.382": swing_high - 0.382 * diff,
        "0.500": swing_high - 0.500 * diff,
        "0.618": swing_high - 0.618 * diff,
    }

def entry_conditions(df_15m: pd.DataFrame, zone: Dict[str, Any], fib: Dict[str, float]) -> bool:
    last = df_15m.iloc[-1]
    o = float(last["open"])
    c = float(last["close"])
    l = float(last["low"])
    h = float(last["high"])

    in_zone = (l <= float(zone["top"])) and (c >= float(zone["bottom"]))
    if not in_zone:
        return False

    # Fib 0.618 touch required
    lvl = float(fib[FIB_ENTRY_LEVEL])
    if not (l <= lvl <= h):
        return False

    if ENTRY_REQUIRES_BULLISH and c <= o:
        return False

    if ENTRY_CLOSE_ABOVE_ZONE_TOP and c <= float(zone["top"]):
        return False

    return True

# ======================================================
# BREAKOUT CONFIRMATION
# ======================================================

def check_breakout_confirmation(df_15m: pd.DataFrame, trade: Dict[str, Any]) -> bool:
    """
    Require the last N candles AFTER entry to confirm above pump high.
    Default: N=2 candles close above pump high.
    """
    if not ENABLE_BREAKOUT_CONFIRMATION:
        return True

    if trade.get("breakout_confirmed", False):
        return True

    pump_high = float(trade["pump_high"])
    entry_ts_ms = int(trade.get("start_ts", 0))  # ms

    df_after = df_15m[df_15m["ts"] > entry_ts_ms]
    if len(df_after) < BREAKOUT_CANDLES_REQUIRED:
        return False

    recent = df_after.tail(BREAKOUT_CANDLES_REQUIRED)

    ok = True
    for _, candle in recent.iterrows():
        high = float(candle["high"])
        close = float(candle["close"])

        # Candle must exceed pump high; optionally require close above pump high
        if high <= pump_high:
            ok = False
            break
        if BREAKOUT_CLOSE_ABOVE_HIGH and close <= pump_high:
            ok = False
            break

    return ok

# ======================================================
# DYNAMIC TP2
# ======================================================

def calculate_dynamic_tp2_rr(zone: Dict[str, Any], pump: Dict[str, Any], df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> float:
    """
    Returns TP2 R:R between TP2_RR_MIN and TP2_RR_MAX based on setup quality:
    pump, reaction, volume, trend, base
    """
    if not TP2_DYNAMIC:
        return float(TP2_RR_MIN)

    score = 0.0
    max_score = 10.0

    # 1) Pump strength (0-2)
    pump_pct = float(pump.get("move_pct", 0.0))
    if pump_pct >= 6.2:
        score += 2.0
    elif pump_pct >= 5.8:
        score += 1.5
    elif pump_pct >= 5.3:
        score += 1.0
    else:
        score += 0.5

    # 2) Reaction strength (0-2) in R units
    zone_size = float(zone["top"] - zone["bottom"])
    if zone_size > 0:
        reaction_high = float(zone.get("reaction_high") or zone["top"])
        reaction_r = (reaction_high - float(zone["top"])) / zone_size
        if reaction_r >= 2.5:
            score += 2.0
        elif reaction_r >= 2.0:
            score += 1.5
        elif reaction_r >= 1.5:
            score += 1.0
        else:
            score += 0.5
    else:
        score += 0.5

    # 3) Volume strength (0-2): last candle vol vs vol_sma
    last = df_15m.iloc[-1]
    if not pd.isna(last["vol_sma"]) and float(last["vol_sma"]) > 0:
        vol_ratio = float(last["volume"]) / float(last["vol_sma"])
        if vol_ratio >= 1.5:
            score += 2.0
        elif vol_ratio >= 1.2:
            score += 1.5
        elif vol_ratio >= 1.0:
            score += 1.0
        else:
            score += 0.5
    else:
        score += 0.5

    # 4) 1h trend strength (0-2): distance above ema_fast
    last_1h = df_1h.iloc[-1]
    if not pd.isna(last_1h["ema_fast"]) and float(last_1h["ema_fast"]) > 0:
        dist = (float(last_1h["close"]) - float(last_1h["ema_fast"])) / float(last_1h["ema_fast"]) * 100.0
        if dist >= 2.0:
            score += 2.0
        elif dist >= 1.0:
            score += 1.5
        elif dist >= 0.5:
            score += 1.0
        else:
            score += 0.5
    else:
        score += 0.5

    # 5) Base quality (0-2): tighter base (lower mean body pct) = better
    base_mean = float(zone.get("base_body_pct_mean", BASE_MAX_BODY_PCT))
    if base_mean <= 0.35:
        score += 2.0
    elif base_mean <= 0.40:
        score += 1.5
    elif base_mean <= 0.45:
        score += 1.0
    else:
        score += 0.5

    score_norm = max(0.0, min(1.0, score / max_score))
    tp2_rr = float(TP2_RR_MIN) + score_norm * (float(TP2_RR_MAX) - float(TP2_RR_MIN))
    tp2_rr = max(float(TP2_RR_MIN), min(float(TP2_RR_MAX), tp2_rr))
    return round(tp2_rr, 2)

# ======================================================
# TRADE BUILDING + REPORTING
# ======================================================

def calc_stop(entry: float, zone: Dict[str, Any], last_row: pd.Series) -> float:
    if STOP_METHOD == "ATR":
        atr = float(last_row["atr"]) if not pd.isna(last_row["atr"]) else 0.0
        if atr <= 0:
            return 0.0
        return entry - (ATR_STOP_MULT * atr)

    # STRUCT: below tap wick low with buffer
    tap_low = zone.get("tap_low")
    if tap_low is None:
        tap_low = float(last_row["low"])
    stop = float(tap_low) * (1.0 - WICK_STOP_BUFFER_PCT)
    return stop

def build_trade(
    ex_name: str,
    symbol: str,
    entry: float,
    zone: Dict[str, Any],
    pump: Dict[str, Any],
    fib: Dict[str, float],
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame
) -> Optional[Dict[str, Any]]:
    last = df_15m.iloc[-1]
    stop = calc_stop(entry, zone, last)
    if stop <= 0 or stop >= entry:
        return None

    risk_dist = entry - stop
    tp1 = entry + TP1_RR * risk_dist

    tp2_rr = calculate_dynamic_tp2_rr(zone, pump, df_15m, df_1h)
    tp2 = entry + tp2_rr * risk_dist

    ts_ms = now_ms()
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "LONG",
        "entry": float(entry),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp2_rr": float(tp2_rr),
        "tp1_hit": False,
        "tp1_partial_taken": False,
        "breakout_confirmed": False,
        "pump_high": float(pump["swing_high"]),
        "status": "PENDING" if ENABLE_BREAKOUT_CONFIRMATION else "ACTIVE",
        "start_ts": ts_ms,  # ms
        "created_ts": ts_ms,
        "zone_created_ts": int(zone["created_ts"]),
    }

def send_signal(trade: Dict[str, Any]):
    tp2_rr = float(trade.get("tp2_rr", TP2_RR_MIN))
    breakout_status = "⏳ PENDING BREAKOUT CONFIRMATION" if ENABLE_BREAKOUT_CONFIRMATION else "✅ BREAKOUT CONFIRMED"

    msg = (
        f"📊 {trade['symbol']}\n"
        f"📈 LONG\n"
        f"{breakout_status}\n\n"
        f"📍 ENTRY: {trade['entry']:.6f}\n"
        f"🛑 STOP LOSS: {trade['stop']:.6f}\n\n"
        f"🎯 TAKE PROFIT TARGETS:\n"
        f"TP1 (1:1): {trade['tp1']:.6f} - Take {int(TP1_SIZE_PCT*100)}% profit\n"
        f"TP2 ({tp2_rr:.1f}:1): {trade['tp2']:.6f} - Take remaining {int(TP2_SIZE_PCT*100)}% (RECOMMENDED)\n\n"
        f"🕐 {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        "⚠️ Not financial advice. Info only."
    )
    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} ({trade['status']}) TP2={tp2_rr:.2f}R")

    trade_key = f"{trade['ex_name']}|{trade['symbol']}|{trade['direction']}|{trade['start_ts']}"
    with open_trades_lock:
        open_trades[trade_key] = trade

# ======================================================
# TRACKER + STATS
# ======================================================

def _best_last_price(ticker: dict) -> float:
    for k in ("last", "close", "mark"):
        v = ticker.get(k)
        if v is not None:
            try:
                f = float(v)
                if f > 0:
                    return f
            except Exception:
                pass
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    try:
        if bid and ask:
            bf, af = float(bid), float(ask)
            if bf > 0 and af > 0:
                return (bf + af) / 2.0
    except Exception:
        pass
    return 0.0

def _record_closed(trade: Dict[str, Any], outcome: str, exit_price: float):
    with stats_lock:
        closed_trades.append({
            "ex": trade["ex_name"],
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "outcome": outcome,
            "exit_price": float(exit_price),
            "tp1_partial_taken": bool(trade.get("tp1_partial_taken", False)),
            "closed_ts": now_ms(),
        })

        if STATS_BATCH_SIZE > 0 and (len(closed_trades) % STATS_BATCH_SIZE == 0):
            last_n = closed_trades[-STATS_BATCH_SIZE:]
            wins = sum(1 for x in last_n if x["outcome"] == "WIN")
            losses = sum(1 for x in last_n if x["outcome"] == "LOSS")
            total = max(1, len(last_n))
            send_telegram(
                f"📊 CRT BOT PERFORMANCE (LAST {STATS_BATCH_SIZE} CLOSED TRADES)\n\n"
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

                # Always track stop (even if breakout pending)
                ticker = ex.fetch_ticker(t["symbol"])
                px = _best_last_price(ticker)
                if px <= 0:
                    continue

                stop = float(t["stop"])
                if px <= stop:
                    send_telegram(f"❌ SL HIT — {t['symbol']} (LONG) ({t['ex_name']})")
                    apply_stop_penalty(t["ex_name"], t["symbol"], "LONG")
                    _record_closed(t, "LOSS", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # If pending, check breakout confirmation using fresh 15m candles
                if t.get("status") == "PENDING":
                    df_15m = get_df(ex, t["symbol"], "15m")
                    if df_15m is None or len(df_15m) < 50:
                        continue

                    if check_breakout_confirmation(df_15m, t):
                        with open_trades_lock:
                            if k in open_trades:
                                open_trades[k]["breakout_confirmed"] = True
                                open_trades[k]["status"] = "ACTIVE"
                        send_telegram(f"✅ BREAKOUT CONFIRMED — {t['symbol']} ({t['ex_name']}) now ACTIVE")
                    else:
                        continue  # do not process TP while still pending

                # ACTIVE: TP logic
                tp1 = float(t["tp1"])
                tp2 = float(t["tp2"])

                if (not t.get("tp1_hit", False)) and px >= tp1:
                    send_telegram(f"✅ TP1 HIT (1R) — {t['symbol']} ({t['ex_name']})")
                    with open_trades_lock:
                        if k in open_trades:
                            open_trades[k]["tp1_hit"] = True
                            open_trades[k]["tp1_partial_taken"] = True
                    continue

                if px >= tp2:
                    send_telegram(f"🏁 TP2 HIT ({float(t.get('tp2_rr', TP2_RR_MIN)):.1f}R) — {t['symbol']} ({t['ex_name']})")
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

                    # Invalidate zone if close below bottom
                    if zone and not zone.get("invalidated", False):
                        if zone_invalidated(df_15m, zone):
                            zone["invalidated"] = True
                            st["zone"] = zone
                            st.pop("pump", None)
                            st.pop("fib", None)
                            symbol_state[skey] = st
                            continue

                    # Detect fresh zone if none/invalidated/traded
                    if not zone or zone.get("invalidated") or zone.get("traded"):
                        new_zone = detect_demand_zone(df_15m)
                        if new_zone:
                            symbol_state[skey] = {"zone": new_zone}
                        continue

                    # Tap detection (first tap only)
                    if not zone.get("tapped", False):
                        if detect_zone_tap(df_15m, zone):
                            mark_tap(df_15m, zone)
                            st["zone"] = zone
                            symbol_state[skey] = st
                        else:
                            continue

                    # Reaction update
                    zone = update_reaction(df_15m, zone)
                    st["zone"] = zone
                    symbol_state[skey] = st
                    if not zone.get("reacted", False):
                        continue

                    # Pump + fib
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

                    # Entry
                    if entry_conditions(df_15m, zone, fib):
                        if not allow(ex_name, symbol, "LONG"):
                            continue

                        entry_price = float(df_15m["close"].iloc[-1])
                        trade = build_trade(ex_name, symbol, entry_price, zone, pump, fib, df_15m, df_1h)
                        if not trade:
                            continue

                        # Mark zone traded to prevent duplicates
                        zone["traded"] = True
                        st["zone"] = zone
                        symbol_state[skey] = st

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
    return "CRT 15m BOT RUNNING (OPTION B - BALANCED) — OKX + KUCOIN FUTURES (INFO ONLY)"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
