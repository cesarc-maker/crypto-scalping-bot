import os
import time
import math
import threading
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

import ccxt
import pandas as pd
import requests
from flask import Flask
from zoneinfo import ZoneInfo

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
    return datetime.now(timezone.utc).astimezone(CT).strftime("%Y-%m-%d %H:%M:%S CT")


def utc_ts() -> int:
    return int(time.time())


# ======================================================
# CONFIG
# Keep old env/config style, replace old strategy settings
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Keep new bot group IDs fixed unless explicitly overridden
DEFAULT_CHAT_IDS = ["-1003463990210", "-1003749616502"]
CHAT_ID1 = os.getenv("CHAT_ID", "").strip()
CHAT_ID2 = os.getenv("CHAT_ID2", "").strip()
RAW_CHAT_IDS = os.getenv("CHAT_IDS", "").strip()

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

SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 15))
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 5))

EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]

PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 250))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 80))
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 3_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 20))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"
USE_TOP_MOVERS_ONLY = os.getenv("USE_TOP_MOVERS_ONLY", "1") == "1"

# Timeframes
TF_CONTEXT = os.getenv("TF_CONTEXT", "1h")
TF_CONFIRM = os.getenv("TF_CONFIRM", "15m")
TF_EXEC = os.getenv("TF_EXEC", "5m")

# Indicator settings
RSI_LEN = int(os.getenv("RSI_LEN", 14))
ATR_LEN = int(os.getenv("ATR_LEN", 14))
VOL_MA_LEN = int(os.getenv("VOL_MA_LEN", 20))
EMA_FAST = int(os.getenv("EMA_FAST", 20))
EMA_SLOW = int(os.getenv("EMA_SLOW", 50))
USE_EMA_FILTER = os.getenv("USE_EMA_FILTER", "0") == "1"

# Pivot / structure / divergence settings
PIVOT_LEFT = int(os.getenv("PIVOT_LEFT", 2))
PIVOT_RIGHT = int(os.getenv("PIVOT_RIGHT", 2))
MIN_SWING_SEPARATION = int(os.getenv("MIN_SWING_SEPARATION", 5))
MAX_SWING_LOOKBACK = int(os.getenv("MAX_SWING_LOOKBACK", 60))
MIN_PRICE_DELTA_PCT = float(os.getenv("MIN_PRICE_DELTA_PCT", 0.0015))
MIN_RSI_DELTA = float(os.getenv("MIN_RSI_DELTA", 2.0))

# Entry confirmation settings
BOS_ATR_FRACTION = float(os.getenv("BOS_ATR_FRACTION", 0.10))
CANDLE_BODY_MIN_ATR = float(os.getenv("CANDLE_BODY_MIN_ATR", 0.15))
VOLUME_MULT = float(os.getenv("VOLUME_MULT", 1.05))

# Risk management
STOP_MODE = os.getenv("STOP_MODE", "WIDER_OF_ATR_OR_STRUCTURE").strip().upper()
ATR_STOP_MULT = float(os.getenv("ATR_STOP_MULT", 1.2))
WICK_STOP_BUFFER_PCT = float(os.getenv("WICK_STOP_BUFFER_PCT", 0.0005))
MIN_RR = float(os.getenv("MIN_RR", 1.8))
MIN_RISK_PCT = float(os.getenv("MIN_RISK_PCT", 0.0012))
TP_LOOKBACK_15M = int(os.getenv("TP_LOOKBACK_15M", 80))
COOLDOWN_SEC = int(os.getenv("COOLDOWN_SEC", 1800))

# Duplicate-candle / state controls
STATE_CLEANUP_EVERY_SEC = int(os.getenv("STATE_CLEANUP_EVERY_SEC", 900))
STATE_STALE_AFTER_SEC = int(os.getenv("STATE_STALE_AFTER_SEC", 21600))

# Caches
UNIVERSE_TTL_SEC = int(os.getenv("UNIVERSE_TTL_SEC", 900))
MOVERS_TTL_SEC = int(os.getenv("MOVERS_TTL_SEC", 120))
OHLCV_5M_TTL_SEC = int(os.getenv("OHLCV_5M_TTL_SEC", 15))
OHLCV_15M_TTL_SEC = int(os.getenv("OHLCV_15M_TTL_SEC", 30))
OHLCV_1H_TTL_SEC = int(os.getenv("OHLCV_1H_TTL_SEC", 120))
OHLCV_LIMIT_5M = int(os.getenv("OHLCV_LIMIT_5M", 220))
OHLCV_LIMIT_15M = int(os.getenv("OHLCV_LIMIT_15M", 220))
OHLCV_LIMIT_1H = int(os.getenv("OHLCV_LIMIT_1H", 220))

TELEGRAM_API = "https://api.telegram.org"


# ======================================================
# STATE
# One active trade at a time, but scan many symbols
# ======================================================

active_trade: Optional[Dict[str, Any]] = None
active_trade_lock = threading.Lock()
closed_trades: List[Dict[str, Any]] = []
closed_trades_lock = threading.Lock()

cooldown_until = 0
cooldown_lock = threading.Lock()

symbol_state: Dict[str, Dict[str, Any]] = {}
_last_cleanup_ts = 0


# ======================================================
# MODELS
# ======================================================

@dataclass
class PaperTrade:
    trade_id: str
    ex_name: str
    symbol: str
    direction: str
    entry: float
    stop: float
    take_profit: float
    rr: float
    created_ts: int
    status: str
    context_1h: str
    bias_15m: str
    reason: str
    exec_candle_ts: int
    tp_anchor: float
    tp_hit: bool = False


# ======================================================
# HELPERS
# ======================================================


def norm_symbol(symbol: str) -> str:
    return symbol.split(":")[0].replace("/", "").replace("-", "").strip()


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


def get_symbol_key(ex_name: str, symbol: str) -> str:
    return f"{ex_name}|{symbol}"


def in_global_cooldown() -> bool:
    with cooldown_lock:
        return time.time() < cooldown_until


def set_global_cooldown() -> None:
    global cooldown_until
    with cooldown_lock:
        cooldown_until = time.time() + COOLDOWN_SEC


def has_active_trade() -> bool:
    with active_trade_lock:
        return active_trade is not None


def set_active_trade(trade: Dict[str, Any]) -> None:
    global active_trade
    with active_trade_lock:
        active_trade = trade


def clear_active_trade() -> None:
    global active_trade
    with active_trade_lock:
        active_trade = None


def get_active_trade() -> Optional[Dict[str, Any]]:
    with active_trade_lock:
        return dict(active_trade) if active_trade else None


# ======================================================
# TELEGRAM
# Explicit debug logging for delivery issues
# ======================================================


def send_telegram(text: str) -> None:
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing")
        return
    if not CHAT_IDS:
        log.error("No Telegram chat IDs configured")
        return

    max_len = 3800
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]

    for cid in CHAT_IDS:
        for chunk in chunks:
            try:
                url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
                payload = {"chat_id": cid, "text": chunk}
                r = requests.post(url, json=payload, timeout=15)
                response_text = r.text[:2000]
                log.info("Telegram send | chat_id=%s | status=%s", cid, r.status_code)
                log.info("Telegram response | chat_id=%s | body=%s", cid, response_text)

                if r.status_code >= 400:
                    log.error("Telegram HTTP failure | chat_id=%s | body=%s", cid, response_text)
                    continue

                try:
                    data = r.json()
                except Exception:
                    log.error("Telegram non-JSON response | chat_id=%s | body=%s", cid, response_text)
                    continue

                if not data.get("ok", False):
                    log.error(
                        "Telegram API failure | chat_id=%s | error_code=%s | description=%s",
                        cid,
                        data.get("error_code"),
                        data.get("description"),
                    )
            except Exception as e:
                log.error("Telegram exception | chat_id=%s | error=%s", cid, e)


def send_startup() -> None:
    msg = (
        "🤖 MTF REVERSAL BOT STARTED\n\n"
        "Strategy:\n"
        "• 1H = reversal context\n"
        "• 15m = structure confirmation\n"
        "• 5m = execution\n\n"
        "Runtime:\n"
        f"• Exchanges: {', '.join(EXCHANGES)}\n"
        f"• Scan interval: {SCAN_INTERVAL}s\n"
        f"• Track interval: {TRACK_INTERVAL}s\n"
        f"• One position at a time: YES\n"
        f"• Cooldown: {COOLDOWN_SEC // 60} min\n"
        f"• Top movers only: {'YES' if USE_TOP_MOVERS_ONLY else 'NO'}\n"
        f"• Pair limit: {PAIR_LIMIT}\n"
        f"• Top mover count: {TOP_MOVER_COUNT}\n"
        f"• Timeframes: {TF_CONTEXT}, {TF_CONFIRM}, {TF_EXEC}\n"
        f"• Started: {ct_time_str()}\n\n"
        "⚠️ Paper trade / info only. No execution."
    )
    send_telegram(msg)


# ======================================================
# CACHE
# ======================================================


class TTLCache:
    def __init__(self):
        self._store: Dict[Any, Tuple[Any, float]] = {}

    def get(self, key):
        item = self._store.get(key)
        if not item:
            return None
        value, exp = item
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
# UNIVERSE / MOVERS
# ======================================================


def build_quality_universe_from_tickers(markets, tickers) -> List[str]:
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


def get_quality_universe(ex_name: str, ex) -> List[str]:
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


def detect_top_movers_from_tickers(ex_name: str, ex) -> List[str]:
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
# INDICATORS
# ======================================================


def _rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def add_common_indicators(df: pd.DataFrame) -> pd.DataFrame:
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()
    df["rsi"] = _rsi(df["close"], RSI_LEN)
    df["vol_ma"] = df["volume"].rolling(VOL_MA_LEN).mean()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    return df


def add_pivots(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["pivot_high"] = False
    df["pivot_low"] = False
    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    for i in range(left, len(df) - right):
        hi = float(df["high"].iloc[i])
        lo = float(df["low"].iloc[i])

        prev_highs = df["high"].iloc[i - left:i]
        next_highs = df["high"].iloc[i + 1:i + right + 1]
        prev_lows = df["low"].iloc[i - left:i]
        next_lows = df["low"].iloc[i + 1:i + right + 1]

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
        if not data:
            return None
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        df = add_common_indicators(df)
        df = add_pivots(df)
        ohlcv_cache.set(key, df, ttl_sec)
        return df.copy()
    except Exception as e:
        log.error("Fetch error %s %s %s: %s", ex_name, symbol, tf, e)
        return None


# ======================================================
# SWING / STRUCTURE HELPERS
# ======================================================


def confirmed_df(df: pd.DataFrame) -> pd.DataFrame:
    # Use only closed candles. Fetch_ohlcv typically includes the current candle; drop last row.
    if len(df) <= 5:
        return df.iloc[0:0].copy()
    return df.iloc[:-1].copy()


def get_pivot_highs(df: pd.DataFrame) -> List[int]:
    return [int(i) for i in df.index[df["pivot_high"]].tolist()]


def get_pivot_lows(df: pd.DataFrame) -> List[int]:
    return [int(i) for i in df.index[df["pivot_low"]].tolist()]


def last_two_pivot_highs(df: pd.DataFrame) -> Optional[Tuple[int, int]]:
    highs = get_pivot_highs(df)
    if len(highs) < 2:
        return None
    return highs[-2], highs[-1]


def last_two_pivot_lows(df: pd.DataFrame) -> Optional[Tuple[int, int]]:
    lows = get_pivot_lows(df)
    if len(lows) < 2:
        return None
    return lows[-2], lows[-1]


def detect_confirmed_higher_low(df: pd.DataFrame) -> bool:
    lows = last_two_pivot_lows(df)
    if not lows:
        return False
    i1, i2 = lows
    return float(df.loc[i2, "low"]) > float(df.loc[i1, "low"])


def detect_confirmed_lower_high(df: pd.DataFrame) -> bool:
    highs = last_two_pivot_highs(df)
    if not highs:
        return False
    i1, i2 = highs
    return float(df.loc[i2, "high"]) < float(df.loc[i1, "high"])


def detect_bullish_divergence(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    lows = get_pivot_lows(df)
    if len(lows) < 2:
        return None
    recent = lows[-MAX_SWING_LOOKBACK:]
    i1, i2 = recent[-2], recent[-1]
    if (i2 - i1) < MIN_SWING_SEPARATION:
        return None

    p1 = float(df.loc[i1, "low"])
    p2 = float(df.loc[i2, "low"])
    r1 = float(df.loc[i1, "rsi"])
    r2 = float(df.loc[i2, "rsi"])

    if p1 <= 0:
        return None
    price_delta = (p1 - p2) / p1
    rsi_delta = r2 - r1

    if price_delta < MIN_PRICE_DELTA_PCT:
        return None
    if rsi_delta < MIN_RSI_DELTA:
        return None

    return {
        "swing1_idx": i1,
        "swing2_idx": i2,
        "price1": p1,
        "price2": p2,
        "rsi1": r1,
        "rsi2": r2,
    }


def detect_bearish_divergence(df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    highs = get_pivot_highs(df)
    if len(highs) < 2:
        return None
    recent = highs[-MAX_SWING_LOOKBACK:]
    i1, i2 = recent[-2], recent[-1]
    if (i2 - i1) < MIN_SWING_SEPARATION:
        return None

    p1 = float(df.loc[i1, "high"])
    p2 = float(df.loc[i2, "high"])
    r1 = float(df.loc[i1, "rsi"])
    r2 = float(df.loc[i2, "rsi"])

    if p1 <= 0:
        return None
    price_delta = (p2 - p1) / p1
    rsi_delta = r1 - r2

    if price_delta < MIN_PRICE_DELTA_PCT:
        return None
    if rsi_delta < MIN_RSI_DELTA:
        return None

    return {
        "swing1_idx": i1,
        "swing2_idx": i2,
        "price1": p1,
        "price2": p2,
        "rsi1": r1,
        "rsi2": r2,
    }


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
    highs = get_pivot_highs(df_15m)
    lows = get_pivot_lows(df_15m)
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


def get_last_confirmed_pivot_high(df: pd.DataFrame) -> Optional[Tuple[int, float]]:
    highs = get_pivot_highs(df)
    if not highs:
        return None
    idx = highs[-1]
    return idx, float(df.loc[idx, "high"])


def get_last_confirmed_pivot_low(df: pd.DataFrame) -> Optional[Tuple[int, float]]:
    lows = get_pivot_lows(df)
    if not lows:
        return None
    idx = lows[-1]
    return idx, float(df.loc[idx, "low"])


def bullish_candle_confirmation(row: pd.Series) -> bool:
    atr = float(row["atr"]) if not pd.isna(row["atr"]) else 0.0
    body = abs(float(row["close"]) - float(row["open"]))
    return float(row["close"]) > float(row["open"]) and atr > 0 and body >= CANDLE_BODY_MIN_ATR * atr


def bearish_candle_confirmation(row: pd.Series) -> bool:
    atr = float(row["atr"]) if not pd.isna(row["atr"]) else 0.0
    body = abs(float(row["close"]) - float(row["open"]))
    return float(row["close"]) < float(row["open"]) and atr > 0 and body >= CANDLE_BODY_MIN_ATR * atr


def volume_above_average(row: pd.Series) -> bool:
    if pd.isna(row["vol_ma"]) or float(row["vol_ma"]) <= 0:
        return False
    return float(row["volume"]) >= float(row["vol_ma"]) * VOLUME_MULT


def ema_filter_ok(row: pd.Series, side: str) -> bool:
    if not USE_EMA_FILTER:
        return True
    if pd.isna(row["ema_fast"]) or pd.isna(row["ema_slow"]):
        return False
    if side == "LONG":
        return float(row["ema_fast"]) > float(row["ema_slow"]) and float(row["close"]) > float(row["ema_fast"])
    return float(row["ema_fast"]) < float(row["ema_slow"]) and float(row["close"]) < float(row["ema_fast"])


def broke_above_pivot_high(df_5m: pd.DataFrame) -> Optional[Tuple[float, int]]:
    piv = get_last_confirmed_pivot_high(df_5m.iloc[:-1])
    if not piv:
        return None
    _, pivot_high = piv
    row = df_5m.iloc[-1]
    atr = float(row["atr"]) if not pd.isna(row["atr"]) else 0.0
    thresh = pivot_high + (atr * BOS_ATR_FRACTION)
    if float(row["close"]) > thresh:
        return pivot_high, int(row["ts"])
    return None


def broke_below_pivot_low(df_5m: pd.DataFrame) -> Optional[Tuple[float, int]]:
    piv = get_last_confirmed_pivot_low(df_5m.iloc[:-1])
    if not piv:
        return None
    _, pivot_low = piv
    row = df_5m.iloc[-1]
    atr = float(row["atr"]) if not pd.isna(row["atr"]) else 0.0
    thresh = pivot_low - (atr * BOS_ATR_FRACTION)
    if float(row["close"]) < thresh:
        return pivot_low, int(row["ts"])
    return None


def previous_resistance_15m(df_15m: pd.DataFrame) -> Optional[float]:
    highs = get_pivot_highs(df_15m.tail(TP_LOOKBACK_15M))
    if not highs:
        return None
    idx = highs[-1]
    return float(df_15m.loc[idx, "high"])


def previous_support_15m(df_15m: pd.DataFrame) -> Optional[float]:
    lows = get_pivot_lows(df_15m.tail(TP_LOOKBACK_15M))
    if not lows:
        return None
    idx = lows[-1]
    return float(df_15m.loc[idx, "low"])


def choose_stop(entry: float, side: str, atr: float, struct_level: float) -> float:
    if side == "LONG":
        struct_stop = struct_level * (1.0 - WICK_STOP_BUFFER_PCT)
        atr_stop = entry - ATR_STOP_MULT * atr
        if STOP_MODE == "ATR":
            return atr_stop
        if STOP_MODE == "STRUCTURE":
            return struct_stop
        return min(struct_stop, atr_stop)

    struct_stop = struct_level * (1.0 + WICK_STOP_BUFFER_PCT)
    atr_stop = entry + ATR_STOP_MULT * atr
    if STOP_MODE == "ATR":
        return atr_stop
    if STOP_MODE == "STRUCTURE":
        return struct_stop
    return max(struct_stop, atr_stop)


def build_trade(ex_name: str, symbol: str, side: str, df_5m: pd.DataFrame, df_15m: pd.DataFrame, context_1h: str, bias_15m: str, reason: str) -> Optional[Dict[str, Any]]:
    row = df_5m.iloc[-1]
    entry = float(row["close"])
    atr = float(row["atr"]) if not pd.isna(row["atr"]) else 0.0
    if atr <= 0:
        return None

    if side == "LONG":
        struct_pivot = get_last_confirmed_pivot_low(df_5m.iloc[:-1])
        if not struct_pivot:
            return None
        _, struct_low = struct_pivot
        stop = choose_stop(entry, side, atr, struct_low)
        target = previous_resistance_15m(df_15m.iloc[:-1])
        if target is None or target <= entry:
            return None
        risk = entry - stop
        reward = target - entry
    else:
        struct_pivot = get_last_confirmed_pivot_high(df_5m.iloc[:-1])
        if not struct_pivot:
            return None
        _, struct_high = struct_pivot
        stop = choose_stop(entry, side, atr, struct_high)
        target = previous_support_15m(df_15m.iloc[:-1])
        if target is None or target >= entry:
            return None
        risk = stop - entry
        reward = entry - target

    if risk <= 0:
        return None
    if (risk / entry) < MIN_RISK_PCT:
        return None

    rr = reward / risk
    if rr < MIN_RR:
        return None

    trade = PaperTrade(
        trade_id=make_trade_id(ex_name, symbol, side),
        ex_name=ex_name,
        symbol=symbol,
        direction=side,
        entry=entry,
        stop=stop,
        take_profit=target,
        rr=round(rr, 2),
        created_ts=utc_ts(),
        status="ACTIVE",
        context_1h=context_1h,
        bias_15m=bias_15m,
        reason=reason,
        exec_candle_ts=int(row["ts"]),
        tp_anchor=target,
    )
    return asdict(trade)


def evaluate_symbol_setup(ex_name: str, symbol: str, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Optional[Dict[str, Any]]:
    ctx_1h = get_1h_context(df_1h)
    bias_15m = get_15m_bias(df_15m)
    row = df_5m.iloc[-1]

    bull_div = detect_bullish_divergence(df_5m)
    bear_div = detect_bearish_divergence(df_5m)
    bos_up = broke_above_pivot_high(df_5m)
    bos_down = broke_below_pivot_low(df_5m)
    vol_ok = volume_above_average(row)

    if ctx_1h == "bullish" and bias_15m == "bullish":
        if bull_div and bos_up and bullish_candle_confirmation(row) and vol_ok and ema_filter_ok(row, "LONG"):
            return build_trade(
                ex_name,
                symbol,
                "LONG",
                df_5m,
                df_15m,
                ctx_1h,
                bias_15m,
                "5m bullish divergence + BOS above pivot high + bullish candle + volume above average",
            )

    if ctx_1h == "bearish" and bias_15m == "bearish":
        if bear_div and bos_down and bearish_candle_confirmation(row) and vol_ok and ema_filter_ok(row, "SHORT"):
            return build_trade(
                ex_name,
                symbol,
                "SHORT",
                df_5m,
                df_15m,
                ctx_1h,
                bias_15m,
                "5m bearish divergence + BOS below pivot low + bearish candle + volume above average",
            )

    return None


def score_trade_candidate(trade: Dict[str, Any], df_5m: pd.DataFrame) -> float:
    row = df_5m.iloc[-1]
    vol_ratio = 0.0
    if not pd.isna(row["vol_ma"]) and float(row["vol_ma"]) > 0:
        vol_ratio = float(row["volume"]) / float(row["vol_ma"])
    score = float(trade["rr"]) + vol_ratio
    if trade["direction"] == "LONG" and trade["context_1h"] == "bullish" and trade["bias_15m"] == "bullish":
        score += 0.5
    if trade["direction"] == "SHORT" and trade["context_1h"] == "bearish" and trade["bias_15m"] == "bearish":
        score += 0.5
    return round(score, 4)


# ======================================================
# STATE CLEANUP / DUPLICATE CANDLE CONTROL
# ======================================================


def get_state_bucket(ex_name: str, symbol: str) -> Dict[str, Any]:
    key = get_symbol_key(ex_name, symbol)
    now = utc_ts()
    if key not in symbol_state:
        symbol_state[key] = {
            "last_processed_5m_ts": 0,
            "last_signal_ts": 0,
            "_last_seen_ts": now,
        }
    else:
        symbol_state[key]["_last_seen_ts"] = now
    return symbol_state[key]


def cleanup_symbol_state() -> None:
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
        log.info("State cleanup removed %s stale symbol buckets", len(stale_keys))


# ======================================================
# MESSAGING / RECORDING
# ======================================================


def send_signal(trade: Dict[str, Any]) -> None:
    emoji = "📈" if trade["direction"] == "LONG" else "📉"
    msg = (
        f"{emoji} REVERSAL SETUP: {trade['symbol']} {trade['direction']}\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"Entry: {fmt_price(float(trade['entry']))}\n"
        f"Stop: {fmt_price(float(trade['stop']))}\n"
        f"TP: {fmt_price(float(trade['take_profit']))}\n"
        f"RR: {float(trade['rr']):.2f}\n\n"
        f"1H Context: {trade['context_1h']}\n"
        f"15m Bias: {trade['bias_15m']}\n"
        f"Reason: {trade['reason']}\n\n"
        f"🕐 {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        "⚠️ Paper trade / info only. No execution."
    )
    send_telegram(msg)


def send_trade_closed(trade: Dict[str, Any], result: str, exit_price: float) -> None:
    emoji = "✅" if result == "WIN" else "🛑"
    msg = (
        f"{emoji} TRADE CLOSED: {trade['symbol']} {trade['direction']}\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"Entry: {fmt_price(float(trade['entry']))}\n"
        f"Exit: {fmt_price(float(exit_price))}\n"
        f"Stop: {fmt_price(float(trade['stop']))}\n"
        f"TP: {fmt_price(float(trade['take_profit']))}\n"
        f"Result: {result}\n\n"
        f"🕐 {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        "⚠️ Paper trade / info only. No execution."
    )
    send_telegram(msg)


def record_closed_trade(trade: Dict[str, Any], result: str, exit_price: float) -> None:
    with closed_trades_lock:
        closed_trades.append(
            {
                "trade_id": trade["trade_id"],
                "ex_name": trade["ex_name"],
                "symbol": trade["symbol"],
                "direction": trade["direction"],
                "entry": trade["entry"],
                "stop": trade["stop"],
                "take_profit": trade["take_profit"],
                "exit_price": exit_price,
                "rr": trade["rr"],
                "result": result,
                "created_ts": trade["created_ts"],
                "closed_ts": utc_ts(),
                "context_1h": trade["context_1h"],
                "bias_15m": trade["bias_15m"],
                "reason": trade["reason"],
            }
        )


# ======================================================
# TRACKER LOOP
# ======================================================


def tracker_loop() -> None:
    log.info("Tracker loop started.")
    while True:
        time.sleep(TRACK_INTERVAL)
        trade = get_active_trade()
        if not trade:
            continue

        try:
            ex = get_ex_cached(trade["ex_name"])
            if not ex:
                continue
            ticker = ex.fetch_ticker(trade["symbol"])
            px = float(ticker.get("last") or ticker.get("close") or 0.0)
            if px <= 0:
                continue

            if trade["direction"] == "LONG":
                if px <= float(trade["stop"]):
                    send_trade_closed(trade, "LOSS", px)
                    record_closed_trade(trade, "LOSS", px)
                    clear_active_trade()
                    set_global_cooldown()
                    continue
                if px >= float(trade["take_profit"]):
                    send_trade_closed(trade, "WIN", px)
                    record_closed_trade(trade, "WIN", px)
                    clear_active_trade()
                    set_global_cooldown()
                    continue
            else:
                if px >= float(trade["stop"]):
                    send_trade_closed(trade, "LOSS", px)
                    record_closed_trade(trade, "LOSS", px)
                    clear_active_trade()
                    set_global_cooldown()
                    continue
                if px <= float(trade["take_profit"]):
                    send_trade_closed(trade, "WIN", px)
                    record_closed_trade(trade, "WIN", px)
                    clear_active_trade()
                    set_global_cooldown()
                    continue

        except Exception as e:
            log.error("Tracker error: %s", e)


# ======================================================
# SCANNER LOOP
# ======================================================


def scanner_loop() -> None:
    send_startup()
    log.info("Scanner loop started.")

    while True:
        try:
            cleanup_symbol_state()

            if has_active_trade() or in_global_cooldown():
                time.sleep(SCAN_INTERVAL)
                continue

            candidates: List[Tuple[float, Dict[str, Any]]] = []

            for ex_name in EXCHANGES:
                ex = get_ex_cached(ex_name)
                if not ex:
                    continue
                if not ensure_markets_loaded(ex_name, ex):
                    continue

                symbols = detect_top_movers_from_tickers(ex_name, ex) if USE_TOP_MOVERS_ONLY else get_quality_universe(ex_name, ex)

                for symbol in symbols:
                    try:
                        df_1h = get_df_cached(ex_name, ex, symbol, TF_CONTEXT, OHLCV_LIMIT_1H, OHLCV_1H_TTL_SEC)
                        df_15m = get_df_cached(ex_name, ex, symbol, TF_CONFIRM, OHLCV_LIMIT_15M, OHLCV_15M_TTL_SEC)
                        df_5m = get_df_cached(ex_name, ex, symbol, TF_EXEC, OHLCV_LIMIT_5M, OHLCV_5M_TTL_SEC)
                        if df_1h is None or df_15m is None or df_5m is None:
                            continue

                        df_1h = confirmed_df(df_1h)
                        df_15m = confirmed_df(df_15m)
                        df_5m = confirmed_df(df_5m)
                        if len(df_1h) < 80 or len(df_15m) < 80 or len(df_5m) < 100:
                            continue

                        st = get_state_bucket(ex_name, symbol)
                        last_closed_5m_ts = int(df_5m.iloc[-1]["ts"])
                        if last_closed_5m_ts <= int(st.get("last_processed_5m_ts", 0)):
                            continue
                        st["last_processed_5m_ts"] = last_closed_5m_ts

                        trade = evaluate_symbol_setup(ex_name, symbol, df_1h, df_15m, df_5m)
                        if not trade:
                            continue

                        score = score_trade_candidate(trade, df_5m)
                        candidates.append((score, trade))

                    except Exception as e:
                        log.error("Scanner error %s %s: %s", ex_name, symbol, e)

            if candidates and not has_active_trade() and not in_global_cooldown():
                candidates.sort(key=lambda x: x[0], reverse=True)
                best_score, best_trade = candidates[0]
                log.info(
                    "Best candidate | symbol=%s | side=%s | rr=%s | score=%s",
                    best_trade["symbol"],
                    best_trade["direction"],
                    best_trade["rr"],
                    best_score,
                )
                set_active_trade(best_trade)
                send_signal(best_trade)

        except Exception as e:
            log.error("Scanner loop error: %s", e)

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
