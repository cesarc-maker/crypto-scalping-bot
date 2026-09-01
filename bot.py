import os
import time
import threading
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

import ccxt
import pandas as pd
import requests
from flask import Flask
from zoneinfo import ZoneInfo

# ======================================================
# MTF REVERSAL BOT
# ARCHITECTURE STYLE: prior multi-coin bot structure
# STRATEGY: 1H context + 15m structure + 5m execution
# MODE: paper-trade / info only
#
# INCLUDED:
# - env/config setup
# - OKX + KuCoin Futures exchange handling
# - cached universe / movers / OHLCV fetching
# - scanner loop + tracker loop
# - Telegram alerts
# - Flask keepalive
# - multi-trade tracking (capped)
# - per-symbol state buckets
# - cooldowns / de-dupe
# - closed-candle processing
#
# TARGET PROFILE:
# - tuned for approximately 1-2 higher-quality signals per day
#   depending on volatility and exchange universe
# - 4H/1H are context, not hard vetoes
# - 15m accepts established OR transitional reversal structure
# - 5m divergence + BOS remain mandatory
# - secondary confirmations are quality-scored
#
# ⚠️ INFO ONLY. NOT FINANCIAL ADVICE. NO EXECUTION.
# ======================================================

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

# Keep required Telegram groups by default
DEFAULT_CHAT_IDS = ["-1003463990210", "-1003749616502"]
CHAT_ID1 = os.getenv("CHAT_ID", "").strip()
CHAT_ID2 = os.getenv("CHAT_ID2", "").strip()
RAW_CHAT_IDS = os.getenv("CHAT_IDS", "")

CHAT_IDS = set(DEFAULT_CHAT_IDS)
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

# Exchanges
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]

# Trade mode
TRADE_MODE = os.getenv("TRADE_MODE", "both").strip().lower()
if TRADE_MODE not in ("long_only", "short_only", "both"):
    TRADE_MODE = "both"

# Universe
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 260))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 60))
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 2_500_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 35))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"
USE_TOP_MOVERS_ONLY = os.getenv("USE_TOP_MOVERS_ONLY", "1") == "1"

# Timeframes
TF_EXEC = "5m"
TF_CONFIRM = "15m"
TF_CTX = "1h"
TF_REGIME = "4h"

# Indicators
RSI_LEN = int(os.getenv("RSI_LEN", 14))
ATR_LEN = int(os.getenv("ATR_LEN", 14))
VOL_MA_LEN = int(os.getenv("VOL_MA_LEN", 20))
EMA_FAST = int(os.getenv("EMA_FAST", 20))
EMA_SLOW = int(os.getenv("EMA_SLOW", 50))
USE_EMA_FILTER = os.getenv("USE_EMA_FILTER", "1") == "1"

# Pivot / divergence / structure
PIVOT_LEFT = int(os.getenv("PIVOT_LEFT", 2))
PIVOT_RIGHT = int(os.getenv("PIVOT_RIGHT", 2))
DIV_LOOKBACK = int(os.getenv("DIV_LOOKBACK", 60))
DIV_MIN_SWING_SEPARATION = int(os.getenv("DIV_MIN_SWING_SEPARATION", 3))
DIV_MIN_PRICE_DELTA_PCT = float(os.getenv("DIV_MIN_PRICE_DELTA_PCT", 0.0005))
DIV_MIN_RSI_DELTA = float(os.getenv("DIV_MIN_RSI_DELTA", 0.8))

# Execution confirmation
BOS_ATR_FRACTION = float(os.getenv("BOS_ATR_FRACTION", 0.08))
CANDLE_BODY_MIN_ATR = float(os.getenv("CANDLE_BODY_MIN_ATR", 0.08))
VOL_MULT = float(os.getenv("VOL_MULT", 0.95))

# Quality scoring
# Mandatory: 15m reversal structure + 5m divergence + 5m BOS + valid trade builder
# Secondary factors: breakout hold, exhaustion, candle, volume, EMA, HTF context
MIN_QUALITY_SCORE = int(os.getenv("MIN_QUALITY_SCORE", 3))

# Trade quality / location filters
LOCATION_LOOKBACK_15M = int(os.getenv("LOCATION_LOOKBACK_15M", 20))
MIN_DISTANCE_FROM_15M_HIGH_PCT = float(os.getenv("MIN_DISTANCE_FROM_15M_HIGH_PCT", 0.006))
MIN_DISTANCE_FROM_15M_LOW_PCT = float(os.getenv("MIN_DISTANCE_FROM_15M_LOW_PCT", 0.006))
USE_BREAKOUT_HOLD_FILTER = os.getenv("USE_BREAKOUT_HOLD_FILTER", "1") == "1"
USE_EXHAUSTION_FILTER = os.getenv("USE_EXHAUSTION_FILTER", "1") == "1"
EXHAUSTION_LOOKBACK_BARS = int(os.getenv("EXHAUSTION_LOOKBACK_BARS", 6))
EXHAUSTION_EXCLUDE_RECENT_BARS = int(os.getenv("EXHAUSTION_EXCLUDE_RECENT_BARS", 2))

# Risk management
STOP_METHOD = os.getenv("STOP_METHOD", "WIDER").strip().upper()
if STOP_METHOD not in ("ATR", "STRUCT", "WIDER"):
    STOP_METHOD = "WIDER"
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", 1.8))
WICK_STOP_BUFFER_PCT = float(os.getenv("WICK_STOP_BUFFER_PCT", 0.0015))
MIN_RISK_PCT = float(os.getenv("MIN_RISK_PCT", 0.0005))
MIN_RR = float(os.getenv("MIN_RR", 1.35))
TP_LOOKBACK_15M = int(os.getenv("TP_LOOKBACK_15M", 80))
STOP_TRIGGER_BUFFER_PCT = float(os.getenv("STOP_TRIGGER_BUFFER_PCT", 0.0005))

# Multi-trade behavior / cooldowns
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", 10))
COIN_COOLDOWN_SEC = int(os.getenv("COIN_COOLDOWN_SEC", 1200))
WINDOW = int(os.getenv("WINDOW", 900))
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 3600))

# Cache controls
UNIVERSE_TTL_SEC = int(os.getenv("UNIVERSE_TTL_SEC", 15 * 60))
MOVERS_TTL_SEC = int(os.getenv("MOVERS_TTL_SEC", 120))
OHLCV_5M_TTL_SEC = int(os.getenv("OHLCV_5M_TTL_SEC", 15))
OHLCV_15M_TTL_SEC = int(os.getenv("OHLCV_15M_TTL_SEC", 30))
OHLCV_1H_TTL_SEC = int(os.getenv("OHLCV_1H_TTL_SEC", 120))
OHLCV_4H_TTL_SEC = int(os.getenv("OHLCV_4H_TTL_SEC", 300))
OHLCV_LIMIT_5M = int(os.getenv("OHLCV_LIMIT_5M", 220))
OHLCV_LIMIT_15M = int(os.getenv("OHLCV_LIMIT_15M", 220))
OHLCV_LIMIT_1H = int(os.getenv("OHLCV_LIMIT_1H", 220))
OHLCV_LIMIT_4H = int(os.getenv("OHLCV_LIMIT_4H", 220))
USE_4H_SOFT_VETO = os.getenv("USE_4H_SOFT_VETO", "0") == "1"  # compatibility only; 4H is context-scored

# State cleanup
STATE_CLEANUP_EVERY_SEC = int(os.getenv("STATE_CLEANUP_EVERY_SEC", 15 * 60))
STATE_STALE_AFTER_SEC = int(os.getenv("STATE_STALE_AFTER_SEC", 6 * 60 * 60))

# Optional debug instrumentation
DEBUG_REJECTIONS = os.getenv("DEBUG_REJECTIONS", "1") == "1"
DEBUG_LOG_LIMIT_PER_CYCLE = int(os.getenv("DEBUG_LOG_LIMIT_PER_CYCLE", 30))

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
_debug_log_count = 0

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
    return True


def touch_coin(symbol: str) -> None:
    recent_coin_calls[norm_symbol(symbol)] = time.time()


def make_trade_id(ex_name: str, symbol: str, direction: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%m%d-%H%M")
    base = norm_symbol(symbol).replace("/", "")
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


def open_trade_count() -> int:
    with open_trades_lock:
        return len(open_trades)


def can_open_more_trades() -> bool:
    return open_trade_count() < MAX_OPEN_TRADES


def add_open_trade(trade: Dict[str, Any]) -> None:
    key = f"{trade['trade_id']}|{trade['symbol']}|{trade['direction']}|{utc_ts()}"
    with open_trades_lock:
        open_trades[key] = trade


def remove_open_trade(key: str) -> None:
    with open_trades_lock:
        open_trades.pop(key, None)


def reset_debug_counter() -> None:
    global _debug_log_count
    _debug_log_count = 0


def debug_reject(symbol: str, side: str, reason: str) -> None:
    global _debug_log_count
    if not DEBUG_REJECTIONS:
        return
    if _debug_log_count >= DEBUG_LOG_LIMIT_PER_CYCLE:
        return
    _debug_log_count += 1
    log.info("REJECT | %s | %s | %s", symbol, side, reason)


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

    max_len = 3800
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]

    for cid in CHAT_IDS:
        for ch in chunks:
            try:
                url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
                r = requests.post(url, json={"chat_id": cid, "text": ch}, timeout=10)
                log.info("Telegram send | chat_id=%s | status=%s | body=%s", cid, r.status_code, r.text[:300])
                if r.status_code >= 400:
                    log.error("Telegram HTTP %s: %s", r.status_code, r.text[:300])
            except Exception as e:
                log.error("Telegram error for %s: %s", cid, e)


def send_startup():
    lines = [
        "MTF REVERSAL BOT STARTED",
        "",
        "1H: reversal context",
        "15m: structure confirmation",
        "5m: execution trigger",
        "Multi-coin futures scanning",
        f"Max open trades: {MAX_OPEN_TRADES}",
        "",
        f"Coin cooldown: {COIN_COOLDOWN_SEC // 60} min",
        f"Universe mode: {'TOP MOVERS' if USE_TOP_MOVERS_ONLY else 'QUALITY UNIVERSE'}",
        f"Stop mode: {STOP_METHOD} | ATR x {ATR_STOP_MULT:.2f} | Wick buffer {WICK_STOP_BUFFER_PCT * 100:.2f}%",
        f"BOS ATR frac: {BOS_ATR_FRACTION:.2f} | EMA filter: {'ON' if USE_EMA_FILTER else 'OFF'}",
        f"4H regime: CONTEXT ONLY | Quality threshold: {MIN_QUALITY_SCORE}/6",
        f"Location filters: long>{MIN_DISTANCE_FROM_15M_HIGH_PCT * 100:.2f}% from highs | short>{MIN_DISTANCE_FROM_15M_LOW_PCT * 100:.2f}% from lows",
        f"Started: {ct_time_str()}",
        "",
        "Info only. Not financial advice.",
    ]
    send_telegram("\n".join(lines))


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
        return True
    return False


def touch_signal(ex_name: str, symbol: str, direction: str):
    recent_signals[_cd_key(ex_name, symbol, direction)] = time.time()
    touch_coin(symbol)


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

    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()

    rs = avg_gain / (avg_loss.replace(0, pd.NA))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()
    df["vol_sma"] = df["volume"].rolling(VOL_MA_LEN).mean()
    df["rsi"] = _rsi(df["close"], RSI_LEN)
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return df


def add_pivots(df: pd.DataFrame) -> pd.DataFrame:
    df["pivot_high"] = False
    df["pivot_low"] = False
    for i in range(PIVOT_LEFT, len(df) - PIVOT_RIGHT):
        hi = float(df["high"].iloc[i])
        lo = float(df["low"].iloc[i])
        prev_highs = df["high"].iloc[i - PIVOT_LEFT:i]
        next_highs = df["high"].iloc[i + 1:i + PIVOT_RIGHT + 1]
        prev_lows = df["low"].iloc[i - PIVOT_LEFT:i]
        next_lows = df["low"].iloc[i + 1:i + PIVOT_RIGHT + 1]
        if hi > float(prev_highs.max()) and hi > float(next_highs.max()):
            df.at[df.index[i], "pivot_high"] = True
        if lo < float(prev_lows.min()) and lo < float(next_lows.min()):
            df.at[df.index[i], "pivot_low"] = True
    return df


def get_df_cached(ex_name: str, ex, symbol: str, tf: str, limit: int, ttl_sec: int) -> Optional[pd.DataFrame]:
    key = (ex_name, symbol, tf, limit)
    hit = ohlcv_cache.get(key)
    if hit is not None:
        return hit.copy()
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        df = add_indicators(df)
        df = add_pivots(df)
        ohlcv_cache.set(key, df, ttl_sec)
        return df.copy()
    except Exception as e:
        log.error("Fetch error %s %s %s: %s", ex_name, symbol, tf, e)
        return None


def confirmed_df(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) <= 5:
        return df.iloc[0:0].copy()
    return df.iloc[:-1].copy()


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
        log.error("Exchange load error (%s): %s", name, e)
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
        log.error("load_markets failed (%s): %s", ex_name, e)
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
            spread_bps = ((float(ask) - float(bid)) / float(bid)) * 10000
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
        log.error("Universe build error (%s): %s", ex_name, e)
        return []


def detect_top_movers_from_tickers(ex_name: str, ex) -> list:
    key = ("movers", ex_name)
    hit = movers_cache.get(key)
    if hit is not None:
        return hit

    try:
        tickers = ex.fetch_tickers()
    except Exception as e:
        log.error("Tickers error (%s): %s", ex_name, e)
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
# STRATEGY HELPERS
# ======================================================


def pivot_high_idxs(df: pd.DataFrame) -> List[int]:
    return [int(i) for i in df.index[df["pivot_high"]].tolist()]


def pivot_low_idxs(df: pd.DataFrame) -> List[int]:
    return [int(i) for i in df.index[df["pivot_low"]].tolist()]


def detect_confirmed_higher_low(df: pd.DataFrame) -> bool:
    lows = pivot_low_idxs(df)
    if len(lows) < 2:
        return False
    i1, i2 = lows[-2], lows[-1]
    return float(df.loc[i2, "low"]) > float(df.loc[i1, "low"])


def detect_confirmed_lower_high(df: pd.DataFrame) -> bool:
    highs = pivot_high_idxs(df)
    if len(highs) < 2:
        return False
    i1, i2 = highs[-2], highs[-1]
    return float(df.loc[i2, "high"]) < float(df.loc[i1, "high"])


def detect_bullish_divergence(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    lows = pivot_low_idxs(df)
    if len(lows) < 2:
        return None
    recent = lows[-min(len(lows), DIV_LOOKBACK):]
    i1, i2 = recent[-2], recent[-1]
    if (i2 - i1) < DIV_MIN_SWING_SEPARATION:
        return None
    p1 = float(df.loc[i1, "low"])
    p2 = float(df.loc[i2, "low"])
    r1 = float(df.loc[i1, "rsi"])
    r2 = float(df.loc[i2, "rsi"])
    if p1 <= 0:
        return None
    price_delta_pct = (p1 - p2) / p1
    if price_delta_pct < DIV_MIN_PRICE_DELTA_PCT:
        return None
    if (r2 - r1) < DIV_MIN_RSI_DELTA:
        return None
    return {"swing1_idx": i1, "swing2_idx": i2}


def detect_bearish_divergence(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    highs = pivot_high_idxs(df)
    if len(highs) < 2:
        return None
    recent = highs[-min(len(highs), DIV_LOOKBACK):]
    i1, i2 = recent[-2], recent[-1]
    if (i2 - i1) < DIV_MIN_SWING_SEPARATION:
        return None
    p1 = float(df.loc[i1, "high"])
    p2 = float(df.loc[i2, "high"])
    r1 = float(df.loc[i1, "rsi"])
    r2 = float(df.loc[i2, "rsi"])
    if p1 <= 0:
        return None
    price_delta_pct = (p2 - p1) / p1
    if price_delta_pct < DIV_MIN_PRICE_DELTA_PCT:
        return None
    if (r1 - r2) < DIV_MIN_RSI_DELTA:
        return None
    return {"swing1_idx": i1, "swing2_idx": i2}


def get_1h_context(df_1h: pd.DataFrame) -> str:
    bull_div = detect_bullish_divergence(df_1h) is not None
    bear_div = detect_bearish_divergence(df_1h) is not None
    higher_low = detect_confirmed_higher_low(df_1h)
    lower_high = detect_confirmed_lower_high(df_1h)
    bullish = bull_div or higher_low
    bearish = bear_div or lower_high
    if bullish and bearish:
        return "neutral"
    if bullish:
        return "bullish"
    if bearish:
        return "bearish"
    return "neutral"


def get_15m_bias(df_15m: pd.DataFrame) -> str:
    highs = pivot_high_idxs(df_15m)
    lows = pivot_low_idxs(df_15m)
    if len(highs) < 2 or len(lows) < 2:
        return "neutral"
    h1, h2 = highs[-2], highs[-1]
    l1, l2 = lows[-2], lows[-1]
    hh = float(df_15m.loc[h2, "high"]) > float(df_15m.loc[h1, "high"])
    hl = float(df_15m.loc[l2, "low"]) > float(df_15m.loc[l1, "low"])
    lh = float(df_15m.loc[h2, "high"]) < float(df_15m.loc[h1, "high"])
    ll = float(df_15m.loc[l2, "low"]) < float(df_15m.loc[l1, "low"])
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "neutral"



def bullish_15m_transition(df_15m: pd.DataFrame) -> bool:
    """
    Accept an established bullish structure OR an early bullish reversal:
    - strict HH + HL
    - confirmed higher low
    - latest confirmed close breaks above the prior 15m pivot high
    """
    if len(df_15m) < 6:
        return False
    if get_15m_bias(df_15m) == "bullish":
        return True
    if detect_confirmed_higher_low(df_15m):
        return True

    prior = last_pivot_high(df_15m.iloc[:-1])
    if prior is not None:
        _, px = prior
        return float(df_15m.iloc[-1]["close"]) > px
    return False


def bearish_15m_transition(df_15m: pd.DataFrame) -> bool:
    """
    Accept an established bearish structure OR an early bearish reversal:
    - strict LH + LL
    - confirmed lower high
    - latest confirmed close breaks below the prior 15m pivot low
    """
    if len(df_15m) < 6:
        return False
    if get_15m_bias(df_15m) == "bearish":
        return True
    if detect_confirmed_lower_high(df_15m):
        return True

    prior = last_pivot_low(df_15m.iloc[:-1])
    if prior is not None:
        _, px = prior
        return float(df_15m.iloc[-1]["close"]) < px
    return False


def last_pivot_high(df: pd.DataFrame) -> Optional[Tuple[int, float]]:
    highs = pivot_high_idxs(df)
    if not highs:
        return None
    idx = highs[-1]
    return idx, float(df.loc[idx, "high"])


def last_pivot_low(df: pd.DataFrame) -> Optional[Tuple[int, float]]:
    lows = pivot_low_idxs(df)
    if not lows:
        return None
    idx = lows[-1]
    return idx, float(df.loc[idx, "low"])


def low_vol_ok(df_5m: pd.DataFrame) -> bool:
    last = df_5m.iloc[-1]
    if pd.isna(last["vol_sma"]) or float(last["vol_sma"]) <= 0:
        return False
    return float(last["volume"]) >= float(last["vol_sma"]) * VOL_MULT


def bullish_candle_confirmation(df_5m: pd.DataFrame) -> bool:
    last = df_5m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    body = abs(float(last["close"]) - float(last["open"]))
    return float(last["close"]) > float(last["open"]) and atr > 0 and body >= CANDLE_BODY_MIN_ATR * atr


def bearish_candle_confirmation(df_5m: pd.DataFrame) -> bool:
    last = df_5m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    body = abs(float(last["close"]) - float(last["open"]))
    return float(last["close"]) < float(last["open"]) and atr > 0 and body >= CANDLE_BODY_MIN_ATR * atr


def ema_filter_ok(df_5m: pd.DataFrame, side: str) -> bool:
    if not USE_EMA_FILTER:
        return True
    last = df_5m.iloc[-1]
    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]):
        return False
    if side == "LONG":
        return float(last["ema_fast"]) > float(last["ema_slow"]) and float(last["close"]) > float(last["ema_fast"])
    return float(last["ema_fast"]) < float(last["ema_slow"]) and float(last["close"]) < float(last["ema_fast"])


def broke_above_pivot_high(df_5m: pd.DataFrame) -> bool:
    piv = last_pivot_high(df_5m.iloc[:-1])
    if not piv:
        return False
    _, px = piv
    last = df_5m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    return float(last["close"]) > (px + atr * BOS_ATR_FRACTION)


def broke_below_pivot_low(df_5m: pd.DataFrame) -> bool:
    piv = last_pivot_low(df_5m.iloc[:-1])
    if not piv:
        return False
    _, px = piv
    last = df_5m.iloc[-1]
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    return float(last["close"]) < (px - atr * BOS_ATR_FRACTION)


def previous_resistance_15m(df_15m: pd.DataFrame) -> Optional[float]:
    highs = pivot_high_idxs(df_15m.tail(TP_LOOKBACK_15M))
    if not highs:
        return None
    idx = highs[-1]
    return float(df_15m.loc[idx, "high"])


def previous_support_15m(df_15m: pd.DataFrame) -> Optional[float]:
    lows = pivot_low_idxs(df_15m.tail(TP_LOOKBACK_15M))
    if not lows:
        return None
    idx = lows[-1]
    return float(df_15m.loc[idx, "low"])


def choose_stop(entry: float, side: str, atr: float, struct_level: float) -> float:
    """
    WIDER mode chooses the farther stop between structure and ATR.
    For longs, farther means lower. For shorts, farther means higher.
    """
    if side == "LONG":
        struct_stop = struct_level * (1.0 - WICK_STOP_BUFFER_PCT)
        atr_stop = entry - ATR_STOP_MULT * atr
        if STOP_METHOD == "ATR":
            return atr_stop
        if STOP_METHOD == "STRUCT":
            return struct_stop
        return min(struct_stop, atr_stop)

    struct_stop = struct_level * (1.0 + WICK_STOP_BUFFER_PCT)
    atr_stop = entry + ATR_STOP_MULT * atr
    if STOP_METHOD == "ATR":
        return atr_stop
    if STOP_METHOD == "STRUCT":
        return struct_stop
    return max(struct_stop, atr_stop)


def stop_hit_long(px: float, stop: float) -> bool:
    trigger = stop * (1.0 - STOP_TRIGGER_BUFFER_PCT)
    return px <= trigger


def stop_hit_short(px: float, stop: float) -> bool:
    trigger = stop * (1.0 + STOP_TRIGGER_BUFFER_PCT)
    return px >= trigger


def long_location_ok(df_15m: pd.DataFrame, entry: float) -> bool:
    lookback = min(len(df_15m), LOCATION_LOOKBACK_15M)
    if lookback <= 0:
        return True
    recent_high = float(df_15m["high"].iloc[-lookback:].max())
    distance_to_high = (recent_high - entry) / entry
    return distance_to_high >= MIN_DISTANCE_FROM_15M_HIGH_PCT


def short_location_ok(df_15m: pd.DataFrame, entry: float) -> bool:
    lookback = min(len(df_15m), LOCATION_LOOKBACK_15M)
    if lookback <= 0:
        return True
    recent_low = float(df_15m["low"].iloc[-lookback:].min())
    distance_to_low = (entry - recent_low) / entry
    return distance_to_low >= MIN_DISTANCE_FROM_15M_LOW_PCT


def breakout_hold_long(df_5m: pd.DataFrame) -> bool:
    if not USE_BREAKOUT_HOLD_FILTER or len(df_5m) < 4:
        return True
    piv = last_pivot_high(df_5m.iloc[:-2])
    if not piv:
        return False
    _, px = piv
    prev_close = float(df_5m["close"].iloc[-2])
    return prev_close > px


def breakout_hold_short(df_5m: pd.DataFrame) -> bool:
    if not USE_BREAKOUT_HOLD_FILTER or len(df_5m) < 4:
        return True
    piv = last_pivot_low(df_5m.iloc[:-2])
    if not piv:
        return False
    _, px = piv
    prev_close = float(df_5m["close"].iloc[-2])
    return prev_close < px


def exhaustion_filter_long(df_5m: pd.DataFrame) -> bool:
    if not USE_EXHAUSTION_FILTER:
        return True
    min_bars = EXHAUSTION_LOOKBACK_BARS + EXHAUSTION_EXCLUDE_RECENT_BARS + 2
    if len(df_5m) < min_bars:
        return True
    window_start = -EXHAUSTION_LOOKBACK_BARS
    window_end = -EXHAUSTION_EXCLUDE_RECENT_BARS
    prev_high = float(df_5m["high"].iloc[window_start:window_end].max())
    last_high = float(df_5m["high"].iloc[-1])
    return last_high > prev_high


def exhaustion_filter_short(df_5m: pd.DataFrame) -> bool:
    if not USE_EXHAUSTION_FILTER:
        return True
    min_bars = EXHAUSTION_LOOKBACK_BARS + EXHAUSTION_EXCLUDE_RECENT_BARS + 2
    if len(df_5m) < min_bars:
        return True
    window_start = -EXHAUSTION_LOOKBACK_BARS
    window_end = -EXHAUSTION_EXCLUDE_RECENT_BARS
    prev_low = float(df_5m["low"].iloc[window_start:window_end].min())
    last_low = float(df_5m["low"].iloc[-1])
    return last_low < prev_low




def raw_ema_alignment(df_5m: pd.DataFrame, side: str) -> bool:
    last = df_5m.iloc[-1]
    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_slow"]):
        return False
    if side == "LONG":
        return float(last["ema_fast"]) > float(last["ema_slow"]) and float(last["close"]) > float(last["ema_fast"])
    return float(last["ema_fast"]) < float(last["ema_slow"]) and float(last["close"]) < float(last["ema_fast"])


def quality_score(
    side: str,
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_5m: pd.DataFrame,
) -> Tuple[int, List[str]]:
    """
    Score six secondary confirmations. These do NOT replace the mandatory
    15m structure, 5m divergence, 5m BOS, and valid RR/location/stop rules.
    """
    ctx_4h = get_1h_context(df_4h)
    ctx_1h = get_1h_context(df_1h)
    checks: List[Tuple[str, bool]] = []

    if side == "LONG":
        checks = [
            ("breakout_hold", breakout_hold_long(df_5m)),
            ("exhaustion", exhaustion_filter_long(df_5m)),
            ("bull_candle", bullish_candle_confirmation(df_5m)),
            ("volume", low_vol_ok(df_5m)),
            ("ema", raw_ema_alignment(df_5m, "LONG")),
            ("htf_context", ctx_1h == "bullish" or ctx_4h == "bullish"),
        ]
    else:
        checks = [
            ("breakout_hold", breakout_hold_short(df_5m)),
            ("exhaustion", exhaustion_filter_short(df_5m)),
            ("bear_candle", bearish_candle_confirmation(df_5m)),
            ("volume", low_vol_ok(df_5m)),
            ("ema", raw_ema_alignment(df_5m, "SHORT")),
            ("htf_context", ctx_1h == "bearish" or ctx_4h == "bearish"),
        ]

    passed = [name for name, ok in checks if ok]
    return len(passed), passed


def quality_grade(score: int) -> str:
    if score >= 5:
        return "A+"
    if score == 4:
        return "A"
    if score == 3:
        return "B"
    return "C"


# ======================================================
# TRADE BUILDERS
# ======================================================


def build_trade_long(ex_name: str, symbol: str, df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    last = df_5m.iloc[-1]
    entry = float(last["close"])
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None
    if not long_location_ok(df_15m.iloc[:-1], entry):
        return None
    piv = last_pivot_low(df_5m.iloc[:-1])
    if not piv:
        return None
    _, struct_low = piv
    stop = choose_stop(entry, "LONG", atr, struct_low)
    if stop <= 0 or stop >= entry:
        return None
    risk = entry - stop
    if (risk / entry) < MIN_RISK_PCT:
        return None
    tp = previous_resistance_15m(df_15m.iloc[:-1])
    if tp is None or tp <= entry:
        return None
    rr = (tp - entry) / risk
    if rr < MIN_RR:
        return None
    return {
        "trade_id": make_trade_id(ex_name, symbol, "LONG"),
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "LONG",
        "entry": entry,
        "stop": stop,
        "tp": tp,
        "rr": round(rr, 2),
        "status": "ACTIVE",
        "created_ts": utc_ts(),
        "exec_ts": int(last["ts"]),
        "context_1h": "bullish",
        "bias_15m": "bullish",
        "reason": "bull div + BOS above pivot high + breakout hold + bullish candle + volume",
    }


def build_trade_short(ex_name: str, symbol: str, df_5m: pd.DataFrame, df_15m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    last = df_5m.iloc[-1]
    entry = float(last["close"])
    atr = float(last["atr"]) if not pd.isna(last["atr"]) else 0.0
    if atr <= 0:
        return None
    if not short_location_ok(df_15m.iloc[:-1], entry):
        return None
    piv = last_pivot_high(df_5m.iloc[:-1])
    if not piv:
        return None
    _, struct_high = piv
    stop = choose_stop(entry, "SHORT", atr, struct_high)
    if stop <= entry:
        return None
    risk = stop - entry
    if (risk / entry) < MIN_RISK_PCT:
        return None
    tp = previous_support_15m(df_15m.iloc[:-1])
    if tp is None or tp >= entry:
        return None
    rr = (entry - tp) / risk
    if rr < MIN_RR:
        return None
    return {
        "trade_id": make_trade_id(ex_name, symbol, "SHORT"),
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": "SHORT",
        "entry": entry,
        "stop": stop,
        "tp": tp,
        "rr": round(rr, 2),
        "status": "ACTIVE",
        "created_ts": utc_ts(),
        "exec_ts": int(last["ts"]),
        "context_1h": "bearish",
        "bias_15m": "bearish",
        "reason": "bear div + BOS below pivot low + breakout hold + bearish candle + volume",
    }


# ======================================================
# SETUP EVALUATORS
# ======================================================


def evaluate_long_setup(ex_name: str, symbol: str, df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    ctx_4h = get_1h_context(df_4h)
    ctx_1h = get_1h_context(df_1h)
    strict_15m = get_15m_bias(df_15m)

    # Mandatory #1: established or transitional 15m bullish structure.
    if not bullish_15m_transition(df_15m):
        debug_reject(symbol, "LONG", "15m bullish/reversal structure missing")
        return None

    # Mandatory #2: 5m bullish RSI divergence.
    if detect_bullish_divergence(df_5m) is None:
        debug_reject(symbol, "LONG", "no 5m bullish divergence")
        return None

    # Mandatory #3: 5m break of structure.
    if not broke_above_pivot_high(df_5m):
        debug_reject(symbol, "LONG", "no 5m BOS above pivot high")
        return None

    # Secondary confirmations are scored instead of acting as hard vetoes.
    score, passed = quality_score("LONG", df_4h, df_1h, df_5m)
    if score < MIN_QUALITY_SCORE:
        debug_reject(symbol, "LONG", f"quality score {score}/6 below {MIN_QUALITY_SCORE}")
        return None

    # Mandatory #4: location, stop, TP, and RR builder.
    trade = build_trade_long(ex_name, symbol, df_5m, df_15m)
    if trade is None:
        debug_reject(symbol, "LONG", "trade builder rejected (location/risk/rr/tp/stop)")
        return None

    trade["context_1h"] = ctx_1h
    trade["context_4h"] = ctx_4h
    trade["bias_15m"] = strict_15m if strict_15m != "neutral" else "bullish transition"
    trade["quality_score"] = score
    trade["quality_grade"] = quality_grade(score)
    trade["quality_factors"] = passed
    trade["reason"] = "15m reversal structure + 5m bull divergence + BOS"
    return trade


def evaluate_short_setup(ex_name: str, symbol: str, df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    ctx_4h = get_1h_context(df_4h)
    ctx_1h = get_1h_context(df_1h)
    strict_15m = get_15m_bias(df_15m)

    # Mandatory #1: established or transitional 15m bearish structure.
    if not bearish_15m_transition(df_15m):
        debug_reject(symbol, "SHORT", "15m bearish/reversal structure missing")
        return None

    # Mandatory #2: 5m bearish RSI divergence.
    if detect_bearish_divergence(df_5m) is None:
        debug_reject(symbol, "SHORT", "no 5m bearish divergence")
        return None

    # Mandatory #3: 5m break of structure.
    if not broke_below_pivot_low(df_5m):
        debug_reject(symbol, "SHORT", "no 5m BOS below pivot low")
        return None

    # Secondary confirmations are scored instead of acting as hard vetoes.
    score, passed = quality_score("SHORT", df_4h, df_1h, df_5m)
    if score < MIN_QUALITY_SCORE:
        debug_reject(symbol, "SHORT", f"quality score {score}/6 below {MIN_QUALITY_SCORE}")
        return None

    # Mandatory #4: location, stop, TP, and RR builder.
    trade = build_trade_short(ex_name, symbol, df_5m, df_15m)
    if trade is None:
        debug_reject(symbol, "SHORT", "trade builder rejected (location/risk/rr/tp/stop)")
        return None

    trade["context_1h"] = ctx_1h
    trade["context_4h"] = ctx_4h
    trade["bias_15m"] = strict_15m if strict_15m != "neutral" else "bearish transition"
    trade["quality_score"] = score
    trade["quality_grade"] = quality_grade(score)
    trade["quality_factors"] = passed
    trade["reason"] = "15m reversal structure + 5m bear divergence + BOS"
    return trade


# ======================================================
# SIGNAL MESSAGING
# ======================================================


def send_signal(trade: Dict[str, Any]):
    emoji = "📈" if trade["direction"] == "LONG" else "📉"
    msg = (
        f"{emoji} {trade['symbol']}\n"
        f"{trade['direction']} — ENTRY CONFIRMED\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"📍 ENTRY: {fmt_price(float(trade['entry']))}\n"
        f"🛑 STOP: {fmt_price(float(trade['stop']))}\n"
        f"🎯 TP: {fmt_price(float(trade['tp']))}\n"
        f"📐 RR: {float(trade['rr']):.2f}\n\n"
        f"Quality: {trade.get('quality_grade', '?')} ({trade.get('quality_score', 0)}/6)\n"
        f"4H Context: {trade.get('context_4h', 'neutral')}\n"
        f"1H Context: {trade['context_1h']}\n"
        f"15m Bias: {trade['bias_15m']}\n"
        f"Factors: {', '.join(trade.get('quality_factors', []))}\n"
        f"Reason: {trade['reason']}\n"
        f"🕐 {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        "⚠️ Not financial advice. Info only."
    )
    send_telegram(msg)
    add_open_trade(trade)
    touch_signal(trade["ex_name"], trade["symbol"], trade["direction"])
    log.info("Signal sent → %s %s %s", trade["ex_name"], trade["symbol"], trade["direction"])


def send_status(ex_name: str, symbol: str, direction: str, text: str):
    send_telegram(f"ℹ️ {symbol} {direction} ({ex_name.upper()}): {text}")


# ======================================================
# TRACKER + STATS
# ======================================================


def _record_closed(trade: Dict[str, Any], outcome: str, exit_price: float):
    with stats_lock:
        closed_trades.append({
            "trade_id": trade["trade_id"],
            "ex": trade["ex_name"],
            "symbol": trade["symbol"],
            "direction": trade["direction"],
            "outcome": outcome,
            "exit_price": float(exit_price),
            "closed_ts": utc_ts(),
        })


def tracker_loop():
    log.info("Tracker loop started.")
    while True:
        time.sleep(TRACK_INTERVAL)

        with open_trades_lock:
            keys = list(open_trades.keys())

        for key in keys:
            try:
                with open_trades_lock:
                    trade = open_trades.get(key)
                if not trade:
                    continue

                ex = get_ex_cached(trade["ex_name"])
                if not ex:
                    continue

                ticker = ex.fetch_ticker(trade["symbol"])
                px = float(ticker.get("last") or ticker.get("close") or 0.0)
                if px <= 0:
                    continue

                stop = float(trade["stop"])
                tp = float(trade["tp"])
                direction = trade["direction"]

                if direction == "LONG" and stop_hit_long(px, stop):
                    send_telegram(f"❌ SL HIT — {trade['symbol']} (LONG) ({trade['ex_name'].upper()})")
                    apply_stop_penalty(trade["ex_name"], trade["symbol"], "LONG")
                    _record_closed(trade, "LOSS", px)
                    remove_open_trade(key)
                    continue

                if direction == "SHORT" and stop_hit_short(px, stop):
                    send_telegram(f"❌ SL HIT — {trade['symbol']} (SHORT) ({trade['ex_name'].upper()})")
                    apply_stop_penalty(trade["ex_name"], trade["symbol"], "SHORT")
                    _record_closed(trade, "LOSS", px)
                    remove_open_trade(key)
                    continue

                if direction == "LONG" and px >= tp:
                    send_telegram(f"🏁 TP HIT — {trade['symbol']} (LONG) ({trade['ex_name'].upper()})")
                    _record_closed(trade, "WIN", px)
                    remove_open_trade(key)
                    continue

                if direction == "SHORT" and px <= tp:
                    send_telegram(f"🏁 TP HIT — {trade['symbol']} (SHORT) ({trade['ex_name'].upper()})")
                    _record_closed(trade, "WIN", px)
                    remove_open_trade(key)
                    continue

            except Exception as e:
                log.error("Tracker error %s: %s", key, e)


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
    st_side.clear()


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
        log.info("State cleanup: removed %s stale symbol buckets", len(stale_keys))


def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        reset_debug_counter()
        cleanup_symbol_state()

        if not can_open_more_trades():
            time.sleep(SCAN_INTERVAL)
            continue

        for ex_name in EXCHANGES:
            if not can_open_more_trades():
                break

            ex = get_ex_cached(ex_name)
            if not ex:
                continue
            if not ensure_markets_loaded(ex_name, ex):
                continue

            symbols = detect_top_movers_from_tickers(ex_name, ex) if USE_TOP_MOVERS_ONLY else get_quality_universe(ex_name, ex)

            candidates: List[Dict[str, Any]] = []

            for symbol in symbols:
                try:
                    if not can_open_more_trades():
                        break

                    df_5m = get_df_cached(ex_name, ex, symbol, "5m", limit=OHLCV_LIMIT_5M, ttl_sec=OHLCV_5M_TTL_SEC)
                    df_15m = get_df_cached(ex_name, ex, symbol, "15m", limit=OHLCV_LIMIT_15M, ttl_sec=OHLCV_15M_TTL_SEC)
                    df_1h = get_df_cached(ex_name, ex, symbol, TF_CTX, limit=OHLCV_LIMIT_1H, ttl_sec=OHLCV_1H_TTL_SEC)
                    df_4h = get_df_cached(ex_name, ex, symbol, TF_REGIME, limit=OHLCV_LIMIT_4H, ttl_sec=OHLCV_4H_TTL_SEC)
                    if df_5m is None or df_15m is None or df_1h is None or df_4h is None:
                        continue

                    df_5m = confirmed_df(df_5m)
                    df_15m = confirmed_df(df_15m)
                    df_1h = confirmed_df(df_1h)
                    df_4h = confirmed_df(df_4h)
                    if len(df_5m) < 120 or len(df_15m) < 120 or len(df_1h) < 80 or len(df_4h) < 80:
                        continue

                    st_bucket = _get_state_bucket(ex_name, symbol)
                    exec_ts = int(df_5m.iloc[-1]["ts"])

                    if TRADE_MODE in ("both", "long_only"):
                        stL = st_bucket["LONG"]
                        if int(stL.get("last_exec_ts", 0)) != exec_ts:
                            stL["last_exec_ts"] = exec_ts
                            if allow_signal(ex_name, symbol, "LONG") and allow_coin(symbol):
                                trade = evaluate_long_setup(ex_name, symbol, df_4h, df_1h, df_15m, df_5m)
                                if trade:
                                    candidates.append(trade)
                            else:
                                debug_reject(symbol, "LONG", "cooldown blocked")

                    if TRADE_MODE in ("both", "short_only"):
                        stS = st_bucket["SHORT"]
                        if int(stS.get("last_exec_ts", 0)) != exec_ts:
                            stS["last_exec_ts"] = exec_ts
                            if allow_signal(ex_name, symbol, "SHORT") and allow_coin(symbol):
                                trade = evaluate_short_setup(ex_name, symbol, df_4h, df_1h, df_15m, df_5m)
                                if trade:
                                    candidates.append(trade)
                            else:
                                debug_reject(symbol, "SHORT", "cooldown blocked")

                except Exception as e:
                    log.error("Scanner error %s %s: %s", ex_name, symbol, e)

            candidates.sort(key=lambda t: float(t["rr"]), reverse=True)

            for trade in candidates:
                if not can_open_more_trades():
                    break
                if allow_coin(trade["symbol"]) and allow_signal(trade["ex_name"], trade["symbol"], trade["direction"]):
                    send_signal(trade)

        time.sleep(SCAN_INTERVAL)


# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "MTF reversal bot running"


if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
