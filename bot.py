# ======================================================
# MTF REVERSAL BOT
# 1H = reversal context
# 15m = structure confirmation
# 5m = execution
#
# PAPER TRADE / INFO ONLY
# - No live order execution
# - One position at a time
# - Telegram alerts
# - Hardcoded Telegram groups
# ======================================================

import os
import time
import ccxt
import pandas as pd
import threading
import requests
import logging
from flask import Flask
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List, Tuple

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("MTF_REVERSAL_BOT")

# ======================================================
# TIME HELPERS
# ======================================================

CT = ZoneInfo("America/Chicago")

def ct_time_str() -> str:
    return datetime.now(timezone.utc).astimezone(CT).strftime("%H:%M CT")

def utc_ts() -> int:
    return int(time.time())

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", 10000))

# ONLY THESE TWO TELEGRAM GROUPS
CHAT_IDS = [
    "-1003463990210",
    "-1003749616502",
]

EXCHANGE_NAME = os.getenv("EXCHANGE_NAME", "okx").strip().lower()
SYMBOL = os.getenv("SYMBOL", "BNB/USDT:USDT").strip()

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 10))

TRADE_MODE = os.getenv("TRADE_MODE", "both").strip().lower()
if TRADE_MODE not in ("long_only", "short_only", "both"):
    TRADE_MODE = "both"

TF_CTX = "1h"
TF_CONFIRM = "15m"
TF_EXEC = "5m"

OHLCV_LIMIT_1H = int(os.getenv("OHLCV_LIMIT_1H", 220))
OHLCV_LIMIT_15M = int(os.getenv("OHLCV_LIMIT_15M", 220))
OHLCV_LIMIT_5M = int(os.getenv("OHLCV_LIMIT_5M", 260))

OHLCV_1H_TTL_SEC = int(os.getenv("OHLCV_1H_TTL_SEC", 120))
OHLCV_15M_TTL_SEC = int(os.getenv("OHLCV_15M_TTL_SEC", 40))
OHLCV_5M_TTL_SEC = int(os.getenv("OHLCV_5M_TTL_SEC", 20))

# Indicator settings
RSI_LEN = int(os.getenv("RSI_LEN", 14))
EMA_FAST = int(os.getenv("EMA_FAST", 20))
EMA_SLOW = int(os.getenv("EMA_SLOW", 50))
ATR_LEN = int(os.getenv("ATR_LEN", 14))
VOL_MA_LEN = int(os.getenv("VOL_MA_LEN", 20))

# Pivot / swing settings
PIVOT_LEFT = int(os.getenv("PIVOT_LEFT", 2))
PIVOT_RIGHT = int(os.getenv("PIVOT_RIGHT", 2))
SWING_LOOKBACK = int(os.getenv("SWING_LOOKBACK", 100))
MIN_SWING_SEPARATION = int(os.getenv("MIN_SWING_SEPARATION", 5))

# Divergence thresholds
DIV_MIN_PRICE_DELTA_PCT = float(os.getenv("DIV_MIN_PRICE_DELTA_PCT", 0.0015))
DIV_MIN_RSI_DELTA = float(os.getenv("DIV_MIN_RSI_DELTA", 2.0))

# 5m execution filters
VOLUME_MULT = float(os.getenv("VOLUME_MULT", 1.05))
BOS_ATR_BUFFER_MULT = float(os.getenv("BOS_ATR_BUFFER_MULT", 0.05))
BULLISH_CLOSE_POS_MIN = float(os.getenv("BULLISH_CLOSE_POS_MIN", 0.65))
BEARISH_CLOSE_POS_MAX = float(os.getenv("BEARISH_CLOSE_POS_MAX", 0.35))
REQUIRE_5M_EMA_ALIGNMENT = os.getenv("REQUIRE_5M_EMA_ALIGNMENT", "0") == "1"

# Risk
STOP_METHOD = os.getenv("STOP_METHOD", "ATR").strip().upper()
if STOP_METHOD not in ("ATR", "STRUCT"):
    STOP_METHOD = "ATR"

ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", 1.2))
WICK_STOP_BUFFER_PCT = float(os.getenv("WICK_STOP_BUFFER_PCT", 0.0005))
MIN_RR = float(os.getenv("MIN_RR", 1.5))
MIN_RISK_PCT = float(os.getenv("MIN_RISK_PCT", 0.001))

# Trade / cooldown
ONE_POSITION_AT_A_TIME = os.getenv("ONE_POSITION_AT_A_TIME", "1") == "1"
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", 1800))
WINDOW = int(os.getenv("WINDOW", 1200))

# Display
RISK_PCT_TEXT = os.getenv("RISK_PCT_TEXT", "1%")
LEVERAGE_TEXT = os.getenv("LEVERAGE_TEXT", "3x isolated")
POSITION_SIZE_TEXT = os.getenv("POSITION_SIZE_TEXT", "Risk only 1% of account equity")

# ======================================================
# STATE
# ======================================================

recent_signals: Dict[str, float] = {}
recent_position_close_ts: Optional[float] = None

open_trades: Dict[str, Dict[str, Any]] = {}
open_trades_lock = threading.Lock()

closed_trades: List[Dict[str, Any]] = []
stats_lock = threading.Lock()

last_processed_exec_candle_ts: Dict[str, int] = {}

# ======================================================
# HELPERS
# ======================================================

def norm_symbol(symbol: str) -> str:
    return symbol.split(":")[0].replace("/", "").strip()

def make_trade_id(ex_name: str, symbol: str, direction: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%m%d-%H%M")
    base = norm_symbol(symbol)
    side = "L" if direction == "LONG" else "S"
    ex_tag = ex_name.upper()
    return f"{base}-{side}-{ex_tag}-{ts}"

def fmt_price(px: float) -> str:
    if px >= 1000:
        return f"{px:,.2f}"
    if px >= 1:
        return f"{px:.4f}"
    if px >= 0.01:
        return f"{px:.6f}"
    return f"{px:.8f}"

def has_open_position() -> bool:
    with open_trades_lock:
        return len(open_trades) > 0

def cooldown_ok() -> bool:
    global recent_position_close_ts
    if recent_position_close_ts is None:
        return True
    return (time.time() - recent_position_close_ts) >= COOLDOWN_SEC

def allow_signal(key: str) -> bool:
    now = time.time()
    last = recent_signals.get(key)
    if last is None or (now - last) > WINDOW:
        recent_signals[key] = now
        return True
    return False

# ======================================================
# TELEGRAM
# ======================================================

TELEGRAM_API = "https://api.telegram.org"

def send_telegram(text: str):
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN missing")
        log.error("BOT_TOKEN missing")
        return

    if not CHAT_IDS:
        print("ERROR: No chat IDs configured")
        log.warning("No chat IDs configured")
        return

    max_len = 3800
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]

    for cid in CHAT_IDS:
        for ch in chunks:
            try:
                url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
                r = requests.post(url, json={"chat_id": cid, "text": ch}, timeout=10)
                print("SEND TO:", cid, "STATUS:", r.status_code, "BODY:", r.text[:300])
                if r.status_code >= 400:
                    log.error(f"Telegram HTTP {r.status_code}: {r.text[:500]}")
            except Exception as e:
                print("TELEGRAM EXCEPTION:", cid, str(e))
                log.error(f"Telegram error for {cid}: {e}")

def send_startup():
    msg = (
        "🤖 MTF REVERSAL BOT STARTED\n\n"
        "1H → reversal context\n"
        "15m → structure confirmation\n"
        "5m → execution trigger\n\n"
        f"Symbol: {SYMBOL}\n"
        f"Exchange: {EXCHANGE_NAME}\n"
        f"Trade mode: {TRADE_MODE}\n"
        f"Min RR: {MIN_RR:.2f}\n"
        f"Cooldown: {COOLDOWN_SEC // 60} min\n\n"
        f"🕐 Started: {ct_time_str()}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

def send_trade_update(trade: Dict[str, Any], lines: List[str], title: str = "Update"):
    msg = (
        f"🔔 {title}: {trade['symbol']} {trade['direction']}\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        + "\n".join(lines)
        + f"\n\n🕐 {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        + "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

# ======================================================
# CACHE
# ======================================================

class TTLCache:
    def __init__(self):
        self._store: Dict[Any, Tuple[Any, float]] = {}

    def get(self, key):
        v = self._store.get(key)
        if not v:
            return None
        value, exp = v
        if time.time() > exp:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key, value, ttl_sec: int):
        self._store[key] = (value, time.time() + ttl_sec)

ohlcv_cache = TTLCache()

# ======================================================
# INDICATORS
# ======================================================

def calc_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()

    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()
    df["rsi"] = calc_rsi(df["close"], RSI_LEN)
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["vol_ma"] = df["volume"].rolling(VOL_MA_LEN).mean()
    return df

def candle_close_position(row: pd.Series) -> float:
    rng = float(row["high"] - row["low"])
    if rng <= 0:
        return 0.5
    return float((row["close"] - row["low"]) / rng)

# ======================================================
# SWING / PIVOT HELPERS
# ======================================================

def pivot_highs(df: pd.DataFrame, left: int, right: int) -> List[int]:
    out = []
    for i in range(left, len(df) - right):
        h = float(df["high"].iloc[i])
        prev_ok = all(h > float(df["high"].iloc[i - j]) for j in range(1, left + 1))
        next_ok = all(h >= float(df["high"].iloc[i + j]) for j in range(1, right + 1))
        if prev_ok and next_ok:
            out.append(i)
    return out

def pivot_lows(df: pd.DataFrame, left: int, right: int) -> List[int]:
    out = []
    for i in range(left, len(df) - right):
        lo = float(df["low"].iloc[i])
        prev_ok = all(lo < float(df["low"].iloc[i - j]) for j in range(1, left + 1))
        next_ok = all(lo <= float(df["low"].iloc[i + j]) for j in range(1, right + 1))
        if prev_ok and next_ok:
            out.append(i)
    return out

def get_recent_structure_points(df: pd.DataFrame) -> Dict[str, Any]:
    work = df.tail(SWING_LOOKBACK).reset_index(drop=True)
    highs = pivot_highs(work, PIVOT_LEFT, PIVOT_RIGHT)
    lows = pivot_lows(work, PIVOT_LEFT, PIVOT_RIGHT)

    return {
        "last_high": float(work["high"].iloc[highs[-1]]) if len(highs) >= 1 else None,
        "prev_high": float(work["high"].iloc[highs[-2]]) if len(highs) >= 2 else None,
        "last_low": float(work["low"].iloc[lows[-1]]) if len(lows) >= 1 else None,
        "prev_low": float(work["low"].iloc[lows[-2]]) if len(lows) >= 2 else None,
        "last_high_idx": int(highs[-1]) if len(highs) >= 1 else None,
        "prev_high_idx": int(highs[-2]) if len(highs) >= 2 else None,
        "last_low_idx": int(lows[-1]) if len(lows) >= 1 else None,
        "prev_low_idx": int(lows[-2]) if len(lows) >= 2 else None,
    }

# ======================================================
# DIVERGENCE
# ======================================================

def detect_bullish_divergence(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    work = df.tail(SWING_LOOKBACK).reset_index(drop=True)
    lows = pivot_lows(work, PIVOT_LEFT, PIVOT_RIGHT)
    if len(lows) < 2:
        return None

    i1, i2 = lows[-2], lows[-1]
    if (i2 - i1) < MIN_SWING_SEPARATION:
        return None

    p1 = float(work["low"].iloc[i1])
    p2 = float(work["low"].iloc[i2])
    r1 = float(work["rsi"].iloc[i1])
    r2 = float(work["rsi"].iloc[i2])

    if p1 <= 0:
        return None

    price_delta_pct = (p1 - p2) / p1
    rsi_delta = r2 - r1

    if price_delta_pct < DIV_MIN_PRICE_DELTA_PCT:
        return None
    if rsi_delta < DIV_MIN_RSI_DELTA:
        return None

    return {
        "type": "bullish",
        "swing1_idx": int(i1),
        "swing2_idx": int(i2),
        "price1": float(p1),
        "price2": float(p2),
        "rsi1": float(r1),
        "rsi2": float(r2),
    }

def detect_bearish_divergence(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    work = df.tail(SWING_LOOKBACK).reset_index(drop=True)
    highs = pivot_highs(work, PIVOT_LEFT, PIVOT_RIGHT)
    if len(highs) < 2:
        return None

    i1, i2 = highs[-2], highs[-1]
    if (i2 - i1) < MIN_SWING_SEPARATION:
        return None

    p1 = float(work["high"].iloc[i1])
    p2 = float(work["high"].iloc[i2])
    r1 = float(work["rsi"].iloc[i1])
    r2 = float(work["rsi"].iloc[i2])

    if p1 <= 0:
        return None

    price_delta_pct = (p2 - p1) / p1
    rsi_delta = r1 - r2

    if price_delta_pct < DIV_MIN_PRICE_DELTA_PCT:
        return None
    if rsi_delta < DIV_MIN_RSI_DELTA:
        return None

    return {
        "type": "bearish",
        "swing1_idx": int(i1),
        "swing2_idx": int(i2),
        "price1": float(p1),
        "price2": float(p2),
        "rsi1": float(r1),
        "rsi2": float(r2),
    }

# ======================================================
# 1H CONTEXT
# ======================================================

def has_confirmed_higher_low(df: pd.DataFrame) -> bool:
    pts = get_recent_structure_points(df)
    if pts["prev_low"] is None or pts["last_low"] is None:
        return False
    return float(pts["last_low"]) > float(pts["prev_low"])

def has_confirmed_lower_high(df: pd.DataFrame) -> bool:
    pts = get_recent_structure_points(df)
    if pts["prev_high"] is None or pts["last_high"] is None:
        return False
    return float(pts["last_high"]) < float(pts["prev_high"])

def get_1h_context(df_1h: pd.DataFrame) -> str:
    bull_div = detect_bullish_divergence(df_1h)
    bear_div = detect_bearish_divergence(df_1h)
    bull_hl = has_confirmed_higher_low(df_1h)
    bear_lh = has_confirmed_lower_high(df_1h)

    bullish = bool(bull_div) or bull_hl
    bearish = bool(bear_div) or bear_lh

    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return "neutral"

# ======================================================
# 15m STRUCTURE CONFIRMATION
# ======================================================

def get_15m_structure(df_15m: pd.DataFrame) -> str:
    pts = get_recent_structure_points(df_15m)

    if None in (pts["prev_high"], pts["last_high"], pts["prev_low"], pts["last_low"]):
        return "neutral"

    bullish = (
        float(pts["last_high"]) > float(pts["prev_high"]) and
        float(pts["last_low"]) > float(pts["prev_low"])
    )
    bearish = (
        float(pts["last_high"]) < float(pts["prev_high"]) and
        float(pts["last_low"]) < float(pts["prev_low"])
    )

    if bullish and not bearish:
        return "bullish"
    if bearish and not bullish:
        return "bearish"
    return "neutral"

# ======================================================
# 5m EXECUTION
# ======================================================

def volume_ok(df_5m: pd.DataFrame) -> bool:
    last = df_5m.iloc[-1]
    if pd.isna(last["vol_ma"]) or float(last["vol_ma"]) <= 0:
        return False
    return float(last["volume"]) >= float(last["vol_ma"]) * VOLUME_MULT

def bullish_candle_ok(df_5m: pd.DataFrame) -> bool:
    last = df_5m.iloc[-1]
    if float(last["close"]) <= float(last["open"]):
        return False
    return candle_close_position(last) >= BULLISH_CLOSE_POS_MIN

def bearish_candle_ok(df_5m: pd.DataFrame) -> bool:
    last = df_5m.iloc[-1]
    if float(last["close"]) >= float(last["open"]):
        return False
    return candle_close_position(last) <= BEARISH_CLOSE_POS_MAX

def ema_alignment_long_ok(df_5m: pd.DataFrame) -> bool:
    if not REQUIRE_5M_EMA_ALIGNMENT:
        return True
    last = df_5m.iloc[-1]
    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]):
        return False
    return float(last["ema_fast"]) > float(last["ema_slow"])

def ema_alignment_short_ok(df_5m: pd.DataFrame) -> bool:
    if not REQUIRE_5M_EMA_ALIGNMENT:
        return True
    last = df_5m.iloc[-1]
    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]):
        return False
    return float(last["ema_fast"]) < float(last["ema_slow"])

def latest_exec_pivot_high(df_5m: pd.DataFrame) -> Optional[float]:
    work = df_5m.tail(SWING_LOOKBACK).reset_index(drop=True)
    highs = pivot_highs(work, PIVOT_LEFT, PIVOT_RIGHT)
    if not highs:
        return None
    return float(work["high"].iloc[highs[-1]])

def latest_exec_pivot_low(df_5m: pd.DataFrame) -> Optional[float]:
    work = df_5m.tail(SWING_LOOKBACK).reset_index(drop=True)
    lows = pivot_lows(work, PIVOT_LEFT, PIVOT_RIGHT)
    if not lows:
        return None
    return float(work["low"].iloc[lows[-1]])

def previous_15m_resistance(df_15m: pd.DataFrame) -> Optional[float]:
    pts = get_recent_structure_points(df_15m)
    if pts["last_high"] is not None:
        return float(pts["last_high"])
    if pts["prev_high"] is not None:
        return float(pts["prev_high"])
    return None

def previous_15m_support(df_15m: pd.DataFrame) -> Optional[float]:
    pts = get_recent_structure_points(df_15m)
    if pts["last_low"] is not None:
        return float(pts["last_low"])
    if pts["prev_low"] is not None:
        return float(pts["prev_low"])
    return None

def get_5m_entry_signal(df_5m: pd.DataFrame, df_15m: pd.DataFrame, direction: str) -> Optional[Dict[str, Any]]:
    last = df_5m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None

    entry = float(last["close"])

    if direction == "LONG":
        div = detect_bullish_divergence(df_5m)
        bos_level = latest_exec_pivot_high(df_5m)
        tp_level = previous_15m_resistance(df_15m)

        if not div or bos_level is None or tp_level is None:
            return None
        if entry <= bos_level + (BOS_ATR_BUFFER_MULT * atr):
            return None
        if not bullish_candle_ok(df_5m):
            return None
        if not volume_ok(df_5m):
            return None
        if not ema_alignment_long_ok(df_5m):
            return None

        return {
            "direction": "LONG",
            "entry": entry,
            "bos_level": float(bos_level),
            "structure_stop_ref": float(div["price2"]),
            "tp_level": float(tp_level),
            "reason": "5m bullish divergence + BOS + bullish candle + volume",
        }

    if direction == "SHORT":
        div = detect_bearish_divergence(df_5m)
        bos_level = latest_exec_pivot_low(df_5m)
        tp_level = previous_15m_support(df_15m)

        if not div or bos_level is None or tp_level is None:
            return None
        if entry >= bos_level - (BOS_ATR_BUFFER_MULT * atr):
            return None
        if not bearish_candle_ok(df_5m):
            return None
        if not volume_ok(df_5m):
            return None
        if not ema_alignment_short_ok(df_5m):
            return None

        return {
            "direction": "SHORT",
            "entry": entry,
            "bos_level": float(bos_level),
            "structure_stop_ref": float(div["price2"]),
            "tp_level": float(tp_level),
            "reason": "5m bearish divergence + BOS + bearish candle + volume",
        }

    return None

# ======================================================
# TRADE BUILDING
# ======================================================

def calc_quality_score(df_5m: pd.DataFrame, df_15m: pd.DataFrame, direction: str) -> float:
    last = df_5m.iloc[-1]
    score = 0.0

    if not pd.isna(last["vol_ma"]) and float(last["vol_ma"]) > 0:
        vr = float(last["volume"]) / float(last["vol_ma"])
        score += 3.0 if vr >= 1.5 else 2.0 if vr >= 1.2 else 1.0 if vr >= 1.0 else 0.5
    else:
        score += 0.5

    atr_pct = (float(last["atr"]) / float(last["close"]) * 100.0) if float(last["close"]) > 0 else 0.0
    score += 2.5 if atr_pct >= 0.8 else 2.0 if atr_pct >= 0.5 else 1.0

    struct = get_15m_structure(df_15m)
    if direction == "LONG" and struct == "bullish":
        score += 2.5
    elif direction == "SHORT" and struct == "bearish":
        score += 2.5
    else:
        score += 0.5

    score += 2.0
    return round(min(score, 10.0), 2)

def risk_label(score: float) -> Tuple[str, str]:
    if score >= 8.0:
        return ("LOW", "A+")
    if score >= 6.5:
        return ("LOW–MED", "A")
    if score >= 5.0:
        return ("MED", "B")
    return ("HIGH", "C")

def build_trade(ex_name: str, symbol: str, signal: Dict[str, Any], df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    last = df_5m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None

    direction = signal["direction"]
    entry = float(signal["entry"])
    tp = float(signal["tp_level"])

    if direction == "LONG":
        struct_stop = float(signal["structure_stop_ref"]) * (1.0 - WICK_STOP_BUFFER_PCT)
        atr_stop = entry - (ATR_STOP_MULT * atr)
        stop = min(struct_stop, atr_stop) if STOP_METHOD == "ATR" else struct_stop

        if stop <= 0 or stop >= entry:
            return None

        risk_dist = entry - stop
        reward_dist = tp - entry

    else:
        struct_stop = float(signal["structure_stop_ref"]) * (1.0 + WICK_STOP_BUFFER_PCT)
        atr_stop = entry + (ATR_STOP_MULT * atr)
        stop = max(struct_stop, atr_stop) if STOP_METHOD == "ATR" else struct_stop

        if stop <= entry:
            return None

        risk_dist = stop - entry
        reward_dist = entry - tp

    if risk_dist <= 0 or reward_dist <= 0:
        return None

    risk_pct = risk_dist / entry
    rr = reward_dist / risk_dist

    if risk_pct < MIN_RISK_PCT:
        return None
    if rr < MIN_RR:
        return None

    q_score = calc_quality_score(df_5m, df_15m, direction)
    risk_text, grade = risk_label(q_score)
    now = utc_ts()

    return {
        "trade_id": make_trade_id(ex_name, symbol, direction),
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": direction,
        "entry": float(entry),
        "entry_range_low": float(entry * 0.999),
        "entry_range_high": float(entry * 1.001),
        "stop": float(stop),
        "tp1": float(tp),
        "tp2": float(tp),
        "tp3": None,
        "tp1_hit": False,
        "tp1_partial_taken": False,
        "tp2_rr": float(rr),
        "rr": float(rr),
        "quality_score": float(q_score),
        "risk_text": risk_text,
        "risk_grade": grade,
        "status": "ACTIVE",
        "start_ts": now,
        "created_ts": now,
        "risk_pct": RISK_PCT_TEXT,
        "leverage": LEVERAGE_TEXT,
        "position_size_text": POSITION_SIZE_TEXT,
        "thesis": signal["reason"],
        "updates": [],
        "setup_type": "MTF_REVERSAL",
        "analytics": {
            "tp1_hit": False,
            "tp2_hit": False,
            "max_favorable": 0.0,
            "max_adverse": 0.0,
            "time_to_tp1_sec": None,
            "time_to_tp2_sec": None,
        }
    }

# ======================================================
# EXCHANGE
# ======================================================

def get_ex(name: str):
    try:
        if name == "okx":
            return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        if name == "kucoin_futures":
            return ccxt.kucoinfutures({"enableRateLimit": True})
        if name == "binance":
            return ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        return None
    except Exception as e:
        log.error(f"Exchange load error ({name}): {e}")
        return None

EX = None
MARKETS_READY = False

def get_ex_cached():
    global EX
    if EX is not None:
        return EX
    EX = get_ex(EXCHANGE_NAME)
    return EX

def ensure_markets_loaded(ex) -> bool:
    global MARKETS_READY
    if MARKETS_READY:
        return True
    try:
        ex.load_markets()
        MARKETS_READY = True
        return True
    except Exception as e:
        log.error(f"load_markets failed: {e}")
        return False

# ======================================================
# DATA FETCH
# ======================================================

def get_df_cached(ex_name: str, ex, symbol: str, tf: str, limit: int, ttl_sec: int) -> Optional[pd.DataFrame]:
    key = (ex_name, symbol, tf, limit)
    hit = ohlcv_cache.get(key)
    if hit is not None:
        return hit
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        df = add_indicators(df)
        ohlcv_cache.set(key, df, ttl_sec)
        return df
    except Exception as e:
        log.error(f"Fetch error {symbol} {tf}: {e}")
        return None

# ======================================================
# TRADE LIFECYCLE
# ======================================================

def add_trade_event(trade: Dict[str, Any], event_type: str, message: str):
    trade.setdefault("updates", [])
    trade["updates"].append({
        "ts": utc_ts(),
        "type": event_type,
        "message": message,
    })

def send_signal(trade: Dict[str, Any]):
    emoji = "📈" if trade["direction"] == "LONG" else "📉"
    entry_low = min(float(trade["entry_range_low"]), float(trade["entry_range_high"]))
    entry_high = max(float(trade["entry_range_low"]), float(trade["entry_range_high"]))

    msg = (
        f"{emoji} Trade Triggered: {trade['symbol']} {trade['direction']}\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"Entry: {fmt_price(entry_low)}–{fmt_price(entry_high)}\n"
        f"Stop Loss: {fmt_price(float(trade['stop']))}\n"
        f"Take Profit: {fmt_price(float(trade['tp1']))}\n\n"
        f"RR: {trade['rr']:.2f}\n"
        f"Risk: {trade['risk_pct']}\n"
        f"Leverage: {trade['leverage']}\n"
        f"Position size: {trade['position_size_text']}\n\n"
        f"Thesis: {trade['thesis']}\n\n"
        f"Quality: {trade['quality_score']:.1f}/10 | Risk: {trade['risk_text']} ({trade['risk_grade']})\n"
        f"🕐 {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)
    add_trade_event(trade, "TRADE_TRIGGERED", "Trade triggered and plan published.")

    with open_trades_lock:
        open_trades[trade["trade_id"]] = trade

def record_closed(trade: Dict[str, Any], outcome: str, exit_price: float):
    global recent_position_close_ts
    recent_position_close_ts = time.time()

    with stats_lock:
        closed_trades.append({
            "trade_id": trade.get("trade_id"),
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "entry": float(trade["entry"]),
            "stop": float(trade["stop"]),
            "tp": float(trade["tp1"]),
            "exit_price": float(exit_price),
            "rr": float(trade.get("rr", 0.0)),
            "quality_score": trade.get("quality_score"),
            "outcome": outcome,
            "created_ts": trade.get("created_ts"),
            "closed_ts": utc_ts(),
        })

# ======================================================
# TRACKER LOOP
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
                    trade = open_trades.get(k)
                if not trade:
                    continue

                ex = get_ex_cached()
                if not ex:
                    continue

                ticker = ex.fetch_ticker(trade["symbol"])
                px = float(ticker.get("last") or ticker.get("close") or 0.0)
                if px <= 0:
                    continue

                entry = float(trade["entry"])
                stop = float(trade["stop"])
                tp = float(trade["tp1"])
                direction = trade["direction"]

                if direction == "LONG":
                    favorable = (px - entry) / entry
                    adverse = (entry - px) / entry
                else:
                    favorable = (entry - px) / entry
                    adverse = (px - entry) / entry

                with open_trades_lock:
                    if k in open_trades:
                        open_trades[k]["analytics"]["max_favorable"] = max(
                            float(open_trades[k]["analytics"].get("max_favorable", 0.0)), favorable
                        )
                        open_trades[k]["analytics"]["max_adverse"] = max(
                            float(open_trades[k]["analytics"].get("max_adverse", 0.0)), adverse
                        )

                if direction == "LONG" and px <= stop:
                    add_trade_event(trade, "FULLY_CLOSED", "Trade stopped.")
                    send_trade_update(trade, ["Stop loss reached.", "Trade closed."], title="Trade Closed")
                    record_closed(trade, "LOSS", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "SHORT" and px >= stop:
                    add_trade_event(trade, "FULLY_CLOSED", "Trade stopped.")
                    send_trade_update(trade, ["Stop loss reached.", "Trade closed."], title="Trade Closed")
                    record_closed(trade, "LOSS", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "LONG" and px >= tp:
                    add_trade_event(trade, "FULLY_CLOSED", "Take profit reached.")
                    send_trade_update(trade, ["Take profit reached.", "Trade closed."], title="Trade Closed")
                    record_closed(trade, "WIN", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "SHORT" and px <= tp:
                    add_trade_event(trade, "FULLY_CLOSED", "Take profit reached.")
                    send_trade_update(trade, ["Take profit reached.", "Trade closed."], title="Trade Closed")
                    record_closed(trade, "WIN", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# CLOSED-CANDLE GUARD
# ======================================================

def is_new_exec_candle(df_5m: pd.DataFrame, key: str) -> bool:
    if df_5m.empty:
        return False

    ts = int(df_5m["ts"].iloc[-1])
    last_seen = last_processed_exec_candle_ts.get(key)

    if last_seen is not None and ts <= last_seen:
        return False

    last_processed_exec_candle_ts[key] = ts
    return True

# ======================================================
# SCANNER LOOP
# ======================================================

def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        try:
            ex = get_ex_cached()
            if not ex:
                time.sleep(SCAN_INTERVAL)
                continue

            if not ensure_markets_loaded(ex):
                time.sleep(SCAN_INTERVAL)
                continue

            if ONE_POSITION_AT_A_TIME and has_open_position():
                time.sleep(SCAN_INTERVAL)
                continue

            if not cooldown_ok():
                time.sleep(SCAN_INTERVAL)
                continue

            df_1h = get_df_cached(EXCHANGE_NAME, ex, SYMBOL, TF_CTX, OHLCV_LIMIT_1H, OHLCV_1H_TTL_SEC)
            df_15m = get_df_cached(EXCHANGE_NAME, ex, SYMBOL, TF_CONFIRM, OHLCV_LIMIT_15M, OHLCV_15M_TTL_SEC)
            df_5m = get_df_cached(EXCHANGE_NAME, ex, SYMBOL, TF_EXEC, OHLCV_LIMIT_5M, OHLCV_5M_TTL_SEC)

            if df_1h is None or df_15m is None or df_5m is None:
                time.sleep(SCAN_INTERVAL)
                continue

            if len(df_1h) < 120 or len(df_15m) < 120 or len(df_5m) < 150:
                time.sleep(SCAN_INTERVAL)
                continue

            if not is_new_exec_candle(df_5m, f"{EXCHANGE_NAME}|{SYMBOL}"):
                time.sleep(SCAN_INTERVAL)
                continue

            ctx_1h = get_1h_context(df_1h)
            struct_15m = get_15m_structure(df_15m)

            log.info(f"Context -> 1H: {ctx_1h} | 15m: {struct_15m}")

            if TRADE_MODE in ("both", "long_only"):
                if ctx_1h == "bullish" and struct_15m == "bullish":
                    signal = get_5m_entry_signal(df_5m, df_15m, "LONG")
                    if signal and allow_signal(f"{SYMBOL}_LONG"):
                        trade = build_trade(EXCHANGE_NAME, SYMBOL, signal, df_5m, df_15m)
                        if trade:
                            send_signal(trade)
                            time.sleep(SCAN_INTERVAL)
                            continue

            if TRADE_MODE in ("both", "short_only"):
                if ctx_1h == "bearish" and struct_15m == "bearish":
                    signal = get_5m_entry_signal(df_5m, df_15m, "SHORT")
                    if signal and allow_signal(f"{SYMBOL}_SHORT"):
                        trade = build_trade(EXCHANGE_NAME, SYMBOL, signal, df_5m, df_15m)
                        if trade:
                            send_signal(trade)
                            time.sleep(SCAN_INTERVAL)
                            continue

        except Exception as e:
            log.error(f"Scanner error: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "MTF reversal bot running"

# ======================================================
# MAIN
# ======================================================

if __name__ == "__main__":
    print("BOT_TOKEN_SET =", bool(BOT_TOKEN))
    print("CHAT_IDS =", CHAT_IDS)

    send_telegram("Startup test from bot")

    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
