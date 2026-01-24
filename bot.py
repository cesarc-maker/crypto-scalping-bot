# ======================================================
# CRT 15-MINUTE STRATEGY BOT — OPTION B (BALANCED) + LONG/SHORT
# OKX + KUCOIN FUTURES • HIGH WIN RATE CONFIGURATION
#
# TARGET: 3-4 signals/day | 68-72% win rate (LONG side baseline)
#
# LOCKED SETTINGS (per our adjustments):
# - TRADE_MODE: BOTH (LONG + SHORT)
# - Entry Model: 2A (zone/pump/fib = qualification only)
#   -> BREAKOUT CONFIRM -> RETEST -> CONFIRM CANDLE -> ENTER
# - Breakout confirmation: 2 candle CLOSES beyond pump level
# - Pump range: 5.0%–6.5% (LONG pump / SHORT dump)
# - Reaction requirement: 1.5R
# - Volume filter: >= 85% of average
# - Base consolidation: <= 45% avg body%
# - Take Profits: TP1 1R (25%) | TP2 Dynamic 2–4R (75%)
# - Stop: below/above tap wick with buffer
# - Risk Level: Option A (Quality score shown, mapped to A+/A/B/C)
# - Shorts extra filter: RSI(15m,14) must be >= 40 to allow SHORT entries
# - Cooldowns: 30m + 2h penalty after SL, SEPARATE by direction
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
log = logging.getLogger("CRT_15M_OPTION_B_BOTH")

# ======================================================
# TIME HELPERS
# ======================================================

CT = ZoneInfo("America/Chicago")

def ct_time_str() -> str:
    return datetime.now(timezone.utc).astimezone(CT).strftime("%H:%M CT")

# ======================================================
# CONFIG — OPTION B (BALANCED)
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

# Trade mode
TRADE_MODE = os.getenv("TRADE_MODE", "both").strip().lower()
if TRADE_MODE not in ("long_only", "short_only", "both"):
    TRADE_MODE = "both"

# Universe
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 260))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 35))
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 5_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 25))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Timeframes
TF_EXEC = "15m"
TF_CTX = "1h"

# Demand/Supply Zone detection — OPTION B
BASE_LOOKBACK = int(os.getenv("BASE_LOOKBACK", 5))
BASE_MAX_BODY_PCT = float(os.getenv("BASE_MAX_BODY_PCT", 0.45))
DISP_BODY_PCT_MIN = float(os.getenv("DISP_BODY_PCT_MIN", 0.55))
RANGE_SMA_LEN = int(os.getenv("RANGE_SMA_LEN", 20))
DISP_RANGE_MULT = float(os.getenv("DISP_RANGE_MULT", 1.5))

# Tap + reaction — OPTION B
FIRST_TAP_ONLY = True
REACTION_R_MULT = float(os.getenv("REACTION_R_MULT", 1.5))

# Pump/dump detection — OPTION B
PUMP_MIN_PCT = float(os.getenv("PUMP_MIN_PCT", 5.0))
PUMP_MAX_PCT = float(os.getenv("PUMP_MAX_PCT", 6.5))
PUMP_MAX_CANDLES = int(os.getenv("PUMP_MAX_CANDLES", 4))
BREAK_LOOKBACK = int(os.getenv("BREAK_LOOKBACK", 20))

# Volume filter — OPTION B
ENABLE_LOW_VOL_FILTER = os.getenv("ENABLE_LOW_VOL_FILTER", "1") == "1"
LOW_VOL_MULT = float(os.getenv("LOW_VOL_MULT", 0.85))

# Context EMAs
CTX_EMA_FAST = int(os.getenv("CTX_EMA_FAST", 20))
CTX_EMA_SLOW = int(os.getenv("CTX_EMA_SLOW", 50))

# RSI (SHORTS)
ENABLE_RSI_FILTER_SHORTS = os.getenv("ENABLE_RSI_FILTER_SHORTS", "1") == "1"
RSI_LEN = int(os.getenv("RSI_LEN", 14))
RSI_SHORT_MIN = float(os.getenv("RSI_SHORT_MIN", 40))

# Entry model: Breakout -> Retest -> Confirm -> Enter (Model 2A)
BREAKOUT_CANDLES_REQUIRED = int(os.getenv("BREAKOUT_CANDLES_REQUIRED", 2))
BREAKOUT_CLOSE_REQUIRED = True  # locked: close-based

RETEST_MAX_DIP_PCT = float(os.getenv("RETEST_MAX_DIP_PCT", 0.002))   # 0.2%
RETEST_TIMEOUT_CANDLES = int(os.getenv("RETEST_TIMEOUT_CANDLES", 12))  # 3h

# Stop method
STOP_METHOD = os.getenv("STOP_METHOD", "STRUCT").strip().upper()
if STOP_METHOD not in ("STRUCT", "ATR"):
    STOP_METHOD = "STRUCT"
ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", 1.0))

# Take profits
TP1_RR = float(os.getenv("TP1_RR", 1.0))
TP1_SIZE_PCT = float(os.getenv("TP1_SIZE_PCT", 0.25))
TP2_SIZE_PCT = float(os.getenv("TP2_SIZE_PCT", 0.75))
TP2_DYNAMIC = os.getenv("TP2_DYNAMIC", "1") == "1"
TP2_RR_MIN = float(os.getenv("TP2_RR_MIN", 2.0))
TP2_RR_MAX = float(os.getenv("TP2_RR_MAX", 4.0))

# Wick stop buffer
WICK_STOP_BUFFER_PCT = float(os.getenv("WICK_STOP_BUFFER_PCT", 0.0005))  # 0.05%

# Risk level (Option A)
RISK_A_PLUS_MIN = float(os.getenv("RISK_A_PLUS_MIN", 8.0))
RISK_A_MIN = float(os.getenv("RISK_A_MIN", 6.5))
RISK_B_MIN = float(os.getenv("RISK_B_MIN", 5.0))

# News blackout (UTC)
NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "").strip()

# Cooldowns (same as previous bot)
WINDOW = int(os.getenv("WINDOW", 1800))                 # 30m
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 7200))  # 2h

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

# symbol_state[ex|symbol] -> {"LONG": {...}, "SHORT": {...}}
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
                r = requests.post(url, json={"chat_id": cid, "text": ch}, timeout=10)
                if r.status_code >= 400:
                    log.error(f"Telegram HTTP {r.status_code}: {r.text[:200]}")
            except Exception as e:
                log.error(f"Telegram error for {cid}: {e}")

def send_startup():
    msg = (
        "🤖 CRT 15M BOT STARTED (OPTION B - BALANCED) — LONG+SHORT\n\n"
        "🎯 Target: 3–4 signals/day | 68–72% win rate (baseline)\n\n"
        "🧠 Entry Model: Breakout → Retest → Confirm → Enter (2A)\n"
        f"• Breakout: {BREAKOUT_CANDLES_REQUIRED} closes beyond pump level\n"
        f"• Retest tolerance: {RETEST_MAX_DIP_PCT*100:.2f}% | Timeout: {RETEST_TIMEOUT_CANDLES} candles\n\n"
        "⚙️ Filters:\n"
        f"• Pump/Dump: {PUMP_MIN_PCT:.1f}%–{PUMP_MAX_PCT:.1f}%\n"
        f"• Reaction: {REACTION_R_MULT:.1f}R\n"
        f"• Volume: {int(LOW_VOL_MULT*100)}% of avg\n"
        f"• Base: <= {int(BASE_MAX_BODY_PCT*100)}% avg body\n"
        f"• Shorts RSI veto: RSI(15m,{RSI_LEN}) >= {RSI_SHORT_MIN:.0f}\n\n"
        "💰 Exits:\n"
        f"• TP1: 1R ({int(TP1_SIZE_PCT*100)}%)\n"
        f"• TP2: Dynamic {TP2_RR_MIN:.1f}–{TP2_RR_MAX:.1f}R ({int(TP2_SIZE_PCT*100)}%)\n"
        f"• Stop: tap wick ± buffer ({WICK_STOP_BUFFER_PCT*100:.3f}%)\n\n"
        "🧯 Risk label: Quality score shown (A+/A/B/C)\n"
        f"⏱ Cooldowns: {WINDOW//60}m | SL penalty: {STOP_PENALTY_WINDOW//3600}h (separate by direction)\n\n"
        f"🔄 Exchanges: {', '.join([e.upper() for e in EXCHANGES])}\n"
        f"🕐 Started: {ct_time_str()}\n\n"
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
# COOLDOWNS (SEPARATE BY DIRECTION)
# ======================================================

def _cd_key(ex_name: str, symbol: str, direction: str) -> str:
    return f"{ex_name}_{symbol}_{direction}"

def allow_signal(ex_name: str, symbol: str, direction: str) -> bool:
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

def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()

    rs = avg_gain / (avg_loss.replace(0, pd.NA))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

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
    df["rsi"] = _rsi(df["close"], RSI_LEN)
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
# CORE FILTERS
# ======================================================

def ctx_bullish_1h(df_1h: pd.DataFrame) -> bool:
    last = df_1h.iloc[-1]
    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]):
        return False
    return float(last["ema_fast"]) > float(last["ema_slow"]) and float(last["close"]) > float(last["ema_fast"])

def ctx_bearish_1h(df_1h: pd.DataFrame) -> bool:
    last = df_1h.iloc[-1]
    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]):
        return False
    return float(last["ema_fast"]) < float(last["ema_slow"]) and float(last["close"]) < float(last["ema_fast"])

def low_vol_ok(df_15m: pd.DataFrame) -> bool:
    if not ENABLE_LOW_VOL_FILTER:
        return True
    last = df_15m.iloc[-1]
    if pd.isna(last["vol_sma"]) or float(last["vol_sma"]) <= 0:
        return False
    return float(last["volume"]) >= float(last["vol_sma"]) * LOW_VOL_MULT

def shorts_rsi_ok(df_15m: pd.DataFrame) -> bool:
    if not ENABLE_RSI_FILTER_SHORTS:
        return True
    last = df_15m.iloc[-1]
    r = float(last["rsi"]) if not pd.isna(last["rsi"]) else 50.0
    return r >= RSI_SHORT_MIN

def _candle_body_pct(row) -> float:
    rng = float(row["high"] - row["low"])
    if rng <= 0:
        return 0.0
    body = abs(float(row["close"] - row["open"]))
    return body / rng

# ======================================================
# ZONE DETECTION (DEMAND + SUPPLY)
# ======================================================

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

    if base_df.apply(_candle_body_pct, axis=1).mean() > BASE_MAX_BODY_PCT:
        return None

    zone_top = float(base_df["close"].max())
    zone_bottom = float(base_df["low"].min())
    if zone_top <= zone_bottom:
        return None

    return {
        "type": "DEMAND",
        "created_ts": int(disp["ts"]),
        "top": zone_top,
        "bottom": zone_bottom,
        "tapped": False,
        "tap_ts": None,
        "tap_low": None,
        "tap_high": None,
        "reacted": False,
        "reaction_high": None,
        "reaction_low": None,
        "invalidated": False,
        "traded": False,
    }

def detect_supply_zone(df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df_15m) < (BASE_LOOKBACK + RANGE_SMA_LEN + 10):
        return None

    i = len(df_15m) - 1
    disp = df_15m.iloc[i]

    # bearish displacement candle
    if float(disp["close"]) >= float(disp["open"]):
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

    if base_df.apply(_candle_body_pct, axis=1).mean() > BASE_MAX_BODY_PCT:
        return None

    zone_top = float(base_df["high"].max())
    zone_bottom = float(base_df["close"].min())
    if zone_top <= zone_bottom:
        return None

    return {
        "type": "SUPPLY",
        "created_ts": int(disp["ts"]),
        "top": zone_top,
        "bottom": zone_bottom,
        "tapped": False,
        "tap_ts": None,
        "tap_low": None,
        "tap_high": None,
        "reacted": False,
        "reaction_high": None,
        "reaction_low": None,
        "invalidated": False,
        "traded": False,
    }

def zone_invalidated_long(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    # demand invalidation: close below bottom
    return float(df_15m["close"].iloc[-1]) < float(zone["bottom"])

def zone_invalidated_short(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    # supply invalidation: close above top
    return float(df_15m["close"].iloc[-1]) > float(zone["top"])

def detect_zone_tap_long(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    last = df_15m.iloc[-1]
    low = float(last["low"])
    close = float(last["close"])
    touched = low <= float(zone["top"])
    not_closed_below = close >= float(zone["bottom"])
    return bool(touched and not_closed_below)

def detect_zone_tap_short(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    last = df_15m.iloc[-1]
    high = float(last["high"])
    close = float(last["close"])
    touched = high >= float(zone["bottom"])
    not_closed_above = close <= float(zone["top"])
    return bool(touched and not_closed_above)

def update_reaction_long(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> Dict[str, Any]:
    if not zone.get("tapped") or zone.get("reacted"):
        return zone

    R = max(0.0, float(zone["top"]) - float(zone["bottom"]))
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
    target = float(zone["top"]) + (REACTION_R_MULT * R)
    if mx >= target:
        zone["reacted"] = True
    return zone

def update_reaction_short(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> Dict[str, Any]:
    if not zone.get("tapped") or zone.get("reacted"):
        return zone

    R = max(0.0, float(zone["top"]) - float(zone["bottom"]))
    if R <= 0:
        return zone

    tap_ts = zone.get("tap_ts")
    if not tap_ts:
        return zone

    df_after = df_15m[df_15m["ts"] >= tap_ts]
    if df_after.empty:
        return zone

    mn = float(df_after["low"].min())
    zone["reaction_low"] = mn
    target = float(zone["bottom"]) - (REACTION_R_MULT * R)
    if mn <= target:
        zone["reacted"] = True
    return zone

# ======================================================
# PUMP / DUMP DETECTION
# ======================================================

def detect_pump_long(df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
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
            "swing_low": float(low_before),
            "swing_high": float(high_of_pump),
            "move_pct": float(move_pct),
            "pump_ts": int(df_15m["ts"].iloc[end]),
        }

    return None

def detect_dump_short(df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df_15m) < max(80, BREAK_LOOKBACK + 10):
        return None

    end = len(df_15m) - 1
    prev_minor_low = float(df_15m["low"].iloc[max(0, end - BREAK_LOOKBACK):end].min())

    for n in range(1, PUMP_MAX_CANDLES + 1):
        start = end - (n - 1)
        if start < 3:
            continue

        window = df_15m.iloc[start:end+1]
        high_before = float(df_15m["high"].iloc[start-2:start+1].max())
        low_of_dump = float(window["low"].min())
        if high_before <= 0:
            continue

        move_pct = (high_before - low_of_dump) / high_before * 100.0
        if move_pct < PUMP_MIN_PCT or move_pct > PUMP_MAX_PCT:
            continue

        if float(window["close"].iloc[-1]) >= float(window["open"].iloc[0]):
            continue

        if low_of_dump >= prev_minor_low:
            continue

        return {
            "swing_high": float(high_before),
            "swing_low": float(low_of_dump),
            "move_pct": float(move_pct),
            "pump_ts": int(df_15m["ts"].iloc[end]),
        }

    return None

# ======================================================
# MODEL 2A — BREAKOUT/RETEST/CONFIRM HELPERS
# ======================================================

def _last_n_closes(df: pd.DataFrame, n: int) -> List[float]:
    if len(df) < n:
        return []
    return [float(x) for x in df["close"].tail(n).tolist()]

def breakout_confirmed_long(df_15m: pd.DataFrame, pump_high: float) -> bool:
    closes = _last_n_closes(df_15m, BREAKOUT_CANDLES_REQUIRED)
    if len(closes) < BREAKOUT_CANDLES_REQUIRED:
        return False
    return all(c > pump_high for c in closes)

def breakout_confirmed_short(df_15m: pd.DataFrame, pump_low: float) -> bool:
    closes = _last_n_closes(df_15m, BREAKOUT_CANDLES_REQUIRED)
    if len(closes) < BREAKOUT_CANDLES_REQUIRED:
        return False
    return all(c < pump_low for c in closes)

def retest_seen_long(df_15m: pd.DataFrame, pump_high: float) -> bool:
    last = df_15m.iloc[-1]
    low = float(last["low"])
    close = float(last["close"])
    dip_floor = pump_high * (1.0 - RETEST_MAX_DIP_PCT)
    return (low <= pump_high) and (close >= dip_floor)

def retest_seen_short(df_15m: pd.DataFrame, pump_low: float) -> bool:
    last = df_15m.iloc[-1]
    high = float(last["high"])
    close = float(last["close"])
    pop_ceiling = pump_low * (1.0 + RETEST_MAX_DIP_PCT)
    return (high >= pump_low) and (close <= pop_ceiling)

def confirm_entry_long(df_15m: pd.DataFrame, pump_high: float) -> bool:
    last = df_15m.iloc[-1]
    return float(last["close"]) > pump_high and float(last["close"]) > float(last["open"])

def confirm_entry_short(df_15m: pd.DataFrame, pump_low: float) -> bool:
    last = df_15m.iloc[-1]
    return float(last["close"]) < pump_low and float(last["close"]) < float(last["open"])

# ======================================================
# DYNAMIC TP2 + RISK LABEL (Option A)
# ======================================================

def risk_label(score: float) -> Tuple[str, str]:
    # returns (risk_text, grade)
    if score >= RISK_A_PLUS_MIN:
        return ("LOW", "A+")
    if score >= RISK_A_MIN:
        return ("LOW–MED", "A")
    if score >= RISK_B_MIN:
        return ("MED", "B")
    return ("HIGH", "C")

def calc_quality_score(
    zone: Dict[str, Any],
    pump: Dict[str, Any],
    df_15m: pd.DataFrame,
    df_1h: pd.DataFrame,
    direction: str
) -> float:
    score = 0.0
    max_score = 10.0

    # 1) Pump/Dump strength (0-2)
    pump_pct = float(pump["move_pct"])
    if pump_pct >= 6.0:
        score += 2.0
    elif pump_pct >= 5.5:
        score += 1.5
    elif pump_pct >= 5.0:
        score += 1.0
    else:
        score += 0.5

    # 2) Reaction strength (0-2)
    zone_size = float(zone["top"]) - float(zone["bottom"])
    if zone_size > 0:
        if direction == "LONG":
            rh = zone.get("reaction_high", float(zone["top"]))
            reaction_r = (float(rh) - float(zone["top"])) / zone_size
        else:
            rl = zone.get("reaction_low", float(zone["bottom"]))
            reaction_r = (float(zone["bottom"]) - float(rl)) / zone_size

        if reaction_r >= 2.5:
            score += 2.0
        elif reaction_r >= 2.0:
            score += 1.5
        elif reaction_r >= 1.5:
            score += 1.0
        else:
            score += 0.5

    # 3) Volume strength (0-2)
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

    # 4) 1h trend strength (0-2)
    last_1h = df_1h.iloc[-1]
    if not pd.isna(last_1h["ema_fast"]) and float(last_1h["ema_fast"]) > 0:
        dist = (float(last_1h["close"]) - float(last_1h["ema_fast"])) / float(last_1h["ema_fast"]) * 100.0
        if direction == "SHORT":
            dist = -dist
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

    # 5) Base quality (0-2)
    # tighter base -> better
    if BASE_MAX_BODY_PCT <= 0.35:
        score += 2.0
    elif BASE_MAX_BODY_PCT <= 0.40:
        score += 1.5
    elif BASE_MAX_BODY_PCT <= 0.45:
        score += 1.0
    else:
        score += 0.5

    score = max(0.0, min(max_score, score))
    return round(score, 2)

def dynamic_tp2_rr(score: float) -> float:
    if not TP2_DYNAMIC:
        return float(TP2_RR_MIN)
    score_norm = max(0.0, min(1.0, score / 10.0))
    rr = TP2_RR_MIN + score_norm * (TP2_RR_MAX - TP2_RR_MIN)
    rr = max(TP2_RR_MIN, min(TP2_RR_MAX, rr))
    return round(rr, 2)

# ======================================================
# TRADE BUILDING
# ======================================================

def build_trade(
    ex_name: str,
    symbol: str,
    direction: str,
    entry: float,
    zone: Dict[str, Any],
    df_15m: pd.DataFrame,
    pump: Dict[str, Any],
    df_1h: pd.DataFrame
) -> Optional[Dict[str, Any]]:

    last = df_15m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None

    # Stop
    if STOP_METHOD == "ATR":
        if direction == "LONG":
            stop = entry - ATR_STOP_MULT * atr
        else:
            stop = entry + ATR_STOP_MULT * atr
    else:
        # STRUCT: wick stop based on tap candle wick
        tap_low = zone.get("tap_low")
        tap_high = zone.get("tap_high")
        if direction == "LONG":
            if tap_low is None:
                tap_low = float(last["low"])
            stop = float(tap_low) * (1.0 - WICK_STOP_BUFFER_PCT)
        else:
            if tap_high is None:
                tap_high = float(last["high"])
            stop = float(tap_high) * (1.0 + WICK_STOP_BUFFER_PCT)

    if stop <= 0:
        return None

    if direction == "LONG" and stop >= entry:
        return None
    if direction == "SHORT" and stop <= entry:
        return None

    risk_dist = abs(entry - stop)
    tp1 = entry + TP1_RR * risk_dist if direction == "LONG" else entry - TP1_RR * risk_dist

    # Quality + dynamic TP2
    q_score = calc_quality_score(zone, pump, df_15m, df_1h, direction)
    tp2_rr = dynamic_tp2_rr(q_score)
    tp2 = entry + tp2_rr * risk_dist if direction == "LONG" else entry - tp2_rr * risk_dist

    risk_txt, grade = risk_label(q_score)

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": direction,
        "entry": float(entry),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp1_hit": False,
        "tp1_partial_taken": False,
        "tp2_rr": float(tp2_rr),
        "quality_score": float(q_score),
        "risk_text": risk_txt,
        "risk_grade": grade,
        "status": "ACTIVE",
        "start_ts": now,
        "created_ts": now,
        "zone_created_ts": int(zone["created_ts"]),
    }

# ======================================================
# TELEGRAM SIGNALS
# ======================================================

def send_signal(trade: Dict[str, Any], zone: Dict[str, Any], pump: Dict[str, Any]):
    ts = ct_time_str()

    funny_lines = [
        "😂 Stop loss exists because you are not a prophet.",
        "🧠 Trade the plan, not your emotions.",
        "🚫 No FOMO. No revenge. Just execution.",
        "🥤 Hydrate before you click buttons.",
        "🤖 Be a robot, not a raccoon on energy drinks.",
    ]
    funny = funny_lines[int(time.time()) % len(funny_lines)]

    direction = trade["direction"]
    emoji = "📈" if direction == "LONG" else "📉"

    msg = (
        f"{emoji} {trade['symbol']}\n"
        f"{'LONG' if direction=='LONG' else 'SHORT'} — ✅ ENTRY CONFIRMED\n\n"
        f"📍 ENTRY: {trade['entry']:.6f}\n"
        f"🛑 STOP: {trade['stop']:.6f}\n\n"
        f"🎯 TAKE PROFITS:\n"
        f"TP1 (1R): {trade['tp1']:.6f} — Take {int(TP1_SIZE_PCT*100)}%\n"
        f"TP2 ({trade['tp2_rr']:.2f}R): {trade['tp2']:.6f} — Take {int(TP2_SIZE_PCT*100)}%\n\n"
        f"🧯 RISK: {trade['risk_text']} ({trade['risk_grade']}) | Quality: {trade['quality_score']:.1f}/10\n"
        f"🕐 {ts} | {trade['ex_name'].upper()}\n\n"
        f"{funny}\n\n"
        "⚠️ Not financial advice. Info only."
    )
    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} {direction} TP2={trade['tp2_rr']:.2f}R Score={trade['quality_score']:.2f}")

    trade_key = f"{trade['ex_name']}|{trade['symbol']}|{trade['direction']}|{int(time.time())}"
    with open_trades_lock:
        open_trades[trade_key] = trade

def send_status(ex_name: str, symbol: str, direction: str, text: str):
    msg = f"ℹ️ {symbol} {direction} ({ex_name.upper()}): {text}"
    send_telegram(msg)

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
                tp1 = float(t["tp1"])
                tp2 = float(t["tp2"])
                direction = t["direction"]

                # Stop
                if direction == "LONG" and px <= stop:
                    send_telegram(f"❌ SL HIT — {t['symbol']} (LONG) ({t['ex_name'].upper()})")
                    apply_stop_penalty(t["ex_name"], t["symbol"], "LONG")
                    _record_closed(t, "LOSS", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "SHORT" and px >= stop:
                    send_telegram(f"❌ SL HIT — {t['symbol']} (SHORT) ({t['ex_name'].upper()})")
                    apply_stop_penalty(t["ex_name"], t["symbol"], "SHORT")
                    _record_closed(t, "LOSS", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # TP1 partial
                if not t.get("tp1_hit", False):
                    if direction == "LONG" and px >= tp1:
                        send_telegram(f"✅ TP1 HIT (1R) — {t['symbol']} (LONG) ({t['ex_name'].upper()})")
                        with open_trades_lock:
                            if k in open_trades:
                                open_trades[k]["tp1_hit"] = True
                                open_trades[k]["tp1_partial_taken"] = True
                        continue
                    if direction == "SHORT" and px <= tp1:
                        send_telegram(f"✅ TP1 HIT (1R) — {t['symbol']} (SHORT) ({t['ex_name'].upper()})")
                        with open_trades_lock:
                            if k in open_trades:
                                open_trades[k]["tp1_hit"] = True
                                open_trades[k]["tp1_partial_taken"] = True
                        continue

                # TP2 close
                if direction == "LONG" and px >= tp2:
                    send_telegram(f"🏁 TP2 HIT ({t['tp2_rr']:.2f}R) — {t['symbol']} (LONG) ({t['ex_name'].upper()})")
                    _record_closed(t, "WIN", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "SHORT" and px <= tp2:
                    send_telegram(f"🏁 TP2 HIT ({t['tp2_rr']:.2f}R) — {t['symbol']} (SHORT) ({t['ex_name'].upper()})")
                    _record_closed(t, "WIN", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# MAIN SCANNER LOOP (LONG + SHORT)
# ======================================================

def _get_state_bucket(ex_name: str, symbol: str) -> Dict[str, Any]:
    skey = f"{ex_name}|{symbol}"
    if skey not in symbol_state:
        symbol_state[skey] = {"LONG": {}, "SHORT": {}}
    return symbol_state[skey]

def _reset_side(st_side: Dict[str, Any]):
    st_side.clear()

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

                    st_bucket = _get_state_bucket(ex_name, symbol)

                    # -------------------------
                    # LONG SIDE
                    # -------------------------
                    if TRADE_MODE in ("both", "long_only"):
                        stL = st_bucket["LONG"]
                        zoneL = stL.get("zone")

                        if not ctx_bullish_1h(df_1h):
                            _reset_side(stL)
                        else:
                            if not low_vol_ok(df_15m):
                                pass
                            else:
                                if zoneL and not zoneL.get("invalidated", False):
                                    if zone_invalidated_long(df_15m, zoneL):
                                        zoneL["invalidated"] = True
                                        _reset_side(stL)
                                        zoneL = None

                                if not zoneL:
                                    new_zone = detect_demand_zone(df_15m)
                                    if new_zone:
                                        stL["zone"] = new_zone
                                    else:
                                        pass
                                else:
                                    # Tap
                                    if not zoneL.get("tapped", False):
                                        if detect_zone_tap_long(df_15m, zoneL):
                                            zoneL["tapped"] = True
                                            zoneL["tap_ts"] = int(df_15m["ts"].iloc[-1])
                                            zoneL["tap_low"] = float(df_15m["low"].iloc[-1])
                                            zoneL["tap_high"] = float(df_15m["high"].iloc[-1])
                                            stL["zone"] = zoneL
                                        else:
                                            pass
                                    # Reaction
                                    zoneL = update_reaction_long(df_15m, zoneL)
                                    stL["zone"] = zoneL
                                    if not zoneL.get("reacted", False):
                                        pass
                                    else:
                                        # Pump
                                        pump = stL.get("pump")
                                        if not pump:
                                            pump = detect_pump_long(df_15m)
                                            if pump:
                                                stL["pump"] = pump
                                                stL["pump_high"] = float(pump["swing_high"])
                                                stL["phase"] = "WAIT_BREAKOUT"
                                                stL["phase_started_idx"] = len(df_15m) - 1
                                            else:
                                                pass

                                        if stL.get("pump_high"):
                                            pump_high = float(stL["pump_high"])

                                            # Timeout guard
                                            if stL.get("phase_started_idx") is not None:
                                                elapsed = (len(df_15m) - 1) - int(stL["phase_started_idx"])
                                                if elapsed > RETEST_TIMEOUT_CANDLES:
                                                    _reset_side(stL)

                                            phase = stL.get("phase", "WAIT_BREAKOUT")

                                            if phase == "WAIT_BREAKOUT":
                                                if breakout_confirmed_long(df_15m, pump_high):
                                                    stL["phase"] = "WAIT_RETEST"
                                                    stL["phase_started_idx"] = len(df_15m) - 1
                                                    send_status(ex_name, symbol, "LONG", "✅ Breakout confirmed — waiting for retest.")
                                            elif phase == "WAIT_RETEST":
                                                if retest_seen_long(df_15m, pump_high):
                                                    stL["phase"] = "WAIT_CONFIRM"
                                                    stL["phase_started_idx"] = len(df_15m) - 1
                                                    send_status(ex_name, symbol, "LONG", "📍 Retest seen — waiting for confirmation candle.")
                                            elif phase == "WAIT_CONFIRM":
                                                if confirm_entry_long(df_15m, pump_high):
                                                    if allow_signal(ex_name, symbol, "LONG"):
                                                        entry = float(df_15m["close"].iloc[-1])
                                                        trade = build_trade(ex_name, symbol, "LONG", entry, zoneL, df_15m, stL["pump"], df_1h)
                                                        if trade:
                                                            send_signal(trade, zoneL, stL["pump"])
                                                            _reset_side(stL)

                    # -------------------------
                    # SHORT SIDE
                    # -------------------------
                    if TRADE_MODE in ("both", "short_only"):
                        stS = st_bucket["SHORT"]
                        zoneS = stS.get("zone")

                        if not ctx_bearish_1h(df_1h):
                            _reset_side(stS)
                        else:
                            if not low_vol_ok(df_15m):
                                pass
                            else:
                                # RSI veto for shorts
                                if not shorts_rsi_ok(df_15m):
                                    pass
                                else:
                                    if zoneS and not zoneS.get("invalidated", False):
                                        if zone_invalidated_short(df_15m, zoneS):
                                            zoneS["invalidated"] = True
                                            _reset_side(stS)
                                            zoneS = None

                                    if not zoneS:
                                        new_zone = detect_supply_zone(df_15m)
                                        if new_zone:
                                            stS["zone"] = new_zone
                                        else:
                                            pass
                                    else:
                                        # Tap
                                        if not zoneS.get("tapped", False):
                                            if detect_zone_tap_short(df_15m, zoneS):
                                                zoneS["tapped"] = True
                                                zoneS["tap_ts"] = int(df_15m["ts"].iloc[-1])
                                                zoneS["tap_low"] = float(df_15m["low"].iloc[-1])
                                                zoneS["tap_high"] = float(df_15m["high"].iloc[-1])
                                                stS["zone"] = zoneS
                                            else:
                                                pass

                                        # Reaction
                                        zoneS = update_reaction_short(df_15m, zoneS)
                                        stS["zone"] = zoneS
                                        if not zoneS.get("reacted", False):
                                            pass
                                        else:
                                            # Dump
                                            dump = stS.get("pump")
                                            if not dump:
                                                dump = detect_dump_short(df_15m)
                                                if dump:
                                                    stS["pump"] = dump
                                                    stS["pump_low"] = float(dump["swing_low"])
                                                    stS["phase"] = "WAIT_BREAKOUT"
                                                    stS["phase_started_idx"] = len(df_15m) - 1
                                                else:
                                                    pass

                                            if stS.get("pump_low"):
                                                pump_low = float(stS["pump_low"])

                                                # Timeout guard
                                                if stS.get("phase_started_idx") is not None:
                                                    elapsed = (len(df_15m) - 1) - int(stS["phase_started_idx"])
                                                    if elapsed > RETEST_TIMEOUT_CANDLES:
                                                        _reset_side(stS)

                                                phase = stS.get("phase", "WAIT_BREAKOUT")

                                                if phase == "WAIT_BREAKOUT":
                                                    if breakout_confirmed_short(df_15m, pump_low):
                                                        stS["phase"] = "WAIT_RETEST"
                                                        stS["phase_started_idx"] = len(df_15m) - 1
                                                        send_status(ex_name, symbol, "SHORT", "✅ Breakdown confirmed — waiting for retest.")
                                                elif phase == "WAIT_RETEST":
                                                    if retest_seen_short(df_15m, pump_low):
                                                        stS["phase"] = "WAIT_CONFIRM"
                                                        stS["phase_started_idx"] = len(df_15m) - 1
                                                        send_status(ex_name, symbol, "SHORT", "📍 Retest seen — waiting for confirmation candle.")
                                                elif phase == "WAIT_CONFIRM":
                                                    if confirm_entry_short(df_15m, pump_low):
                                                        if allow_signal(ex_name, symbol, "SHORT"):
                                                            entry = float(df_15m["close"].iloc[-1])
                                                            trade = build_trade(ex_name, symbol, "SHORT", entry, zoneS, df_15m, stS["pump"], df_1h)
                                                            if trade:
                                                                send_signal(trade, zoneS, stS["pump"])
                                                                _reset_side(stS)

                except Exception as e:
                    log.error(f"Scanner error {ex_name} {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "CRT 15m STRATEGY BOT RUNNING (INFO ONLY) — OPTION B BALANCED (LONG+SHORT)"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
