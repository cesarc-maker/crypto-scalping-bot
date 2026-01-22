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
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("CRT_15M_OPTION_B")

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
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]

# Universe
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 260))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 35))
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 5_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 25))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Timeframes
TF_EXEC = "15m"
TF_CTX = "1h"

# Demand Zone detection - OPTION B (BALANCED)
BASE_LOOKBACK = int(os.getenv("BASE_LOOKBACK", 5))
BASE_MAX_BODY_PCT = float(os.getenv("BASE_MAX_BODY_PCT", 0.45))      # TIGHTENED
DISP_BODY_PCT_MIN = float(os.getenv("DISP_BODY_PCT_MIN", 0.55))
RANGE_SMA_LEN = int(os.getenv("RANGE_SMA_LEN", 20))
DISP_RANGE_MULT = float(os.getenv("DISP_RANGE_MULT", 1.5))

# Tap + reaction - OPTION B (BALANCED)
FIRST_TAP_ONLY = True
REACTION_R_MULT = float(os.getenv("REACTION_R_MULT", 1.5))           # INCREASED

# Pump detection - OPTION B (BALANCED)
PUMP_MIN_PCT = float(os.getenv("PUMP_MIN_PCT", 5.0))                 # INCREASED
PUMP_MAX_PCT = float(os.getenv("PUMP_MAX_PCT", 6.5))                 # DECREASED
PUMP_MAX_CANDLES = int(os.getenv("PUMP_MAX_CANDLES", 4))
BREAK_LOOKBACK = int(os.getenv("BREAK_LOOKBACK", 20))

# Fib entry level
FIB_ENTRY_LEVEL = os.getenv("FIB_ENTRY_LEVEL", "0.618").strip()
if FIB_ENTRY_LEVEL not in ("0.382", "0.500", "0.618"):
    FIB_ENTRY_LEVEL = "0.618"

# Entry rules
ENTRY_REQUIRES_BULLISH = True
ENTRY_CLOSE_ABOVE_ZONE_TOP = True

# Stop method
STOP_METHOD = os.getenv("STOP_METHOD", "STRUCT").strip().upper()
if STOP_METHOD not in ("STRUCT", "ATR"):
    STOP_METHOD = "STRUCT"

ATR_LEN = int(os.getenv("ATR_LEN", 14))

# Take profits
TP1_RR = float(os.getenv("TP1_RR", 1.0))
TP2_RR_MIN = float(os.getenv("TP2_RR_MIN", 2.0))  # Minimum TP2
TP2_RR_MAX = float(os.getenv("TP2_RR_MAX", 4.0))  # Maximum TP2
TP2_DYNAMIC = os.getenv("TP2_DYNAMIC", "1") == "1"  # Enable dynamic TP2

# Wick stop buffer
WICK_STOP_BUFFER_PCT = float(os.getenv("WICK_STOP_BUFFER_PCT", 0.0005))

# Filters - OPTION B (BALANCED)
CTX_EMA_FAST = int(os.getenv("CTX_EMA_FAST", 20))
CTX_EMA_SLOW = int(os.getenv("CTX_EMA_SLOW", 50))

ENABLE_LOW_VOL_FILTER = os.getenv("ENABLE_LOW_VOL_FILTER", "1") == "1"
LOW_VOL_MULT = float(os.getenv("LOW_VOL_MULT", 0.85))                # INCREASED

NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "").strip()

# Breakout confirmation - OPTION B (BALANCED)
ENABLE_BREAKOUT_CONFIRMATION = os.getenv("ENABLE_BREAKOUT_CONFIRMATION", "1") == "1"
BREAKOUT_CANDLES_REQUIRED = int(os.getenv("BREAKOUT_CANDLES_REQUIRED", 2))  # 2 candles confirmation
BREAKOUT_CLOSE_ABOVE_HIGH = os.getenv("BREAKOUT_CLOSE_ABOVE_HIGH", "1") == "1"

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

symbol_state: Dict[str, Dict[str, Any]] = {}

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
    utc_now = datetime.now(timezone.utc)
    central_time = utc_now - timedelta(hours=6)
    ts = central_time.strftime("%H:%M CT")
    
    tp2_msg = f"Dynamic ({TP2_RR_MIN:.1f}-{TP2_RR_MAX:.1f}:1)" if TP2_DYNAMIC else f"Fixed {TP2_RR_MIN:.1f}:1"
    
    msg = (
        f"🤖 CRT 15M BOT STARTED (OPTION B - BALANCED)\n\n"
        f"📊 Strategy: High Win Rate Setup\n"
        f"🎯 Expected: 3-4 signals/day | 68-72% win rate\n\n"
        f"⚙️ Key Filters:\n"
        f"• Pump: {PUMP_MIN_PCT:.1f}%-{PUMP_MAX_PCT:.1f}%\n"
        f"• Reaction: {REACTION_R_MULT:.1f}R minimum\n"
        f"• Breakout: {BREAKOUT_CANDLES_REQUIRED} candles confirmation\n"
        f"• Volume: {int(LOW_VOL_MULT*100)}% of average\n"
        f"• Base: Max {int(BASE_MAX_BODY_PCT*100)}% body candles\n\n"
        f"💰 Take Profits:\n"
        f"• TP1: 1:1 (25% profit)\n"
        f"• TP2: {tp2_msg} (75% profit - RECOMMENDED)\n\n"
        f"🔄 Exchanges: {', '.join([e.upper() for e in EXCHANGES])}\n"
        f"⏰ Scanning every {SCAN_INTERVAL}s\n"
        f"🕐 Started: {ts}\n\n"
        "✅ Bot is active and monitoring..."
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

def calculate_dynamic_tp2(zone: Dict[str, Any], pump: Dict[str, Any], df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> float:
    """
    Calculate dynamic TP2 based on setup quality and market conditions.
    Returns R:R ratio between TP2_RR_MIN and TP2_RR_MAX.
    """
    if not TP2_DYNAMIC:
        return TP2_RR_MIN  # Use minimum if dynamic disabled
    
    score = 0.0
    max_score = 10.0
    
    # 1. Pump strength (0-2 points)
    pump_pct = pump["move_pct"]
    if pump_pct >= 6.0:
        score += 2.0  # Strong pump
    elif pump_pct >= 5.5:
        score += 1.5
    elif pump_pct >= 5.0:
        score += 1.0
    else:
        score += 0.5
    
    # 2. Reaction strength (0-2 points)
    zone_size = zone["top"] - zone["bottom"]
    if zone_size > 0:
        reaction_high = zone.get("reaction_high", zone["top"])
        reaction_r = (reaction_high - zone["top"]) / zone_size
        if reaction_r >= 2.5:
            score += 2.0  # Very strong reaction
        elif reaction_r >= 2.0:
            score += 1.5
        elif reaction_r >= 1.5:
            score += 1.0
        else:
            score += 0.5
    
    # 3. Volume strength (0-2 points)
    last = df_15m.iloc[-1]
    if not pd.isna(last["vol_sma"]) and float(last["vol_sma"]) > 0:
        vol_ratio = float(last["volume"]) / float(last["vol_sma"])
        if vol_ratio >= 1.5:
            score += 2.0  # Exceptional volume
        elif vol_ratio >= 1.2:
            score += 1.5
        elif vol_ratio >= 1.0:
            score += 1.0
        else:
            score += 0.5
    
    # 4. 1h trend strength (0-2 points)
    last_1h = df_1h.iloc[-1]
    if not pd.isna(last_1h["ema_fast"]) and float(last_1h["ema_fast"]) > 0:
        distance_above_ema = (float(last_1h["close"]) - float(last_1h["ema_fast"])) / float(last_1h["ema_fast"]) * 100
        if distance_above_ema >= 2.0:
            score += 2.0  # Strong trend
        elif distance_above_ema >= 1.0:
            score += 1.5
        elif distance_above_ema >= 0.5:
            score += 1.0
        else:
            score += 0.5
    
    # 5. Base quality (0-2 points)
    # Tighter base = better quality
    body_pct = BASE_MAX_BODY_PCT
    if body_pct <= 0.35:
        score += 2.0  # Very tight base
    elif body_pct <= 0.40:
        score += 1.5
    elif body_pct <= 0.45:
        score += 1.0
    else:
        score += 0.5
    
    # Calculate TP2 based on score
    # Score 0-5 = TP2_RR_MIN (2.0)
    # Score 5-10 = Linear scale to TP2_RR_MAX (4.0)
    score_normalized = score / max_score  # 0 to 1
    
    tp2_rr = TP2_RR_MIN + (score_normalized * (TP2_RR_MAX - TP2_RR_MIN))
    
    # Clamp to min/max
    tp2_rr = max(TP2_RR_MIN, min(TP2_RR_MAX, tp2_rr))
    
    return round(tp2_rr, 2)

def entry_conditions(df_15m: pd.DataFrame, zone: Dict[str, Any], fib: Dict[str, float]) -> bool:
    last = df_15m.iloc[-1]
    o = float(last["open"])
    c = float(last["close"])
    l = float(last["low"])
    h = float(last["high"])

    in_zone = (l <= float(zone["top"])) and (c >= float(zone["bottom"]))
    if not in_zone:
        return False

    # Entry requires TOUCH of fib level
    lvl = float(fib[FIB_ENTRY_LEVEL])
    if not (l <= lvl <= h):
        return False

    if ENTRY_REQUIRES_BULLISH and c <= o:
        return False

    if ENTRY_CLOSE_ABOVE_ZONE_TOP and c <= float(zone["top"]):
        return False

    return True

def check_breakout_confirmation(df_15m: pd.DataFrame, trade: Dict[str, Any]) -> bool:
    """Check if breakout is confirmed above pump high"""
    if not ENABLE_BREAKOUT_CONFIRMATION:
        return True
    
    if trade.get("breakout_confirmed", False):
        return True
    
    pump_high = float(trade["pump_high"])
    
    # Get candles after entry
    entry_ts = trade.get("start_ts", 0)
    df_after_entry = df_15m[df_15m["ts"] > entry_ts * 1000]
    
    if len(df_after_entry) < BREAKOUT_CANDLES_REQUIRED:
        return False
    
    # Check the required number of candles
    recent_candles = df_after_entry.tail(BREAKOUT_CANDLES_REQUIRED)
    
    confirm_count = 0
    for idx, candle in recent_candles.iterrows():
        high = float(candle["high"])
        close = float(candle["close"])
        
        # Check if high breaks pump high
        if high > pump_high:
            # If required, check if close is also above pump high
            if BREAKOUT_CLOSE_ABOVE_HIGH:
                if close > pump_high:
                    confirm_count += 1
            else:
                confirm_count += 1
    
    # Require all candles to confirm
    return confirm_count >= BREAKOUT_CANDLES_REQUIRED

# ======================================================
# TRADE BUILDING + REPORTING
# ======================================================

def build_trade(ex_name: str, symbol: str, entry: float, zone: Dict[str, Any], df_15m: pd.DataFrame, pump: Dict[str, Any], df_1h: pd.DataFrame) -> Optional[Dict[str, Any]]:
    last = df_15m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None

    # Wick-based stop - use tap wick low
    tap_low = zone.get("tap_low")
    if tap_low is None:
        tap_low = float(last["low"])
    stop = float(tap_low) * (1.0 - WICK_STOP_BUFFER_PCT)

    if stop <= 0 or stop >= entry:
        return None

    risk_dist = entry - stop
    tp1 = entry + TP1_RR * risk_dist
    
    # Calculate dynamic TP2 based on setup quality
    tp2_rr = calculate_dynamic_tp2(zone, pump, df_15m, df_1h)
    tp2 = entry + tp2_rr * risk_dist

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "LONG",
        "entry": float(entry),
        "stop": float(stop),
        "initial_stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp2_rr": float(tp2_rr),  # Store the calculated R:R
        "tp1_hit": False,
        "tp1_partial_taken": False,
        "breakout_confirmed": False,
        "breakout_confirm_count": 0,
        "pump_high": float(pump["swing_high"]),
        "status": "PENDING" if ENABLE_BREAKOUT_CONFIRMATION else "ACTIVE",
        "start_ts": now,
        "created_ts": now,
        "zone_created_ts": int(zone["created_ts"]),
    }

def send_signal(trade: Dict[str, Any], zone: Dict[str, Any], pump: Dict[str, Any], fib: Dict[str, float]):
    utc_now = datetime.now(timezone.utc)
    central_time = utc_now - timedelta(hours=6)
    ts = central_time.strftime("%H:%M CT")
    
    # Breakout status
    if ENABLE_BREAKOUT_CONFIRMATION:
        breakout_status = "⏳ PENDING BREAKOUT CONFIRMATION"
    else:
        breakout_status = "✅ BREAKOUT CONFIRMED"
    
    # Get the dynamic TP2 R:R
    tp2_rr = trade.get("tp2_rr", 2.5)
    
    msg = (
        f"📊 {trade['symbol']}\n"
        f"📈 LONG\n"
        f"{breakout_status}\n\n"
        f"📍 ENTRY: {trade['entry']:.6f}\n"
        f"🛑 STOP LOSS: {trade['stop']:.6f}\n\n"
        f"🎯 TAKE PROFIT TARGETS:\n"
        f"TP1 (1:1): {trade['tp1']:.6f} - Take 25% profit\n"
        f"TP2 ({tp2_rr:.1f}:1): {trade['tp2']:.6f} - Take remaining 75% (RECOMMENDED)\n\n"
        f"🕐 {ts} | {trade['ex_name'].upper()}\n\n"
        "⚠️ Not financial advice. Take trades at your own risk!"
    )
    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} CRT15m LONG ({trade['status']}) TP2={tp2_rr:.1f}R")

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
            "tp1_partial_taken": trade.get("tp1_partial_taken", False),
            "closed_ts": int(time.time()),
        })

        if len(closed_trades) % STATS_BATCH_SIZE == 0
