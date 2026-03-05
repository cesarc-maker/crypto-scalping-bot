# ======================================================
# CRT 15-MINUTE STRATEGY BOT — OPTION B (BALANCED) + LONG/SHORT
# OKX + KUCOIN FUTURES • HIGH WIN RATE CONFIGURATION (INFO ONLY)
#
# PRIORITY ORDER APPLIED (WIN-RATE IMPROVEMENTS):
# 1) Metrics fix: track GREEN (TP1 hit then stopped) separately from LOSS; report TP1+ green-rate + TP2-rate
# 2) 15m chop filter: ATR% floor (blocks low-energy noise)
# 3) Long headroom filter: require room to 1H resistance (prevents buying into nearby highs)
# 4) Short support proximity filter: avoid shorts too close to 1H swing lows
# 5) Stricter shorts: MSS impulse requirement + stronger retest rejection structure
#
# NEW ADJUSTMENTS (TP1-FIRST LONGS, KEEP TP RATIO):
# - LOW_VOL_MULT default tightened to 1.05 (requires >= avg volume participation)
# - CHOP_ATR_PCT_MIN default tightened to 0.0027 (0.27% ATR)
# - LONG_HEADROOM_MIN_PCT default tightened to 0.014 (1.4% room to recent 1H highs)
# - LONG_HEADROOM_LOOKBACK_1H default to 72 (3 days of 1H candles)
# - BASE_MAX_BODY_PCT default tightened to 0.40 (cleaner bases)
# - PUMP_MIN_PCT default tightened to 5.3 (better impulse -> better TP1 follow-through)
#
# Keep structure; settings + small logic tweaks only.
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
log = logging.getLogger("CRT_15M_OPTION_B_REWRITE_V5_TP1_FIRST")

# ======================================================
# TIME HELPERS
# ======================================================

CT = ZoneInfo("America/Chicago")

def ct_time_str() -> str:
    return datetime.now(timezone.utc).astimezone(CT).strftime("%H:%M CT")

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

# Demand Zone detection — OPTION B
BASE_LOOKBACK = int(os.getenv("BASE_LOOKBACK", 5))
BASE_MAX_BODY_PCT = float(os.getenv("BASE_MAX_BODY_PCT", 0.40))  # tightened (was 0.45)
DISP_BODY_PCT_MIN = float(os.getenv("DISP_BODY_PCT_MIN", 0.55))
RANGE_SMA_LEN = int(os.getenv("RANGE_SMA_LEN", 20))
DISP_RANGE_MULT = float(os.getenv("DISP_RANGE_MULT", 1.5))

# Tap + reaction
FIRST_TAP_ONLY = True
REACTION_R_MULT = float(os.getenv("REACTION_R_MULT", 1.5))

# Pump (LONGS)
PUMP_MIN_PCT = float(os.getenv("PUMP_MIN_PCT", 5.3))  # tightened (was 5.0)
PUMP_MAX_PCT = float(os.getenv("PUMP_MAX_PCT", 6.5))
PUMP_MAX_CANDLES = int(os.getenv("PUMP_MAX_CANDLES", 4))
BREAK_LOOKBACK = int(os.getenv("BREAK_LOOKBACK", 20))

# Volume filter
ENABLE_LOW_VOL_FILTER = os.getenv("ENABLE_LOW_VOL_FILTER", "1") == "1"
LOW_VOL_MULT = float(os.getenv("LOW_VOL_MULT", 1.05))  # tightened (was 0.85)

# Context EMAs
CTX_EMA_FAST = int(os.getenv("CTX_EMA_FAST", 20))
CTX_EMA_SLOW = int(os.getenv("CTX_EMA_SLOW", 50))

# RSI
RSI_LEN = int(os.getenv("RSI_LEN", 14))

# LONG MODEL 2A
BREAKOUT_CANDLES_REQUIRED = int(os.getenv("BREAKOUT_CANDLES_REQUIRED", 2))
RETEST_MAX_DIP_PCT = float(os.getenv("RETEST_MAX_DIP_PCT", 0.002))
RETEST_TIMEOUT_CANDLES = int(os.getenv("RETEST_TIMEOUT_CANDLES", 12))

# SHORTS — divergence base
ENABLE_SHORT_DIVERGENCE = os.getenv("ENABLE_SHORT_DIVERGENCE", "1") == "1"
DIV_LOOKBACK = int(os.getenv("DIV_LOOKBACK", 30))
DIV_MIN_PRICE_DELTA_PCT = float(os.getenv("DIV_MIN_PRICE_DELTA_PCT", 0.002))
DIV_MIN_RSI_DELTA = float(os.getenv("DIV_MIN_RSI_DELTA", 2.0))
DIV_REQUIRE_RSI_OVERBOUGHT = os.getenv("DIV_REQUIRE_RSI_OVERBOUGHT", "0") == "1"
DIV_RSI_OVERBOUGHT_LEVEL = float(os.getenv("DIV_RSI_OVERBOUGHT_LEVEL", 65))

# ======================================================
# PRIORITY #2 — 15m CHOP FILTER (ATR% FLOOR)
# ======================================================
ENABLE_CHOP_FILTER = os.getenv("ENABLE_CHOP_FILTER", "1") == "1"
CHOP_ATR_PCT_MIN = float(os.getenv("CHOP_ATR_PCT_MIN", 0.0027))  # tightened (was 0.0025)

# ======================================================
# PRIORITY #3 — LONG HEADROOM FILTER (1H RESISTANCE ROOM)
# ======================================================
ENABLE_LONG_HEADROOM_FILTER = os.getenv("ENABLE_LONG_HEADROOM_FILTER", "1") == "1"
LONG_HEADROOM_LOOKBACK_1H = int(os.getenv("LONG_HEADROOM_LOOKBACK_1H", 72))  # tightened (was 48)
LONG_HEADROOM_MIN_PCT = float(os.getenv("LONG_HEADROOM_MIN_PCT", 0.014))     # tightened (was 0.012)

# ======================================================
# PRIORITY #4 — SHORT SUPPORT PROXIMITY FILTER (avoid late shorts)
# ======================================================
ENABLE_SHORT_SUPPORT_FILTER = os.getenv("ENABLE_SHORT_SUPPORT_FILTER", "1") == "1"
SHORT_SUPPORT_LOOKBACK_1H = int(os.getenv("SHORT_SUPPORT_LOOKBACK_1H", 72))
SHORT_SUPPORT_NEAR_PCT = float(os.getenv("SHORT_SUPPORT_NEAR_PCT", 0.01))

# ======================================================
# SHORTS — HIGH WIN RATE SETTINGS (MSS + Retest-only + Bear Regime)
# ======================================================

SHORT_REQUIRE_1H_BEAR = os.getenv("SHORT_REQUIRE_1H_BEAR", "1") == "1"

SHORT_ENTRY_MODE = os.getenv("SHORT_ENTRY_MODE", "retest_only").strip().lower()
if SHORT_ENTRY_MODE not in ("trigger", "retest_only"):
    SHORT_ENTRY_MODE = "retest_only"

DIV_MIN_SWING_SEPARATION = int(os.getenv("DIV_MIN_SWING_SEPARATION", 6))

SHORT_RETEST_MAX_ABOVE_PIVOT_PCT = float(os.getenv("SHORT_RETEST_MAX_ABOVE_PIVOT_PCT", 0.0015))
SHORT_RETEST_TIMEOUT_CANDLES = int(os.getenv("SHORT_RETEST_TIMEOUT_CANDLES", 10))

SHORT_REQUIRE_TRIGGER_VOL = os.getenv("SHORT_REQUIRE_TRIGGER_VOL", "1") == "1"
SHORT_TRIGGER_VOL_MULT = float(os.getenv("SHORT_TRIGGER_VOL_MULT", 1.1))

SHORT_MOVE_SL_TO_BE_AFTER_TP1 = os.getenv("SHORT_MOVE_SL_TO_BE_AFTER_TP1", "1") == "1"
SHORT_BE_BUFFER_PCT = float(os.getenv("SHORT_BE_BUFFER_PCT", 0.0002))

WICK_STOP_BUFFER_PCT = float(os.getenv("WICK_STOP_BUFFER_PCT", 0.0005))

STOP_METHOD = os.getenv("STOP_METHOD", "STRUCT").strip().upper()
if STOP_METHOD not in ("STRUCT", "ATR"):
    STOP_METHOD = "STRUCT"

ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", 1.0))

MIN_RISK_PCT = float(os.getenv("MIN_RISK_PCT", 0.0015))

TP1_RR = float(os.getenv("TP1_RR", 1.0))
TP1_SIZE_PCT = float(os.getenv("TP1_SIZE_PCT", 0.25))
TP2_SIZE_PCT = float(os.getenv("TP2_SIZE_PCT", 0.75))
TP2_DYNAMIC = os.getenv("TP2_DYNAMIC", "1") == "1"
TP2_RR_MIN = float(os.getenv("TP2_RR_MIN", 2.0))
TP2_RR_MAX = float(os.getenv("TP2_RR_MAX", 4.0))

SHORT_STOP_CAP_PCT = float(os.getenv("SHORT_STOP_CAP_PCT", 0.12))
SHORT_TP1_PCT = float(os.getenv("SHORT_TP1_PCT", 0.08))
SHORT_TP2_PCT = float(os.getenv("SHORT_TP2_PCT", 0.25))
SHORT_USE_PCT_TPS = os.getenv("SHORT_USE_PCT_TPS", "1") == "1"
MIN_TP_PRICE = float(os.getenv("MIN_TP_PRICE", 1e-8))

COIN_COOLDOWN_SEC = int(os.getenv("COIN_COOLDOWN_SEC", 3600))

RISK_A_PLUS_MIN = float(os.getenv("RISK_A_PLUS_MIN", 8.0))
RISK_A_MIN = float(os.getenv("RISK_A_MIN", 6.5))
RISK_B_MIN = float(os.getenv("RISK_B_MIN", 5.0))

NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "").strip()

WINDOW = int(os.getenv("WINDOW", 1800))
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 7200))

STATS_BATCH_SIZE = int(os.getenv("STATS_BATCH_SIZE", 10))

UNIVERSE_TTL_SEC = int(os.getenv("UNIVERSE_TTL_SEC", 15 * 60))
MOVERS_TTL_SEC = int(os.getenv("MOVERS_TTL_SEC", 120))
OHLCV_15M_TTL_SEC = int(os.getenv("OHLCV_15M_TTL_SEC", 30))
OHLCV_1H_TTL_SEC = int(os.getenv("OHLCV_1H_TTL_SEC", 120))
OHLCV_LIMIT_15M = int(os.getenv("OHLCV_LIMIT_15M", 160))
OHLCV_LIMIT_1H = int(os.getenv("OHLCV_LIMIT_1H", 120))

STATE_CLEANUP_EVERY_SEC = int(os.getenv("STATE_CLEANUP_EVERY_SEC", 15 * 60))
STATE_STALE_AFTER_SEC = int(os.getenv("STATE_STALE_AFTER_SEC", 6 * 60 * 60))

# ======================================================
# STATE
# ======================================================

recent_signals: Dict[str, float] = {}
penalty_cooldowns: Dict[str, float] = {}

recent_coin_calls: Dict[str, float] = {}

open_trades: Dict[str, Dict[str, Any]] = {}
open_trades_lock = threading.Lock()

closed_trades: List[Dict[str, Any]] = []
stats_lock = threading.Lock()

symbol_state: Dict[str, Dict[str, Any]] = {}

# ======================================================
# HELPERS
# ======================================================

def norm_symbol(symbol: str) -> str:
    return symbol.split(":")[0].strip()

def allow_coin(symbol: str) -> bool:
    now = time.time()
    key = norm_symbol(symbol)
    last = recent_coin_calls.get(key)
    if last is not None and (now - last) < COIN_COOLDOWN_SEC:
        return False
    recent_coin_calls[key] = now
    return True

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
        "✅ LONGS: Model 2A (Breakout → Retest → Confirm → Enter)\n"
        "✅ SHORTS: HI-WIN (1H Bear Regime + Divergence → MSS → Retest Rejection)\n\n"
        f"🧊 Coin cooldown: {COIN_COOLDOWN_SEC//60} minutes (no repeat callouts)\n"
        f"🧹 Chop filter: {'ON' if ENABLE_CHOP_FILTER else 'OFF'} (ATR% ≥ {CHOP_ATR_PCT_MIN*100:.2f}%)\n"
        f"📈 Long headroom: {'ON' if ENABLE_LONG_HEADROOM_FILTER else 'OFF'} (≥ {LONG_HEADROOM_MIN_PCT*100:.2f}% room, {LONG_HEADROOM_LOOKBACK_1H}h)\n"
        f"📈 Long volume participation: {'ON' if ENABLE_LOW_VOL_FILTER else 'OFF'} (vol ≥ {LOW_VOL_MULT:.2f}× SMA)\n"
        f"📉 Short support filter: {'ON' if ENABLE_SHORT_SUPPORT_FILTER else 'OFF'} (skip within {SHORT_SUPPORT_NEAR_PCT*100:.2f}% of 1H low)\n"
        "📊 Stats report: every 10 CLOSED trades (TP2 / GREEN / LOSS)\n\n"
        f"🕐 Started: {ct_time_str()}\n\n"
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
    recent_coin_calls[norm_symbol(symbol)] = now

# ======================================================
# TTL CACHES (PERF)
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
universe_cache = TTLCache()
movers_cache = TTLCache()

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

    df["atr_pct"] = df["atr"] / df["close"]
    df["range_sma_pct"] = df["range_sma"] / df["close"]
    return df

def add_indicators_1h(df: pd.DataFrame) -> pd.DataFrame:
    df["ema_fast"] = df["close"].ewm(span=CTX_EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=CTX_EMA_SLOW, adjust=False).mean()
    return df

def get_df_cached(ex_name: str, ex, symbol: str, tf: str, limit: int, ttl_sec: int) -> Optional[pd.DataFrame]:
    key = (ex_name, symbol, tf, limit)
    hit = ohlcv_cache.get(key)
    if hit is not None:
        return hit
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        if tf == "15m":
            df = add_indicators_15m(df)
        elif tf == "1h":
            df = add_indicators_1h(df)
        ohlcv_cache.set(key, df, ttl_sec)
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
EX_MARKETS_READY: Dict[str, bool] = {}

def get_ex_cached(name: str):
    if name in EX_INSTANCES and EX_INSTANCES[name]:
        return EX_INSTANCES[name]
    ex = get_ex(name)
    EX_INSTANCES[name] = ex
    EX_MARKETS_READY[name] = False
    return ex

def ensure_markets_loaded(ex_name: str, ex) -> bool:
    if EX_MARKETS_READY.get(ex_name):
        return True
    try:
        ex.load_markets()
        EX_MARKETS_READY[ex_name] = True
        return True
    except Exception as e:
        log.error(f"load_markets failed ({ex_name}): {e}")
        return False

# ======================================================
# QUALITY UNIVERSE + MOVERS
# ======================================================

def build_quality_universe_from_tickers(markets, tickers) -> list:
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

def get_quality_universe(ex_name: str, ex) -> list:
    key = ("universe", ex_name)
    hit = universe_cache.get(key)
    if hit is not None:
        return hit

    try:
        if not ensure_markets_loaded(ex_name, ex):
            return []
        tickers = ex.fetch_tickers()
        pairs = build_quality_universe_from_tickers(ex.markets, tickers)
        universe_cache.set(key, pairs, UNIVERSE_TTL_SEC)
        return pairs
    except Exception as e:
        log.error(f"Universe build error ({ex_name}): {e}")
        return []

def detect_top_movers_from_tickers(ex_name: str, ex) -> list:
    key = ("movers", ex_name)
    hit = movers_cache.get(key)
    if hit is not None:
        return hit

    try:
        tickers = ex.fetch_tickers()
    except Exception as e:
        log.error(f"Tickers error ({ex_name}): {e}")
        return []

    pairs = get_quality_universe(ex_name, ex)
    movers = []

    for s in pairs:
        t = tickers.get(s) or {}
        pct = t.get("percentage")
        last = t.get("last") or t.get("close")
        open_ = t.get("open")

        try:
            if pct is not None:
                score = abs(float(pct))
            elif last is not None and open_ is not None and float(open_) != 0:
                score = abs((float(last) - float(open_)) / float(open_) * 100.0)
            else:
                continue
        except Exception:
            continue

        movers.append((s, score))

    movers.sort(key=lambda x: x[1], reverse=True)
    top = [m[0] for m in movers[:TOP_MOVER_COUNT]]
    movers_cache.set(key, top, MOVERS_TTL_SEC)
    return top

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

def chop_ok(df_15m: pd.DataFrame) -> bool:
    if not ENABLE_CHOP_FILTER:
        return True
    last = df_15m.iloc[-1]
    v = last.get("atr_pct", None)
    if v is None or pd.isna(v):
        return False
    return float(v) >= CHOP_ATR_PCT_MIN

def _candle_body_pct(row) -> float:
    rng = float(row["high"] - row["low"])
    if rng <= 0:
        return 0.0
    body = abs(float(row["close"] - row["open"]))
    return body / rng

# ======================================================
# PRIORITY #3: LONG HEADROOM + PRIORITY #4: SHORT SUPPORT PROXIMITY
# ======================================================

def has_headroom_1h(df_1h: pd.DataFrame, entry: float, lookback: int, min_room_pct: float) -> bool:
    if len(df_1h) < lookback:
        lookback = len(df_1h)
    if lookback <= 5:
        return True
    recent_high = float(df_1h["high"].tail(lookback).max())
    room = (recent_high - float(entry)) / float(entry)
    return room >= float(min_room_pct)

def near_support_1h(df_1h: pd.DataFrame, entry: float, lookback: int, near_pct: float) -> bool:
    if len(df_1h) < lookback:
        lookback = len(df_1h)
    if lookback <= 5:
        return False
    recent_low = float(df_1h["low"].tail(lookback).min())
    dist = (float(entry) - recent_low) / float(entry)
    return dist <= float(near_pct)

# ======================================================
# DEMAND ZONE (LONGS)
# ======================================================

def detect_demand_zone(df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df_15m) < (BASE_LOOKBACK + RANGE_SMA_LEN + 10):
        return None

    i = len(df_15m) - 1
    disp = df_15m.iloc[i]

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
        "invalidated": False,
    }

def zone_invalidated_long(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    return float(df_15m["close"].iloc[-1]) < float(zone["bottom"])

def detect_zone_tap_long(df_15m: pd.DataFrame, zone: Dict[str, Any]) -> bool:
    last = df_15m.iloc[-1]
    low = float(last["low"])
    close = float(last["close"])
    touched = low <= float(zone["top"])
    not_closed_below = close >= float(zone["bottom"])
    return bool(touched and not_closed_below)

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

# ======================================================
# PUMP DETECTION (LONGS)
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

# ======================================================
# LONG ENTRY MODEL 2A HELPERS
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

def retest_seen_long(df_15m: pd.DataFrame, pump_high: float) -> bool:
    last = df_15m.iloc[-1]
    lo = float(last["low"])
    o = float(last["open"])
    c = float(last["close"])
    if lo > pump_high:
        return False
    if c <= pump_high:
        return False
    body = abs(c - o)
    lower_wick = min(o, c) - lo
    return lower_wick > body

def confirm_entry_long(df_15m: pd.DataFrame, pump_high: float) -> bool:
    last = df_15m.iloc[-1]
    return float(last["close"]) > pump_high and float(last["close"]) > float(last["open"])

# ======================================================
# SHORTS — DIVERGENCE + MSS + RETEST (HIGH WIN RATE)
# ======================================================

def _swing_highs(df: pd.DataFrame, lookback: int) -> List[int]:
    idxs = []
    start = max(1, len(df) - lookback - 1)
    end = len(df) - 1
    for i in range(start, end):
        if i <= 0 or i >= len(df) - 1:
            continue
        if float(df["high"].iloc[i]) > float(df["high"].iloc[i - 1]) and float(df["high"].iloc[i]) > float(df["high"].iloc[i + 1]):
            idxs.append(i)
    return idxs

def _pivot_low_between(df: pd.DataFrame, i1: int, i2: int) -> Optional[Tuple[int, float]]:
    if i2 <= i1 + 1:
        return None
    seg = df.iloc[i1:i2+1]
    if seg.empty:
        return None
    piv_idx = int(seg["low"].idxmin())
    piv_low = float(df.loc[piv_idx, "low"])
    return piv_idx, piv_low

def trigger_vol_ok(df_15m: pd.DataFrame) -> bool:
    if not SHORT_REQUIRE_TRIGGER_VOL:
        return True
    last = df_15m.iloc[-1]
    if pd.isna(last["vol_sma"]) or float(last["vol_sma"]) <= 0:
        return False
    return float(last["volume"]) >= float(last["vol_sma"]) * SHORT_TRIGGER_VOL_MULT

def mss_confirmed_short(df_15m: pd.DataFrame, pivot_low: float) -> bool:
    last = df_15m.iloc[-1]
    c = float(last["close"])
    o = float(last["open"])
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return False
    return (c < float(pivot_low) - 0.15 * atr) and (abs(c - o) > 0.25 * atr)

def retest_reject_short(df_15m: pd.DataFrame, pivot_low: float) -> bool:
    last = df_15m.iloc[-1]
    hi = float(last["high"])
    lo = float(last["low"])
    o  = float(last["open"])
    c  = float(last["close"])

    max_above = float(pivot_low) * (1.0 + SHORT_RETEST_MAX_ABOVE_PIVOT_PCT)
    touched = (hi >= float(pivot_low)) and (hi <= max_above)
    if not touched:
        return False

    if not (c < o and c < float(pivot_low)):
        return False

    body = abs(c - o)
    upper_wick = hi - max(o, c)
    rng = max(hi - lo, 1e-12)
    close_pos = (c - lo) / rng

    return (upper_wick > body) and (close_pos < 0.3) and trigger_vol_ok(df_15m)

def detect_bearish_divergence(df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    if len(df_15m) < max(60, DIV_LOOKBACK + 5):
        return None

    swings = _swing_highs(df_15m, DIV_LOOKBACK)
    if len(swings) < 2:
        return None

    i1, i2 = swings[-2], swings[-1]
    if (i2 - i1) < DIV_MIN_SWING_SEPARATION:
        return None

    p1 = float(df_15m["high"].iloc[i1])
    p2 = float(df_15m["high"].iloc[i2])
    r1 = float(df_15m["rsi"].iloc[i1])
    r2 = float(df_15m["rsi"].iloc[i2])

    if p1 <= 0:
        return None

    price_delta_pct = (p2 - p1) / p1
    if price_delta_pct < DIV_MIN_PRICE_DELTA_PCT:
        return None

    rsi_delta = (r2 - r1)
    if rsi_delta > -DIV_MIN_RSI_DELTA:
        return None

    if DIV_REQUIRE_RSI_OVERBOUGHT and r1 < DIV_RSI_OVERBOUGHT_LEVEL:
        return None

    piv = _pivot_low_between(df_15m, i1, i2)
    if not piv:
        return None
    piv_idx, piv_low = piv

    return {
        "swing1_idx": int(i1),
        "swing2_idx": int(i2),
        "swing1_high": float(p1),
        "swing2_high": float(p2),
        "swing1_rsi": float(r1),
        "swing2_rsi": float(r2),
        "pivot_idx": int(piv_idx),
        "pivot_low": float(piv_low),
    }

# ======================================================
# RISK LABEL + DYNAMIC TP2
# ======================================================

def risk_label(score: float) -> Tuple[str, str]:
    if score >= RISK_A_PLUS_MIN:
        return ("LOW", "A+")
    if score >= RISK_A_MIN:
        return ("LOW–MED", "A")
    if score >= RISK_B_MIN:
        return ("MED", "B")
    return ("HIGH", "C")

def calc_quality_score_long(zone: Dict[str, Any], pump: Dict[str, Any], df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> float:
    score = 0.0
    max_score = 10.0

    pump_pct = float(pump["move_pct"])
    score += 2.0 if pump_pct >= 6.0 else 1.5 if pump_pct >= 5.5 else 1.0 if pump_pct >= 5.0 else 0.5

    zone_size = float(zone["top"]) - float(zone["bottom"])
    if zone_size > 0:
        rh = zone.get("reaction_high", float(zone["top"]))
        reaction_r = (float(rh) - float(zone["top"])) / zone_size
        score += 2.0 if reaction_r >= 2.5 else 1.5 if reaction_r >= 2.0 else 1.0 if reaction_r >= 1.5 else 0.5
    else:
        score += 0.5

    last = df_15m.iloc[-1]
    if not pd.isna(last["vol_sma"]) and float(last["vol_sma"]) > 0:
        vr = float(last["volume"]) / float(last["vol_sma"])
        score += 2.0 if vr >= 1.5 else 1.5 if vr >= 1.2 else 1.0 if vr >= 1.0 else 0.5
    else:
        score += 0.5

    last_1h = df_1h.iloc[-1]
    if not pd.isna(last_1h["ema_fast"]) and float(last_1h["ema_fast"]) > 0:
        dist = (float(last_1h["close"]) - float(last_1h["ema_fast"])) / float(last_1h["ema_fast"]) * 100.0
        score += 2.0 if dist >= 2.0 else 1.5 if dist >= 1.0 else 1.0 if dist >= 0.5 else 0.5
    else:
        score += 0.5

    score += 2.0 if BASE_MAX_BODY_PCT <= 0.35 else 1.5 if BASE_MAX_BODY_PCT <= 0.40 else 1.0 if BASE_MAX_BODY_PCT <= 0.45 else 0.5
    return round(max(0.0, min(max_score, score)), 2)

def calc_quality_score_short_div(df_15m: pd.DataFrame, df_1h: pd.DataFrame, div: Dict[str, Any]) -> float:
    score = 0.0

    p1 = float(div["swing1_high"]); p2 = float(div["swing2_high"])
    r1 = float(div["swing1_rsi"]);  r2 = float(div["swing2_rsi"])
    price_up = max(0.0, (p2 - p1) / p1 * 100.0)
    rsi_down = max(0.0, (r1 - r2))

    score += 2.0 if price_up >= 0.8 else 1.5 if price_up >= 0.4 else 1.0
    score += 2.0 if rsi_down >= 8 else 1.5 if rsi_down >= 5 else 1.0

    last = df_15m.iloc[-1]
    if not pd.isna(last["vol_sma"]) and float(last["vol_sma"]) > 0:
        vr = float(last["volume"]) / float(last["vol_sma"])
        score += 2.0 if vr >= 1.5 else 1.5 if vr >= 1.1 else 1.0 if vr >= 0.85 else 0.5
    else:
        score += 0.5

    last_1h = df_1h.iloc[-1]
    if not pd.isna(last_1h["ema_fast"]) and float(last_1h["ema_fast"]) > 0:
        dist = (float(last_1h["close"]) - float(last_1h["ema_fast"])) / float(last_1h["ema_fast"]) * 100.0
        score += 2.0 if dist >= 2.0 else 1.5 if dist >= 1.0 else 1.0 if dist >= 0.5 else 0.5
    else:
        score += 0.5

    score += 1.5
    return round(max(0.0, min(10.0, score)), 2)

def dynamic_tp2_rr(score: float) -> float:
    if not TP2_DYNAMIC:
        return float(TP2_RR_MIN)
    score_norm = max(0.0, min(1.0, score / 10.0))
    rr = TP2_RR_MIN + score_norm * (TP2_RR_MAX - TP2_RR_MIN)
    return round(max(TP2_RR_MIN, min(TP2_RR_MAX, rr)), 2)

# ======================================================
# TRADE BUILDING
# ======================================================

def build_trade_long(ex_name: str, symbol: str, entry: float, zone: Dict[str, Any], df_15m: pd.DataFrame, pump: Dict[str, Any], df_1h: pd.DataFrame) -> Optional[Dict[str, Any]]:
    last = df_15m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None

    entry = float(entry)
    tap_low = zone.get("tap_low")
    if tap_low is None:
        tap_low = float(last["low"])

    stop = float(tap_low) * (1.0 - WICK_STOP_BUFFER_PCT)

    if STOP_METHOD == "ATR":
        atr_stop = entry - ATR_STOP_MULT * atr
        stop = min(stop, atr_stop)

    if stop <= 0 or stop >= entry:
        return None

    risk_dist = entry - stop
    if (risk_dist / entry) < MIN_RISK_PCT:
        return None

    tp1 = entry + TP1_RR * risk_dist

    q_score = calc_quality_score_long(zone, pump, df_15m, df_1h)
    tp2_rr = dynamic_tp2_rr(q_score)
    tp2 = entry + tp2_rr * risk_dist

    risk_txt, grade = risk_label(q_score)

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "LONG",
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
    }

def build_trade_short_div(ex_name: str, symbol: str, entry: float, div: Dict[str, Any], df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> Optional[Dict[str, Any]]:
    last = df_15m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None

    entry = float(entry)
    sweep_high = float(div["swing2_high"])

    struct_stop = sweep_high * (1.0 + WICK_STOP_BUFFER_PCT)
    atr_stop = entry + ATR_STOP_MULT * atr
    stop = max(struct_stop, atr_stop) if STOP_METHOD == "ATR" else struct_stop

    stop_cap = entry * (1.0 + SHORT_STOP_CAP_PCT)
    if stop > stop_cap:
        stop = stop_cap

    if stop <= entry:
        return None

    risk_dist = stop - entry
    if (risk_dist / entry) < MIN_RISK_PCT:
        return None

    if SHORT_USE_PCT_TPS:
        tp1 = max(entry * (1.0 - SHORT_TP1_PCT), MIN_TP_PRICE)
        tp2 = max(entry * (1.0 - SHORT_TP2_PCT), MIN_TP_PRICE)
        if tp2 >= tp1:
            tp2 = max(tp1 * 0.999, MIN_TP_PRICE)
        tp2_rr_effective = round((entry - tp2) / risk_dist, 2)
    else:
        tp1 = entry - TP1_RR * risk_dist
        q_score_tmp = calc_quality_score_short_div(df_15m, df_1h, div)
        tp2_rr_target = dynamic_tp2_rr(q_score_tmp)
        tp2 = entry - tp2_rr_target * risk_dist
        if tp1 <= 0 or tp2 <= 0:
            return None
        tp2_rr_effective = tp2_rr_target

    q_score = calc_quality_score_short_div(df_15m, df_1h, div)
    risk_txt, grade = risk_label(q_score)

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "SHORT",
        "entry": float(entry),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp1_hit": False,
        "tp1_partial_taken": False,
        "tp2_rr": float(tp2_rr_effective),
        "quality_score": float(q_score),
        "risk_text": risk_txt,
        "risk_grade": grade,
        "status": "ACTIVE",
        "start_ts": now,
        "created_ts": now,
        "div": div,
    }

# ======================================================
# TELEGRAM SIGNALS
# ======================================================

def send_signal(trade: Dict[str, Any]):
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

    if direction == "SHORT" and SHORT_USE_PCT_TPS:
        tp1_label = f"TP1 ({SHORT_TP1_PCT*100:.0f}%):"
        tp2_label = f"TP2 ({SHORT_TP2_PCT*100:.0f}%):"
    else:
        tp1_label = "TP1 (1R):"
        tp2_label = f"TP2 ({trade['tp2_rr']:.2f}R):"

    msg = (
        f"{emoji} {trade['symbol']}\n"
        f"{direction} — ✅ ENTRY CONFIRMED\n\n"
        f"📍 ENTRY: {trade['entry']:.6f}\n"
        f"🛑 STOP: {trade['stop']:.6f}\n\n"
        f"🎯 TAKE PROFITS:\n"
        f"{tp1_label} {trade['tp1']:.6f} — Take {int(TP1_SIZE_PCT*100)}%\n"
        f"{tp2_label} {trade['tp2']:.6f} — Take {int(TP2_SIZE_PCT*100)}%\n\n"
        f"🧯 RISK: {trade['risk_text']} ({trade['risk_grade']}) | Quality: {trade['quality_score']:.1f}/10\n"
        f"🕐 {ts} | {trade['ex_name'].upper()}\n\n"
        f"{funny}\n\n"
        "⚠️ Not financial advice. Info only."
    )
    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} {direction}")

    trade_key = f"{trade['ex_name']}|{trade['symbol']}|{trade['direction']}|{int(time.time())}"
    with open_trades_lock:
        open_trades[trade_key] = trade

def send_status(ex_name: str, symbol: str, direction: str, text: str):
    send_telegram(f"ℹ️ {symbol} {direction} ({ex_name.upper()}): {text}")

# ======================================================
# TRACKER + STATS (PRIORITY #1: METRICS FIX)
# ======================================================

def _record_closed(trade: Dict[str, Any], outcome: str, exit_price: float):
    """
    outcome:
      - "WIN"   => TP2 hit
      - "GREEN" => TP1 hit (partial) then stopped (BE/SL)
      - "LOSS"  => stopped before TP1
    """
    with stats_lock:
        closed_trades.append({
            "ex": trade["ex_name"],
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "outcome": outcome,
            "exit_price": float(exit_price),
            "tp1_partial_taken": trade.get("tp1_partial_taken", False),
            "tp1_hit": trade.get("tp1_hit", False),
            "closed_ts": int(time.time()),
        })

        if len(closed_trades) % STATS_BATCH_SIZE == 0:
            last_n = closed_trades[-STATS_BATCH_SIZE:]

            tp2_wins = sum(1 for t in last_n if t["outcome"] == "WIN")
            greens  = sum(1 for t in last_n if t["outcome"] == "GREEN")
            losses  = sum(1 for t in last_n if t["outcome"] == "LOSS")
            total = len(last_n)

            tp1_hits = sum(1 for t in last_n if t.get("tp1_hit") or t.get("tp1_partial_taken"))
            green_rate = (tp1_hits / total * 100.0) if total else 0.0
            tp2_rate = (tp2_wins / total * 100.0) if total else 0.0

            send_telegram(
                f"📊 CRT BOT PERFORMANCE (LAST {STATS_BATCH_SIZE} CLOSED TRADES)\n\n"
                f"Total closed: {total}\n\n"
                f"🏆 TP2 (WIN): {tp2_wins}\n"
                f"🟢 TP1+ (GREEN): {greens}\n"
                f"❌ LOSS: {losses}\n\n"
                f"📈 TP1+ Green Rate: {green_rate:.1f}%\n"
                f"🎯 TP2 Win Rate: {tp2_rate:.1f}%\n\n"
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
                    outcome = "GREEN" if t.get("tp1_partial_taken") else "LOSS"
                    _record_closed(t, outcome, px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "SHORT" and px >= stop:
                    send_telegram(f"❌ SL HIT — {t['symbol']} (SHORT) ({t['ex_name'].upper()})")
                    apply_stop_penalty(t["ex_name"], t["symbol"], "SHORT")
                    outcome = "GREEN" if t.get("tp1_partial_taken") else "LOSS"
                    _record_closed(t, outcome, px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # TP1 partial
                if not t.get("tp1_hit", False):
                    if direction == "LONG" and px >= tp1:
                        send_telegram(f"✅ TP1 HIT — {t['symbol']} (LONG) ({t['ex_name'].upper()})")
                        with open_trades_lock:
                            if k in open_trades:
                                open_trades[k]["tp1_hit"] = True
                                open_trades[k]["tp1_partial_taken"] = True
                        continue

                    if direction == "SHORT" and px <= tp1:
                        send_telegram(f"✅ TP1 HIT — {t['symbol']} (SHORT) ({t['ex_name'].upper()})")
                        with open_trades_lock:
                            if k in open_trades:
                                open_trades[k]["tp1_hit"] = True
                                open_trades[k]["tp1_partial_taken"] = True

                                if SHORT_MOVE_SL_TO_BE_AFTER_TP1:
                                    entry = float(open_trades[k]["entry"])
                                    be = entry * (1.0 + SHORT_BE_BUFFER_PCT)
                                    open_trades[k]["stop"] = min(float(open_trades[k]["stop"]), be)
                        continue

                # TP2 close
                if direction == "LONG" and px >= tp2:
                    send_telegram(f"🏁 TP2 HIT — {t['symbol']} (LONG) ({t['ex_name'].upper()})")
                    _record_closed(t, "WIN", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "SHORT" and px <= tp2:
                    send_telegram(f"🏁 TP2 HIT — {t['symbol']} (SHORT) ({t['ex_name'].upper()})")
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
    now = int(time.time())
    if skey not in symbol_state:
        symbol_state[skey] = {"LONG": {}, "SHORT": {}, "_last_seen_ts": now}
    else:
        symbol_state[skey]["_last_seen_ts"] = now
    return symbol_state[skey]

def _reset_side(st_side: Dict[str, Any]):
    st_side.clear()

_last_cleanup_ts = 0

def cleanup_symbol_state():
    global _last_cleanup_ts
    now = int(time.time())
    if (now - _last_cleanup_ts) < STATE_CLEANUP_EVERY_SEC:
        return
    _last_cleanup_ts = now

    stale_keys = []
    for k, v in symbol_state.items():
        last_seen = int(v.get("_last_seen_ts", 0))
        if last_seen and (now - last_seen) > STATE_STALE_AFTER_SEC:
            stale_keys.append(k)

    for k in stale_keys:
        symbol_state.pop(k, None)

    if stale_keys:
        log.info(f"State cleanup: removed {len(stale_keys)} stale symbol buckets")

def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        cleanup_symbol_state()

        if in_news_blackout():
            time.sleep(SCAN_INTERVAL)
            continue

        for ex_name in EXCHANGES:
            ex = get_ex_cached(ex_name)
            if not ex:
                continue
            if not ensure_markets_loaded(ex_name, ex):
                continue

            movers = detect_top_movers_from_tickers(ex_name, ex)

            for symbol in movers:
                try:
                    df_15m = get_df_cached(ex_name, ex, symbol, "15m", limit=OHLCV_LIMIT_15M, ttl_sec=OHLCV_15M_TTL_SEC)
                    df_1h  = get_df_cached(ex_name, ex, symbol, "1h",  limit=OHLCV_LIMIT_1H,  ttl_sec=OHLCV_1H_TTL_SEC)
                    if df_15m is None or df_1h is None:
                        continue
                    if len(df_15m) < 140 or len(df_1h) < 80:
                        continue

                    if not low_vol_ok(df_15m):
                        continue

                    if not chop_ok(df_15m):
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
                            if zoneL and zone_invalidated_long(df_15m, zoneL):
                                _reset_side(stL)
                                zoneL = None

                            if not zoneL:
                                new_zone = detect_demand_zone(df_15m)
                                if new_zone:
                                    stL["zone"] = new_zone
                                continue

                            # Tap
                            if not zoneL.get("tapped", False):
                                if detect_zone_tap_long(df_15m, zoneL):
                                    zoneL["tapped"] = True
                                    zoneL["tap_ts"] = int(df_15m["ts"].iloc[-1])
                                    zoneL["tap_low"] = float(df_15m["low"].iloc[-1])
                                    zoneL["tap_high"] = float(df_15m["high"].iloc[-1])
                                    stL["zone"] = zoneL
                                else:
                                    continue
                            else:
                                zoneL["tap_low"] = min(float(zoneL.get("tap_low", df_15m["low"].iloc[-1])), float(df_15m["low"].iloc[-1]))
                                stL["zone"] = zoneL

                            # Reaction
                            zoneL = update_reaction_long(df_15m, zoneL)
                            stL["zone"] = zoneL
                            if not zoneL.get("reacted", False):
                                continue

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
                                    continue

                            pump_high = float(stL["pump_high"])

                            # Timeout
                            elapsed = (len(df_15m) - 1) - int(stL.get("phase_started_idx", len(df_15m) - 1))
                            if elapsed > RETEST_TIMEOUT_CANDLES:
                                _reset_side(stL)
                                continue

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
                                    send_status(ex_name, symbol, "LONG", "📍 Quality retest seen — waiting for confirmation candle.")
                            elif phase == "WAIT_CONFIRM":
                                if confirm_entry_long(df_15m, pump_high):
                                    entry = float(df_15m["close"].iloc[-1])

                                    if ENABLE_LONG_HEADROOM_FILTER and not has_headroom_1h(
                                        df_1h, entry, LONG_HEADROOM_LOOKBACK_1H, LONG_HEADROOM_MIN_PCT
                                    ):
                                        continue

                                    if allow_signal(ex_name, symbol, "LONG") and allow_coin(symbol):
                                        trade = build_trade_long(ex_name, symbol, entry, zoneL, df_15m, stL["pump"], df_1h)
                                        if trade:
                                            send_signal(trade)
                                            _reset_side(stL)

                    # -------------------------
                    # SHORT SIDE (HI WIN RATE)
                    # -------------------------
                    if TRADE_MODE in ("both", "short_only") and ENABLE_SHORT_DIVERGENCE:
                        stS = st_bucket["SHORT"]

                        if SHORT_REQUIRE_1H_BEAR and not ctx_bearish_1h(df_1h):
                            _reset_side(stS)
                            continue

                        div = detect_bearish_divergence(df_15m)
                        if not div:
                            _reset_side(stS)
                            continue

                        swing2_idx = int(div["swing2_idx"])
                        swing2_ts = int(df_15m["ts"].iloc[swing2_idx])

                        last_traded_ts = int(stS.get("last_traded_div_swing2_ts", 0))
                        if swing2_ts <= last_traded_ts:
                            continue

                        stS["div"] = div
                        stS["swing2_ts"] = swing2_ts
                        stS["pivot_low"] = float(div["pivot_low"])

                        pivot_low = float(stS["pivot_low"])
                        phase = stS.get("phase", "WAIT_MSS")

                        if phase == "WAIT_MSS":
                            if mss_confirmed_short(df_15m, pivot_low) and trigger_vol_ok(df_15m):
                                stS["phase"] = "WAIT_RETEST" if SHORT_ENTRY_MODE == "retest_only" else "TRIGGER_ENTRY"
                                stS["phase_started_idx"] = len(df_15m) - 1
                                send_status(ex_name, symbol, "SHORT", "✅ MSS impulse confirmed — waiting for retest rejection.")
                            else:
                                continue

                        elapsed = (len(df_15m) - 1) - int(stS.get("phase_started_idx", len(df_15m) - 1))
                        if elapsed > SHORT_RETEST_TIMEOUT_CANDLES:
                            _reset_side(stS)
                            continue

                        if stS.get("phase") == "TRIGGER_ENTRY":
                            entry = float(df_15m["close"].iloc[-1])

                            if ENABLE_SHORT_SUPPORT_FILTER and near_support_1h(
                                df_1h, entry, SHORT_SUPPORT_LOOKBACK_1H, SHORT_SUPPORT_NEAR_PCT
                            ):
                                _reset_side(stS)
                                continue

                            if allow_signal(ex_name, symbol, "SHORT") and allow_coin(symbol):
                                trade = build_trade_short_div(ex_name, symbol, entry, div, df_15m, df_1h)
                                if trade:
                                    send_signal(trade)
                                    stS["last_traded_div_swing2_ts"] = swing2_ts
                                    _reset_side(stS)
                            continue

                        if stS.get("phase") == "WAIT_RETEST":
                            if not retest_reject_short(df_15m, pivot_low):
                                continue

                            entry = float(df_15m["close"].iloc[-1])

                            if ENABLE_SHORT_SUPPORT_FILTER and near_support_1h(
                                df_1h, entry, SHORT_SUPPORT_LOOKBACK_1H, SHORT_SUPPORT_NEAR_PCT
                            ):
                                _reset_side(stS)
                                continue

                            if allow_signal(ex_name, symbol, "SHORT") and allow_coin(symbol):
                                trade = build_trade_short_div(ex_name, symbol, entry, div, df_15m, df_1h)
                                if trade:
                                    send_signal(trade)
                                    stS["last_traded_div_swing2_ts"] = swing2_ts
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
    return "CRT 15m STRATEGY BOT RUNNING (INFO ONLY) — OPTION B BALANCED (TP1-FIRST LONG TUNING APPLIED)"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
