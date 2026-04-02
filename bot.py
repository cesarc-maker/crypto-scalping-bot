# ======================================================
# CRT 15-MINUTE STRATEGY BOT — OPTION B (BALANCED) + LONG/SHORT
# OKX + KUCOIN FUTURES • HIGH WIN RATE CONFIGURATION (INFO ONLY)
#
# INCLUDED:
# - LONGS: Breakout -> Retest -> Confirm -> Enter
# - SHORTS: 1H Bear Regime + Divergence -> MSS -> Retest Rejection
# - TP1-first long tuning
# - Trade lifecycle updates
# - Trade IDs
# - Structured trade cards
# - Analytics + recap engine
# - Optimization suggestions
#
# ⚠️ INFO ONLY. NOT FINANCIAL ADVICE. NO EXECUTION.
# ======================================================

import os
import time
import math
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
log = logging.getLogger("CRT_15M_OPTION_B_V6_LIFECYCLE_ANALYTICS")

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

# HARDCODED TELEGRAM CHAT IDS
CHAT_IDS = [
    "-1003463990210",
    "-1003749616502",
]

# Cadence
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 20))
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 10))

# Exchanges (ONLY OKX + KuCoin Futures)
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]

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

# Demand Zone detection
BASE_LOOKBACK = int(os.getenv("BASE_LOOKBACK", 5))
BASE_MAX_BODY_PCT = float(os.getenv("BASE_MAX_BODY_PCT", 0.40))
DISP_BODY_PCT_MIN = float(os.getenv("DISP_BODY_PCT_MIN", 0.55))
RANGE_SMA_LEN = int(os.getenv("RANGE_SMA_LEN", 20))
DISP_RANGE_MULT = float(os.getenv("DISP_RANGE_MULT", 1.5))

# Tap + reaction
FIRST_TAP_ONLY = True
REACTION_R_MULT = float(os.getenv("REACTION_R_MULT", 1.5))

# Pump (LONGS)
PUMP_MIN_PCT = float(os.getenv("PUMP_MIN_PCT", 5.3))
PUMP_MAX_PCT = float(os.getenv("PUMP_MAX_PCT", 6.5))
PUMP_MAX_CANDLES = int(os.getenv("PUMP_MAX_CANDLES", 4))
BREAK_LOOKBACK = int(os.getenv("BREAK_LOOKBACK", 20))

# Volume filter
ENABLE_LOW_VOL_FILTER = os.getenv("ENABLE_LOW_VOL_FILTER", "1") == "1"
LOW_VOL_MULT = float(os.getenv("LOW_VOL_MULT", 1.05))

# Context EMAs
CTX_EMA_FAST = int(os.getenv("CTX_EMA_FAST", 20))
CTX_EMA_SLOW = int(os.getenv("CTX_EMA_SLOW", 50))

# RSI
RSI_LEN = int(os.getenv("RSI_LEN", 14))

# LONG MODEL 2A
BREAKOUT_CANDLES_REQUIRED = int(os.getenv("BREAKOUT_CANDLES_REQUIRED", 2))
RETEST_MAX_DIP_PCT = float(os.getenv("RETEST_MAX_DIP_PCT", 0.002))
RETEST_TIMEOUT_CANDLES = int(os.getenv("RETEST_TIMEOUT_CANDLES", 12))

# Short divergence
ENABLE_SHORT_DIVERGENCE = os.getenv("ENABLE_SHORT_DIVERGENCE", "1") == "1"
DIV_LOOKBACK = int(os.getenv("DIV_LOOKBACK", 30))
DIV_MIN_PRICE_DELTA_PCT = float(os.getenv("DIV_MIN_PRICE_DELTA_PCT", 0.002))
DIV_MIN_RSI_DELTA = float(os.getenv("DIV_MIN_RSI_DELTA", 2.0))
DIV_REQUIRE_RSI_OVERBOUGHT = os.getenv("DIV_REQUIRE_RSI_OVERBOUGHT", "0") == "1"
DIV_RSI_OVERBOUGHT_LEVEL = float(os.getenv("DIV_RSI_OVERBOUGHT_LEVEL", 65))

# Chop filter
ENABLE_CHOP_FILTER = os.getenv("ENABLE_CHOP_FILTER", "1") == "1"
CHOP_ATR_PCT_MIN = float(os.getenv("CHOP_ATR_PCT_MIN", 0.0027))

# Long headroom filter
ENABLE_LONG_HEADROOM_FILTER = os.getenv("ENABLE_LONG_HEADROOM_FILTER", "1") == "1"
LONG_HEADROOM_LOOKBACK_1H = int(os.getenv("LONG_HEADROOM_LOOKBACK_1H", 72))
LONG_HEADROOM_MIN_PCT = float(os.getenv("LONG_HEADROOM_MIN_PCT", 0.014))

# Short support filter
ENABLE_SHORT_SUPPORT_FILTER = os.getenv("ENABLE_SHORT_SUPPORT_FILTER", "1") == "1"
SHORT_SUPPORT_LOOKBACK_1H = int(os.getenv("SHORT_SUPPORT_LOOKBACK_1H", 72))
SHORT_SUPPORT_NEAR_PCT = float(os.getenv("SHORT_SUPPORT_NEAR_PCT", 0.01))

# Short execution settings
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
LONG_MOVE_SL_TO_BE_AFTER_TP1 = os.getenv("LONG_MOVE_SL_TO_BE_AFTER_TP1", "0") == "1"
SHORT_BE_BUFFER_PCT = float(os.getenv("SHORT_BE_BUFFER_PCT", 0.0002))
LONG_BE_BUFFER_PCT = float(os.getenv("LONG_BE_BUFFER_PCT", 0.0002))

# Risk management
WICK_STOP_BUFFER_PCT = float(os.getenv("WICK_STOP_BUFFER_PCT", 0.0005))
STOP_METHOD = os.getenv("STOP_METHOD", "STRUCT").strip().upper()
if STOP_METHOD not in ("STRUCT", "ATR"):
    STOP_METHOD = "STRUCT"

ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", 1.0))
MIN_RISK_PCT = float(os.getenv("MIN_RISK_PCT", 0.0015))

# TPs
TP1_RR = float(os.getenv("TP1_RR", 1.0))
TP1_SIZE_PCT = float(os.getenv("TP1_SIZE_PCT", 0.25))
TP2_SIZE_PCT = float(os.getenv("TP2_SIZE_PCT", 0.75))
TP2_DYNAMIC = os.getenv("TP2_DYNAMIC", "1") == "1"
TP2_RR_MIN = float(os.getenv("TP2_RR_MIN", 2.0))
TP2_RR_MAX = float(os.getenv("TP2_RR_MAX", 4.0))

# Optional informational TP3
ENABLE_INFO_TP3 = os.getenv("ENABLE_INFO_TP3", "1") == "1"
LONG_TP3_RR = float(os.getenv("LONG_TP3_RR", 5.0))
SHORT_TP3_PCT = float(os.getenv("SHORT_TP3_PCT", 0.40))

# Short normalization
SHORT_STOP_CAP_PCT = float(os.getenv("SHORT_STOP_CAP_PCT", 0.12))
SHORT_TP1_PCT = float(os.getenv("SHORT_TP1_PCT", 0.08))
SHORT_TP2_PCT = float(os.getenv("SHORT_TP2_PCT", 0.25))
SHORT_USE_PCT_TPS = os.getenv("SHORT_USE_PCT_TPS", "1") == "1"
MIN_TP_PRICE = float(os.getenv("MIN_TP_PRICE", 1e-8))

# Trade card fields
RISK_PCT_TEXT = os.getenv("RISK_PCT_TEXT", "1%")
LEVERAGE_TEXT = os.getenv("LEVERAGE_TEXT", "3x isolated")
POSITION_SIZE_TEXT = os.getenv("POSITION_SIZE_TEXT", "Risk only 1% of account equity")

# Cooldowns
COIN_COOLDOWN_SEC = int(os.getenv("COIN_COOLDOWN_SEC", 3600))
WINDOW = int(os.getenv("WINDOW", 1800))
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 7200))

# Labels
RISK_A_PLUS_MIN = float(os.getenv("RISK_A_PLUS_MIN", 8.0))
RISK_A_MIN = float(os.getenv("RISK_A_MIN", 6.5))
RISK_B_MIN = float(os.getenv("RISK_B_MIN", 5.0))

# Stats / recaps
STATS_BATCH_SIZE = int(os.getenv("STATS_BATCH_SIZE", 10))

# News blackout
NEWS_BLACKOUT_UTC = os.getenv("NEWS_BLACKOUT_UTC", "").strip()

# Cache controls
UNIVERSE_TTL_SEC = int(os.getenv("UNIVERSE_TTL_SEC", 15 * 60))
MOVERS_TTL_SEC = int(os.getenv("MOVERS_TTL_SEC", 120))
OHLCV_15M_TTL_SEC = int(os.getenv("OHLCV_15M_TTL_SEC", 30))
OHLCV_1H_TTL_SEC = int(os.getenv("OHLCV_1H_TTL_SEC", 120))
OHLCV_LIMIT_15M = int(os.getenv("OHLCV_LIMIT_15M", 160))
OHLCV_LIMIT_1H = int(os.getenv("OHLCV_LIMIT_1H", 120))

# State cleanup
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
    return symbol.split(":")[0].replace("/", "").strip()

def allow_coin(symbol: str) -> bool:
    now = time.time()
    key = norm_symbol(symbol)
    last = recent_coin_calls.get(key)
    if last is not None and (now - last) < COIN_COOLDOWN_SEC:
        return False
    recent_coin_calls[key] = now
    return True

def make_trade_id(ex_name: str, symbol: str, direction: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%m%d-%H%M")
    base = norm_symbol(symbol)
    side = "L" if direction == "LONG" else "S"
    ex_tag = "OKX" if ex_name == "okx" else "KCF"
    return f"{base}-{side}-{ex_tag}-{ts}"

def fmt_price(px: float) -> str:
    if px >= 1000:
        return f"{px:,.2f}"
    if px >= 1:
        return f"{px:.4f}"
    if px >= 0.01:
        return f"{px:.6f}"
    return f"{px:.8f}"

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

    MAX_LEN = 3800
    chunks = [text[i:i+MAX_LEN] for i in range(0, len(text), MAX_LEN)]

    for cid in CHAT_IDS:
        for ch in chunks:
            try:
                url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
                r = requests.post(url, json={"chat_id": cid, "text": ch}, timeout=10)
                print("SEND TO:", cid, "STATUS:", r.status_code, "BODY:", r.text)
                if r.status_code >= 400:
                    log.error(f"Telegram HTTP {r.status_code}: {r.text[:500]}")
            except Exception as e:
                print("TELEGRAM EXCEPTION:", cid, str(e))
                log.error(f"Telegram error for {cid}: {e}")

def send_startup():
    msg = (
        "🤖 CRT 15M BOT STARTED — LONG+SHORT\n\n"
        "✅ LONGS: Breakout → Retest → Confirm → Enter\n"
        "✅ SHORTS: 1H Bear Regime + Divergence → MSS → Retest Rejection\n"
        "✅ Lifecycle updates + analytics + optimization suggestions enabled\n\n"
        f"📈 Long headroom filter: {'ON' if ENABLE_LONG_HEADROOM_FILTER else 'OFF'} ({LONG_HEADROOM_MIN_PCT*100:.2f}% / {LONG_HEADROOM_LOOKBACK_1H}h)\n"
        f"🧹 Chop filter: {'ON' if ENABLE_CHOP_FILTER else 'OFF'} ({CHOP_ATR_PCT_MIN*100:.2f}% ATR)\n"
        f"📊 Stats batch: {STATS_BATCH_SIZE} closed trades\n"
        f"🧊 Coin cooldown: {COIN_COOLDOWN_SEC//60} min\n\n"
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
    now = utc_ts()
    for a, b in BLACKOUTS:
        if a <= now <= b:
            return True
    return False

# ======================================================
# COOLDOWNS
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
# TTL CACHE
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

def has_headroom_1h(df_1h: pd.DataFrame, entry: float, lookback: int, min_room_pct: float) -> bool:
    if len(df_1h) < lookback:
        lookback = len(df_1h)
    if lookback <= 5:
        return True
    recent_high = float(df_1h["high"].tail(lookback).max())
    room = (recent_high - float(entry)) / float(entry)
    return room >= float(min_room_pct)

def calc_headroom_pct_1h(df_1h: pd.DataFrame, entry: float, lookback: int) -> float:
    if len(df_1h) < lookback:
        lookback = len(df_1h)
    if lookback <= 5 or entry <= 0:
        return 0.0
    recent_high = float(df_1h["high"].tail(lookback).max())
    return (recent_high - entry) / entry * 100.0

def near_support_1h(df_1h: pd.DataFrame, entry: float, lookback: int, near_pct: float) -> bool:
    if len(df_1h) < lookback:
        lookback = len(df_1h)
    if lookback <= 5:
        return False
    recent_low = float(df_1h["low"].tail(lookback).min())
    dist = (float(entry) - recent_low) / float(entry)
    return dist <= float(near_pct)

def calc_support_distance_pct_1h(df_1h: pd.DataFrame, entry: float, lookback: int) -> float:
    if len(df_1h) < lookback:
        lookback = len(df_1h)
    if lookback <= 5 or entry <= 0:
        return 0.0
    recent_low = float(df_1h["low"].tail(lookback).min())
    return (entry - recent_low) / entry * 100.0

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
# LONG ENTRY HELPERS
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
# SHORTS — DIVERGENCE + MSS + RETEST
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
# RISK LABEL + QUALITY
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
# TRADE BUILDERS
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
    tp3 = entry + LONG_TP3_RR * risk_dist if ENABLE_INFO_TP3 else None

    risk_txt, grade = risk_label(q_score)
    now = utc_ts()

    return {
        "trade_id": make_trade_id(ex_name, symbol, "LONG"),
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "LONG",
        "entry": float(entry),
        "entry_range_low": float(entry * 0.998),
        "entry_range_high": float(entry * 1.002),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3) if tp3 else None,
        "tp1_hit": False,
        "tp1_partial_taken": False,
        "tp2_rr": float(tp2_rr),
        "quality_score": float(q_score),
        "risk_text": risk_txt,
        "risk_grade": grade,
        "status": "ACTIVE",
        "start_ts": now,
        "created_ts": now,
        "risk_pct": RISK_PCT_TEXT,
        "leverage": LEVERAGE_TEXT,
        "position_size_text": POSITION_SIZE_TEXT,
        "thesis": "Breakout confirmed, retest held, bullish 1H context, and strong volume participation.",
        "updates": [],
        "setup_type": "LONG_BREAKOUT_RETEST",
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
        tp3 = max(entry * (1.0 - SHORT_TP3_PCT), MIN_TP_PRICE) if ENABLE_INFO_TP3 else None
    else:
        tp1 = entry - TP1_RR * risk_dist
        q_score_tmp = calc_quality_score_short_div(df_15m, df_1h, div)
        tp2_rr_target = dynamic_tp2_rr(q_score_tmp)
        tp2 = entry - tp2_rr_target * risk_dist
        if tp1 <= 0 or tp2 <= 0:
            return None
        tp2_rr_effective = tp2_rr_target
        tp3 = entry - LONG_TP3_RR * risk_dist if ENABLE_INFO_TP3 else None

    q_score = calc_quality_score_short_div(df_15m, df_1h, div)
    risk_txt, grade = risk_label(q_score)
    now = utc_ts()

    return {
        "trade_id": make_trade_id(ex_name, symbol, "SHORT"),
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "SHORT",
        "entry": float(entry),
        "entry_range_low": float(entry * 0.998),
        "entry_range_high": float(entry * 1.002),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "tp3": float(tp3) if tp3 else None,
        "tp1_hit": False,
        "tp1_partial_taken": False,
        "tp2_rr": float(tp2_rr_effective),
        "quality_score": float(q_score),
        "risk_text": risk_txt,
        "risk_grade": grade,
        "status": "ACTIVE",
        "start_ts": now,
        "created_ts": now,
        "risk_pct": RISK_PCT_TEXT,
        "leverage": LEVERAGE_TEXT,
        "position_size_text": POSITION_SIZE_TEXT,
        "thesis": "Bearish divergence confirmed, MSS broke pivot support, retest rejected, and 1H bearish context remains intact.",
        "updates": [],
        "setup_type": "SHORT_DIV_MSS_RETEST",
        "div": div,
    }

# ======================================================
# ANALYTICS ENRICHMENT
# ======================================================

def enrich_trade_analytics(trade: Dict[str, Any], df_15m: pd.DataFrame, df_1h: pd.DataFrame, setup_type: str):
    last = df_15m.iloc[-1]

    vol_ratio = float(last["volume"]) / float(last["vol_sma"]) if float(last["vol_sma"]) > 0 else 0.0
    atr_pct = (float(last["atr"]) / float(last["close"]) * 100.0) if float(last["close"]) > 0 else 0.0
    entry = float(trade["entry"])
    headroom_pct_1h = calc_headroom_pct_1h(df_1h, entry, LONG_HEADROOM_LOOKBACK_1H)

    trade["setup_type"] = setup_type
    trade["features"] = {
        "vol_ratio": round(vol_ratio, 3),
        "atr_pct": round(atr_pct, 3),
        "headroom_pct_1h": round(headroom_pct_1h, 3),
        "quality_score": trade.get("quality_score", 0),
    }

    trade["config_snapshot"] = {
        "LOW_VOL_MULT": LOW_VOL_MULT,
        "CHOP_ATR_PCT_MIN": CHOP_ATR_PCT_MIN,
        "LONG_HEADROOM_MIN_PCT": LONG_HEADROOM_MIN_PCT,
        "LONG_HEADROOM_LOOKBACK_1H": LONG_HEADROOM_LOOKBACK_1H,
        "BASE_MAX_BODY_PCT": BASE_MAX_BODY_PCT,
        "PUMP_MIN_PCT": PUMP_MIN_PCT,
        "TP1_RR": TP1_RR,
        "TP2_RR_MIN": TP2_RR_MIN,
        "TP2_RR_MAX": TP2_RR_MAX,
        "SHORT_TRIGGER_VOL_MULT": SHORT_TRIGGER_VOL_MULT,
        "SHORT_SUPPORT_NEAR_PCT": SHORT_SUPPORT_NEAR_PCT,
    }

    trade["analytics"] = {
        "tp1_hit": False,
        "tp2_hit": False,
        "max_favorable": 0.0,
        "max_adverse": 0.0,
        "time_to_tp1_sec": None,
        "time_to_tp2_sec": None,
    }
    return trade

def enrich_short_support_feature(trade: Dict[str, Any], df_1h: pd.DataFrame):
    entry = float(trade["entry"])
    dist = calc_support_distance_pct_1h(df_1h, entry, SHORT_SUPPORT_LOOKBACK_1H)
    trade.setdefault("features", {})
    trade["features"]["distance_to_1h_support_pct"] = round(dist, 3)
    return trade

# ======================================================
# TRADE LIFECYCLE MESSAGING
# ======================================================

def add_trade_event(trade: Dict[str, Any], event_type: str, message: str):
    trade.setdefault("updates", [])
    trade["updates"].append({
        "ts": utc_ts(),
        "type": event_type,
        "message": message,
    })

def send_trade_update(trade: Dict[str, Any], lines: List[str], title: str = "Update"):
    msg = (
        f"🔔 {title}: {trade['symbol']} {trade['direction']}\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        + "\n".join(lines)
        + f"\n\n🕐 {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        + "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

def send_signal(trade: Dict[str, Any]):
    emoji = "📈" if trade["direction"] == "LONG" else "📉"
    entry_low = min(float(trade["entry_range_low"]), float(trade["entry_range_high"]))
    entry_high = max(float(trade["entry_range_low"]), float(trade["entry_range_high"]))

    msg = (
        f"{emoji} Trade Triggered: {trade['symbol']} {trade['direction']}\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"Entry: {fmt_price(entry_low)}–{fmt_price(entry_high)}\n\n"
        f"Stop Loss: {fmt_price(float(trade['stop']))}\n\n"
        f"TP1: {fmt_price(float(trade['tp1']))}\n"
        f"TP2: {fmt_price(float(trade['tp2']))}\n"
    )

    if trade.get("tp3") is not None:
        msg += f"TP3: {fmt_price(float(trade['tp3']))}\n"

    msg += (
        f"\nRisk: {trade.get('risk_pct', RISK_PCT_TEXT)}\n"
        f"Leverage: {trade.get('leverage', LEVERAGE_TEXT)}\n"
        f"Position size: {trade.get('position_size_text', POSITION_SIZE_TEXT)}\n\n"
        f"Thesis: {trade.get('thesis', 'Structure confirmed with momentum and participation.')}\n\n"
        f"Quality: {trade['quality_score']:.1f}/10 | Risk: {trade['risk_text']} ({trade['risk_grade']})\n"
        f"🕐 {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)
    add_trade_event(trade, "TRADE_TRIGGERED", "Trade triggered and trade plan published.")
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} {trade['direction']}")

    with open_trades_lock:
        open_trades[trade["trade_id"]] = trade

def send_status(ex_name: str, symbol: str, direction: str, text: str):
    send_telegram(f"ℹ️ {symbol} {direction} ({ex_name.upper()}): {text}")

# ======================================================
# PERFORMANCE ANALYSIS
# ======================================================

def analyze_performance(trades: List[Dict[str, Any]]) -> str:
    if not trades:
        return "No trades."

    total = len(trades)
    wins = sum(1 for t in trades if t["outcome"] == "WIN")
    greens = sum(1 for t in trades if t["outcome"] == "GREEN")
    losses = sum(1 for t in trades if t["outcome"] == "LOSS")
    tp1_hits = sum(1 for t in trades if t.get("tp1_hit"))

    avg_vol_win, avg_vol_loss = [], []
    avg_headroom_win, avg_headroom_loss = [], []
    avg_support_win, avg_support_loss = [], []

    for t in trades:
        f = t.get("features", {})
        if not f:
            continue
        if t["outcome"] in ("WIN", "GREEN"):
            avg_vol_win.append(f.get("vol_ratio", 0))
            avg_headroom_win.append(f.get("headroom_pct_1h", 0))
            avg_support_win.append(f.get("distance_to_1h_support_pct", 0))
        else:
            avg_vol_loss.append(f.get("vol_ratio", 0))
            avg_headroom_loss.append(f.get("headroom_pct_1h", 0))
            avg_support_loss.append(f.get("distance_to_1h_support_pct", 0))

    def avg(x): return sum(x)/len(x) if x else 0.0

    setups: Dict[str, Dict[str, int]] = {}
    for t in trades:
        st = t.get("setup_type", "UNKNOWN")
        setups.setdefault(st, {"total": 0, "green": 0, "win": 0, "loss": 0})
        setups[st]["total"] += 1
        if t["outcome"] == "WIN":
            setups[st]["win"] += 1
            setups[st]["green"] += 1
        elif t["outcome"] == "GREEN":
            setups[st]["green"] += 1
        else:
            setups[st]["loss"] += 1

    setup_lines = []
    for k, v in setups.items():
        green_rate = (v["green"] / v["total"] * 100.0) if v["total"] else 0.0
        setup_lines.append(f"- {k}: {green_rate:.1f}% TP1+ green rate ({v['green']}/{v['total']})")

    msg = (
        f"📊 PERFORMANCE ANALYSIS\n\n"
        f"Trades: {total}\n"
        f"Wins: {wins}\n"
        f"Greens: {greens}\n"
        f"Losses: {losses}\n\n"
        f"TP1 Hit Rate: {tp1_hits/total*100:.1f}%\n"
        f"TP2 Win Rate: {wins/total*100:.1f}%\n\n"
        f"--- WHAT WORKED ---\n"
        f"Winning avg vol ratio: {avg(avg_vol_win):.2f}\n"
        f"Winning avg headroom: {avg(avg_headroom_win):.2f}%\n"
        f"Winning avg support distance: {avg(avg_support_win):.2f}%\n\n"
        f"--- WHAT FAILED ---\n"
        f"Losing avg vol ratio: {avg(avg_vol_loss):.2f}\n"
        f"Losing avg headroom: {avg(avg_headroom_loss):.2f}%\n"
        f"Losing avg support distance: {avg(avg_support_loss):.2f}%\n\n"
        f"--- SETUPS ---\n"
        + ("\n".join(setup_lines) if setup_lines else "No setup breakdown.")
    )
    return msg

def suggest_optimizations(trades: List[Dict[str, Any]]) -> str:
    if len(trades) < 12:
        return "🛠 OPTIMIZATION SUGGESTIONS\n\nNot enough closed trades yet for tuning suggestions."

    longs = [t for t in trades if t.get("direction") == "LONG"]
    shorts = [t for t in trades if t.get("direction") == "SHORT"]

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else 0.0

    suggestions = []

    if longs:
        long_wins = [t for t in longs if t.get("outcome") in ("WIN", "GREEN")]
        long_losses = [t for t in longs if t.get("outcome") == "LOSS"]

        win_vol = avg([t.get("features", {}).get("vol_ratio") for t in long_wins])
        loss_vol = avg([t.get("features", {}).get("vol_ratio") for t in long_losses])

        win_headroom = avg([t.get("features", {}).get("headroom_pct_1h") for t in long_wins])
        loss_headroom = avg([t.get("features", {}).get("headroom_pct_1h") for t in long_losses])

        win_atr = avg([t.get("features", {}).get("atr_pct") for t in long_wins])
        loss_atr = avg([t.get("features", {}).get("atr_pct") for t in long_losses])

        long_tp1_rate = sum(1 for t in longs if t.get("tp1_hit")) / len(longs) if longs else 0.0

        if loss_vol and win_vol > loss_vol + 0.12:
            suggestions.append(
                f"LONGS: raise LOW_VOL_MULT slightly. Winners had stronger volume than losers ({win_vol:.2f} vs {loss_vol:.2f})."
            )
        if loss_headroom and win_headroom > loss_headroom + 0.30:
            suggestions.append(
                f"LONGS: tighten LONG_HEADROOM_MIN_PCT. Winners had more 1H room than losers ({win_headroom:.2f}% vs {loss_headroom:.2f}%)."
            )
        if loss_atr and win_atr > loss_atr + 0.03:
            suggestions.append(
                f"LONGS: raise CHOP_ATR_PCT_MIN a bit. Winners had better 15m expansion than losers ({win_atr:.2f}% vs {loss_atr:.2f}%)."
            )
        if long_tp1_rate < 0.55:
            suggestions.append(
                "LONGS: TP1 hit rate is weak. Tighten entry quality first: stronger volume, more headroom, cleaner bases."
            )

    if shorts:
        short_wins = [t for t in shorts if t.get("outcome") in ("WIN", "GREEN")]
        short_losses = [t for t in shorts if t.get("outcome") == "LOSS"]

        win_vol = avg([t.get("features", {}).get("vol_ratio") for t in short_wins])
        loss_vol = avg([t.get("features", {}).get("vol_ratio") for t in short_losses])

        win_support = avg([t.get("features", {}).get("distance_to_1h_support_pct") for t in short_wins])
        loss_support = avg([t.get("features", {}).get("distance_to_1h_support_pct") for t in short_losses])

        win_atr = avg([t.get("features", {}).get("atr_pct") for t in short_wins])
        loss_atr = avg([t.get("features", {}).get("atr_pct") for t in short_losses])

        if loss_support and win_support > loss_support + 0.25:
            suggestions.append(
                f"SHORTS: tighten support-distance filter. Winners had more room above 1H support than losers ({win_support:.2f}% vs {loss_support:.2f}%)."
            )
        if loss_vol and win_vol > loss_vol + 0.10:
            suggestions.append(
                f"SHORTS: raise SHORT_TRIGGER_VOL_MULT slightly. Winners had better trigger participation ({win_vol:.2f} vs {loss_vol:.2f})."
            )
        if loss_atr and win_atr > loss_atr + 0.03:
            suggestions.append(
                f"SHORTS: tighten MSS/retest environment by raising CHOP_ATR_PCT_MIN slightly ({win_atr:.2f}% vs {loss_atr:.2f}%)."
            )

    mfe_losses = avg([t.get("mfe", 0) for t in trades if t.get("outcome") == "LOSS"])
    mae_losses = avg([t.get("mae", 0) for t in trades if t.get("outcome") == "LOSS"])

    if mfe_losses < 0.003:
        suggestions.append("Most losers never moved far in your favor. That usually means entry quality is the problem, not exits.")
    if mae_losses > 0.010:
        suggestions.append("Losers are moving too far against entry. Review stop placement and setup invalidation speed.")

    if not suggestions:
        suggestions.append("No strong optimization signal yet. Keep collecting more trades before changing settings.")

    return "🛠 OPTIMIZATION SUGGESTIONS\n\n" + "\n".join([f"- {s}" for s in suggestions])

# ======================================================
# TRACKER + STATS
# ======================================================

def _record_closed(trade: Dict[str, Any], outcome: str, exit_price: float):
    with stats_lock:
        closed_trades.append({
            "trade_id": trade.get("trade_id"),
            "ex": trade["ex_name"],
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "setup_type": trade.get("setup_type"),
            "entry": float(trade["entry"]),
            "stop": float(trade["stop"]),
            "tp1": float(trade["tp1"]),
            "tp2": float(trade["tp2"]),
            "exit_price": float(exit_price),
            "quality_score": trade.get("quality_score"),
            "risk_grade": trade.get("risk_grade"),
            "features": trade.get("features", {}),
            "config": trade.get("config_snapshot", {}),
            "tp1_hit": bool(trade.get("analytics", {}).get("tp1_hit", False)),
            "tp2_hit": bool(trade.get("analytics", {}).get("tp2_hit", False)),
            "mfe": float(trade.get("analytics", {}).get("max_favorable", 0.0)),
            "mae": float(trade.get("analytics", {}).get("max_adverse", 0.0)),
            "outcome": outcome,
            "created_ts": trade.get("created_ts"),
            "closed_ts": utc_ts(),
            "updates": trade.get("updates", []),
        })

        if len(closed_trades) % STATS_BATCH_SIZE == 0:
            last_n = closed_trades[-STATS_BATCH_SIZE:]
            send_telegram(analyze_performance(last_n))
            send_telegram(suggest_optimizations(last_n))

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

                entry = float(t["entry"])
                stop = float(t["stop"])
                tp1 = float(t["tp1"])
                tp2 = float(t["tp2"])
                direction = t["direction"]

                # Track MFE / MAE
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

                # Stop hit
                if direction == "LONG" and px <= stop:
                    add_trade_event(t, "FULLY_CLOSED", "Trade stopped.")
                    send_trade_update(
                        t,
                        [
                            "Remaining position closed at stop.",
                            "Trade finished green after TP1 partial." if t.get("tp1_partial_taken") else "Trade closed at loss.",
                        ],
                        title="Trade Closed"
                    )
                    apply_stop_penalty(t["ex_name"], t["symbol"], "LONG")
                    outcome = "GREEN" if t.get("tp1_partial_taken") else "LOSS"
                    _record_closed(t, outcome, px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "SHORT" and px >= stop:
                    add_trade_event(t, "FULLY_CLOSED", "Trade stopped.")
                    send_trade_update(
                        t,
                        [
                            "Remaining position closed at stop.",
                            "Trade finished green after TP1 partial." if t.get("tp1_partial_taken") else "Trade closed at loss.",
                        ],
                        title="Trade Closed"
                    )
                    apply_stop_penalty(t["ex_name"], t["symbol"], "SHORT")
                    outcome = "GREEN" if t.get("tp1_partial_taken") else "LOSS"
                    _record_closed(t, outcome, px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # TP1
                if not t.get("tp1_hit", False):
                    if direction == "LONG" and px >= tp1:
                        add_trade_event(t, "PARTIAL_TAKEN", "Took partial at TP1.")
                        with open_trades_lock:
                            if k in open_trades:
                                open_trades[k]["tp1_hit"] = True
                                open_trades[k]["tp1_partial_taken"] = True
                                open_trades[k]["analytics"]["tp1_hit"] = True
                                open_trades[k]["analytics"]["time_to_tp1_sec"] = utc_ts() - int(open_trades[k]["created_ts"])

                                if LONG_MOVE_SL_TO_BE_AFTER_TP1:
                                    be = entry * (1.0 + LONG_BE_BUFFER_PCT)
                                    open_trades[k]["stop"] = max(float(open_trades[k]["stop"]), be)

                        lines = [
                            "Entry filled.",
                            f"Took {int(TP1_SIZE_PCT*100)}% at TP1.",
                        ]
                        if LONG_MOVE_SL_TO_BE_AFTER_TP1:
                            add_trade_event(t, "STOP_MOVED_BE", "Stop moved to breakeven.")
                            lines.append("Stop moved to breakeven.")
                        lines.append("Remaining position open.")
                        send_trade_update(t, lines, title="Update")
                        continue

                    if direction == "SHORT" and px <= tp1:
                        add_trade_event(t, "PARTIAL_TAKEN", "Took partial at TP1.")
                        with open_trades_lock:
                            if k in open_trades:
                                open_trades[k]["tp1_hit"] = True
                                open_trades[k]["tp1_partial_taken"] = True
                                open_trades[k]["analytics"]["tp1_hit"] = True
                                open_trades[k]["analytics"]["time_to_tp1_sec"] = utc_ts() - int(open_trades[k]["created_ts"])

                                if SHORT_MOVE_SL_TO_BE_AFTER_TP1:
                                    be = entry * (1.0 + SHORT_BE_BUFFER_PCT)
                                    open_trades[k]["stop"] = min(float(open_trades[k]["stop"]), be)

                        lines = [
                            "Entry filled.",
                            f"Took {int(TP1_SIZE_PCT*100)}% at TP1.",
                        ]
                        if SHORT_MOVE_SL_TO_BE_AFTER_TP1:
                            add_trade_event(t, "STOP_MOVED_BE", "Stop moved to breakeven.")
                            lines.append("Stop moved to breakeven.")
                        lines.append("Remaining position open.")
                        send_trade_update(t, lines, title="Update")
                        continue

                # TP2
                if direction == "LONG" and px >= tp2:
                    add_trade_event(t, "FULLY_CLOSED", "Trade fully closed at TP2.")
                    t["analytics"]["tp2_hit"] = True
                    t["analytics"]["time_to_tp2_sec"] = utc_ts() - int(t["created_ts"])
                    send_trade_update(
                        t,
                        [
                            "TP2 reached.",
                            "Position fully closed.",
                        ],
                        title="Trade Closed"
                    )
                    _record_closed(t, "WIN", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                if direction == "SHORT" and px <= tp2:
                    add_trade_event(t, "FULLY_CLOSED", "Trade fully closed at TP2.")
                    t["analytics"]["tp2_hit"] = True
                    t["analytics"]["time_to_tp2_sec"] = utc_ts() - int(t["created_ts"])
                    send_trade_update(
                        t,
                        [
                            "TP2 reached.",
                            "Position fully closed.",
                        ],
                        title="Trade Closed"
                    )
                    _record_closed(t, "WIN", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# MAIN SCANNER LOOP
# ======================================================

def _get_state_bucket(ex_name: str, symbol: str) -> Dict[str, Any]:
    skey = f"{ex_name}|{symbol}"
    now = utc_ts()
    if skey not in symbol_state:
        symbol_state[skey] = {"LONG": {}, "SHORT": {}, "_last_seen_ts": now}
    else:
        symbol_state[skey]["_last_seen_ts"] = now
    return symbol_state[skey]

def _reset_side(st_side: Dict[str, Any]):
    last_traded_ts = st_side.get("last_traded_div_swing2_ts")
    st_side.clear()
    if last_traded_ts is not None:
        st_side["last_traded_div_swing2_ts"] = last_traded_ts

_last_cleanup_ts = 0

def cleanup_symbol_state():
    global _last_cleanup_ts
    now = utc_ts()
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
                    # LONGS
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

                            zoneL = update_reaction_long(df_15m, zoneL)
                            stL["zone"] = zoneL
                            if not zoneL.get("reacted", False):
                                continue

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
                                            trade = enrich_trade_analytics(trade, df_15m, df_1h, "LONG_BREAKOUT_RETEST")
                                            send_signal(trade)
                                            _reset_side(stL)

                    # -------------------------
                    # SHORTS
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
                                    trade = enrich_trade_analytics(trade, df_15m, df_1h, "SHORT_DIV_MSS_RETEST")
                                    trade = enrich_short_support_feature(trade, df_1h)
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
                                    trade = enrich_trade_analytics(trade, df_15m, df_1h, "SHORT_DIV_MSS_RETEST")
                                    trade = enrich_short_support_feature(trade, df_1h)
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
    return "CRT 15m STRATEGY BOT RUNNING — lifecycle + analytics build"

if __name__ == "__main__":
    print("BOT_TOKEN_SET =", bool(BOT_TOKEN))
    print("CHAT_IDS =", CHAT_IDS)

    send_telegram("Startup test from bot")

    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
