# ======================================================
# FUTURES ELITE+ MOMENTUM SIGNAL BOT (INFO ONLY)
# OKX + KUCOIN FUTURES • TOP MOVERS (ticker-first)
# 3-TF TREND • DISPLACEMENT • STRUCTURE BREAK
# PULLBACK ENTRY MODE (A vs A+) • STOP-PENALTY COOLDOWN
# R-BASED TPs (CUSTOM RATIOS) • TP/SL TRACKING • POSITION SIZING (INFO)
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

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("FUTURES_ELITE_PLUS_OKX_KUCOIN")

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Multi-chat
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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 25))        # seconds
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 15))      # seconds

# Universe size and mover selection
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 120))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 12))
MOVER_CANDIDATE_MULT = int(os.getenv("MOVER_CANDIDATE_MULT", 5))  # ticker shortlist = TOP_MOVER_COUNT * this

# Regular cooldown per exchange+symbol+direction
WINDOW = int(os.getenv("WINDOW", 1800))  # 30 min default

# Stop-hit penalty cooldown
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 7200))  # 2h default

# Exchanges: ONLY OKX + KuCoin Futures
# Override via env: EXCHANGES="okx,kucoin_futures"
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]  # hard clamp

# Timeframes
TF_EXEC = os.getenv("TF_EXEC", "5m")
TF_CONFIRM = os.getenv("TF_CONFIRM", "15m")
TF_REGIME = os.getenv("TF_REGIME", "1h")

# Trend alignment (EMA stack)
EMA_FAST = int(os.getenv("EMA_FAST", 9))
EMA_MID = int(os.getenv("EMA_MID", 20))
EMA_SLOW = int(os.getenv("EMA_SLOW", 50))

# Displacement thresholds (strict)
ATR_MULT = float(os.getenv("ATR_MULT", 2.3))      # ATR >= ATR_BASE * ATR_MULT
VOL_MULT = float(os.getenv("VOL_MULT", 2.5))      # VOL >= VOL_SMA * VOL_MULT

# ATR lengths (FIXED: baseline longer than ATR length)
ATR_LEN = int(os.getenv("ATR_LEN", 14))
ATR_BASELINE_LEN = int(os.getenv("ATR_BASELINE_LEN", 50))

# Momentum candle quality
BODY_PCT = float(os.getenv("BODY_PCT", 0.65))           # body >= BODY_PCT * range
MAX_WICK_FRAC = float(os.getenv("MAX_WICK_FRAC", 0.30)) # rejection wick cap

# Structure breakout buffer (close beyond structure)
BREAK_BUFFER = float(os.getenv("BREAK_BUFFER", 0.0012))  # 0.12%
STRUCT_LOOKBACK = int(os.getenv("STRUCT_LOOKBACK", 50))  # rolling structure lookback

# No-chop: EMA separation on confirm TF
MIN_EMA_SEP_PCT = float(os.getenv("MIN_EMA_SEP_PCT", 0.0008))  # 0.08%

# Require displacement on confirm TF too (quality boost)
REQUIRE_CONFIRM_DISPLACEMENT = os.getenv("REQUIRE_CONFIRM_DISPLACEMENT", "1") == "1"

# Quality market filter
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 30_000_000))  # 24h quote vol floor
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 25))                  # 0.25% max spread
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Risk model
STOP_ATR_MULT = float(os.getenv("STOP_ATR_MULT", 1.2))
STOP_MIN_PCT = float(os.getenv("STOP_MIN_PCT", 0.35))
STOP_MAX_PCT = float(os.getenv("STOP_MAX_PCT", 0.90))

# Leverage mapping (informational)
LEV_TIGHT = int(os.getenv("LEV_TIGHT", 60))
LEV_NORMAL = int(os.getenv("LEV_NORMAL", 30))
LEV_TIGHT_STOP_PCT = float(os.getenv("LEV_TIGHT_STOP_PCT", 0.60))  # <0.60% => tight

# TP allocations
TP_ALLOCS = [40, 40, 20]

# Custom TP ratios (R-based)
TP_RATIOS_RAW = os.getenv("TP_RATIOS", "1,2,3")
TP_RATIOS = [float(x.strip()) for x in TP_RATIOS_RAW.split(",") if x.strip()]
if len(TP_RATIOS) != 3:
    TP_RATIOS = [1.0, 2.0, 3.0]

# Position sizing (informational only)
ACCOUNT_USDT = float(os.getenv("ACCOUNT_USDT", 1000))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", 0.5))  # % of account risked per trade
MAX_NOTIONAL_USDT = float(os.getenv("MAX_NOTIONAL_USDT", 5000))
MIN_NOTIONAL_USDT = float(os.getenv("MIN_NOTIONAL_USDT", 25))

# Pullback entry mode
USE_PULLBACK_MODE = os.getenv("USE_PULLBACK_MODE", "1") == "1"
EXTENDED_FROM_EMA_FAST_PCT = float(os.getenv("EXTENDED_FROM_EMA_FAST_PCT", 0.004))  # 0.40%
PULLBACK_ENTRY_BLEND = float(os.getenv("PULLBACK_ENTRY_BLEND", 0.6))  # weight toward break level (0..1)
SHORT_PULLBACK_CAP_PCT = float(os.getenv("SHORT_PULLBACK_CAP_PCT", 0.006))  # cap pullback entry above current

# Pending expiry (FIX: avoid endless pending)
PENDING_EXPIRY_SECS = int(os.getenv("PENDING_EXPIRY_SECS", 6 * 3600))  # 6h default

# ======================================================
# STATE
# ======================================================

recent_signals = {}          # normal cooldown timestamps
penalty_cooldowns = {}       # stop penalty expiry timestamps

open_trades = {}
open_trades_lock = threading.Lock()

# ======================================================
# TELEGRAM (FIXED: POST + error checks + split long)
# ======================================================

TELEGRAM_API = "https://api.telegram.org"

def _telegram_post(cid: str, text: str):
    url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={"chat_id": cid, "text": text}, timeout=10)
        if resp.status_code != 200:
            log.error(f"Telegram send failed ({resp.status_code}) for {cid}: {resp.text[:300]}")
    except Exception as e:
        log.error(f"Telegram error for {cid}: {e}")

def send_telegram(text: str):
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing")
        return
    if not CHAT_IDS:
        log.warning("No chat IDs configured")
        return

    # Telegram message limit ~4096 chars; keep a safety margin
    MAX_LEN = 3800
    chunks = [text[i:i+MAX_LEN] for i in range(0, len(text), MAX_LEN)]
    for cid in CHAT_IDS:
        for ch in chunks:
            _telegram_post(cid, ch)

def send_startup():
    r1, r2, r3 = TP_RATIOS
    msg = (
        "🚀 FUTURES ELITE+ MOMENTUM BOT (INFO ONLY)\n\n"
        "Exchanges: OKX + KuCoin Futures\n"
        "Style: Trend-aligned breakout continuation\n"
        f"TFs: {TF_EXEC} / {TF_CONFIRM} / {TF_REGIME}\n"
        f"Trend: EMA{EMA_FAST}/{EMA_MID}/{EMA_SLOW}\n"
        f"Displacement: ATR({ATR_LEN}) vs baseline({ATR_BASELINE_LEN}) x{ATR_MULT} + VOLx{VOL_MULT} | Confirm: {REQUIRE_CONFIRM_DISPLACEMENT}\n"
        f"Candle: body≥{int(BODY_PCT*100)}% | wick≤{int(MAX_WICK_FRAC*100)}%\n"
        f"Structure: rolling {STRUCT_LOOKBACK} | buffer: {BREAK_BUFFER*100:.2f}%\n"
        f"Pullback mode: {USE_PULLBACK_MODE} | Extended if >{EXTENDED_FROM_EMA_FAST_PCT*100:.2f}% from EMA{EMA_FAST}\n"
        f"Cooldown: {WINDOW}s | Stop-penalty: {STOP_PENALTY_WINDOW}s\n"
        f"Stop: {STOP_ATR_MULT}×ATR | Window: {STOP_MIN_PCT:.2f}%–{STOP_MAX_PCT:.2f}%\n"
        f"TP ratios (R): 1:{r1:g} / 1:{r2:g} / 1:{r3:g} | splits {TP_ALLOCS[0]}/{TP_ALLOCS[1]}/{TP_ALLOCS[2]}\n"
        f"Position sizing (info): acct={ACCOUNT_USDT:.0f} USDT | risk={RISK_PCT_PER_TRADE:.2f}%\n"
        f"Scan: {SCAN_INTERVAL}s | Track: {TRACK_INTERVAL}s\n"
    )
    send_telegram(msg)

# ======================================================
# DUPLICATE + PENALTY COOLDOWN (FIX: include exchange)
# ======================================================

def _cd_key(ex_name: str, symbol: str, direction: str) -> str:
    return f"{ex_name}|{symbol}|{direction}"

def allow(ex_name: str, symbol: str, direction: str) -> bool:
    now = time.time()
    key = _cd_key(ex_name, symbol, direction)

    pen_exp = penalty_cooldowns.get(key)
    if pen_exp and now < pen_exp:
        return False

    last = recent_signals.get(key)
    if last is None or (now - last > WINDOW):
        recent_signals[key] = now
        return True

    return False

def apply_stop_penalty(ex_name: str, symbol: str, direction: str):
    now = time.time()
    key = _cd_key(ex_name, symbol, direction)
    penalty_cooldowns[key] = now + STOP_PENALTY_WINDOW
    recent_signals[key] = now

# ======================================================
# INDICATORS (FIXED: ATR baseline length + NaN safety later)
# ======================================================

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_mid"]  = df["close"].ewm(span=EMA_MID, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    df["vol_sma"] = df["volume"].rolling(20).mean()

    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    df["atr"] = df["tr"].rolling(ATR_LEN).mean()
    df["atr_sma"] = df["atr"].rolling(ATR_BASELINE_LEN).mean()

    df["range"] = df["high"] - df["low"]
    return df

def get_df(ex, symbol: str, tf: str):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=200)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
    except Exception as e:
        log.error(f"Fetch error {symbol} {tf}: {e}")
        return None

# ======================================================
# EXCHANGES (FIXED: enableRateLimit + only OKX/KUCOIN)
# ======================================================

def get_ex(name: str):
    try:
        name = name.strip()
        if name == "okx":
            return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        if name == "kucoin_futures":
            return ccxt.kucoinfutures({"enableRateLimit": True})
        raise ValueError(f"Unsupported exchange: {name}")
    except Exception as e:
        log.error(f"Exchange load error ({name}): {e}")
        return None

EX_INSTANCES = {}

def get_ex_cached(name: str):
    name = name.strip()
    if name in EX_INSTANCES and EX_INSTANCES[name]:
        return EX_INSTANCES[name]
    ex = get_ex(name)
    EX_INSTANCES[name] = ex
    return ex

# ======================================================
# QUALITY UNIVERSE (FIXED: metadata-based, ticker iteration)
# ======================================================

def build_quality_universe(ex) -> list:
    """
    Filter to higher quality, liquid USDT linear contracts:
    - quote == USDT
    - contract/swap/future == True
    - active (optional)
    - min 24h quote volume
    - max spread (if bid/ask exists)
    """
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

        # contract markets only (perps/futures)
        is_contract = bool(m.get("contract")) or bool(m.get("swap")) or bool(m.get("future"))
        if not is_contract:
            continue

        if m.get("quote") != "USDT":
            continue

        # Prefer linear (USDT-margined) when that field exists
        if "linear" in m and m.get("linear") is False:
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

        # Quote volume (prefer native quoteVolume)
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
# TOP MOVERS (FIXED: ticker-first, then OHLCV)
# ======================================================

def detect_top_movers(ex):
    """
    Step 1: build quality universe (metadata + tickers)
    Step 2: rank candidates cheaply using ticker % change + volume
    Step 3: confirm with OHLCV ONLY for shortlisted candidates
    """
    try:
        markets = ex.load_markets()
        tickers = ex.fetch_tickers()
    except Exception as e:
        log.error(f"Mover tickers fetch error: {e}")
        return []

    universe = build_quality_universe(ex)
    if not universe:
        return []

    # Cheap ranking: percentage + volume
    cheap_scores = []
    for s in universe:
        t = tickers.get(s)
        if not t:
            continue

        # Prefer 'percentage' if available; else approximate with change/last
        pct = t.get("percentage")
        last = t.get("last") or t.get("close")
        change = t.get("change")

        try:
            if pct is not None:
                pct_val = float(pct)
            else:
                if change is None or last is None:
                    continue
                pct_val = (float(change) / float(last)) * 100.0
        except Exception:
            continue

        qv = t.get("quoteVolume")
        if qv is None:
            # fallback (rough)
            bv = t.get("baseVolume")
            if bv is None or last is None:
                continue
            try:
                qv = float(bv) * float(last)
            except Exception:
                continue
        else:
            try:
                qv = float(qv)
            except Exception:
                continue

        # Simple blended score: move * log(volume)
        score = pct_val * 0.7 + (qv / 1_000_000.0) * 0.3
        cheap_scores.append((s, score))

    cheap_scores.sort(key=lambda x: x[1], reverse=True)

    shortlist_n = max(TOP_MOVER_COUNT * MOVER_CANDIDATE_MULT, TOP_MOVER_COUNT)
    candidates = [s for s, _ in cheap_scores[:shortlist_n]]

    # Confirm with OHLCV (your original concept, but only on candidates)
    movers = []
    for s in candidates:
        df = get_df(ex, s, TF_CONFIRM)
        if df is None or len(df) < 30:
            continue

        last_vol_sma = df["vol_sma"].iloc[-1]
        if pd.isna(last_vol_sma) or last_vol_sma <= 0:
            continue

        pct_change = (df["close"].iloc[-1] - df["close"].iloc[-4]) / df["close"].iloc[-4] * 100.0
        vol_ratio = df["volume"].iloc[-1] / (last_vol_sma + 1e-9)
        score = pct_change * 0.55 + vol_ratio * 0.45
        movers.append((s, score))

    movers.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers[:TOP_MOVER_COUNT]]

# ======================================================
# CORE STRATEGY
# ======================================================

def trend_long(df_exec, df_confirm, df_regime) -> bool:
    return (
        df_exec["ema_fast"].iloc[-1] > df_exec["ema_mid"].iloc[-1] > df_exec["ema_slow"].iloc[-1] and
        df_confirm["ema_fast"].iloc[-1] > df_confirm["ema_mid"].iloc[-1] > df_confirm["ema_slow"].iloc[-1] and
        df_regime["ema_fast"].iloc[-1] > df_regime["ema_mid"].iloc[-1] > df_regime["ema_slow"].iloc[-1]
    )

def trend_short(df_exec, df_confirm, df_regime) -> bool:
    return (
        df_exec["ema_fast"].iloc[-1] < df_exec["ema_mid"].iloc[-1] < df_exec["ema_slow"].iloc[-1] and
        df_confirm["ema_fast"].iloc[-1] < df_confirm["ema_mid"].iloc[-1] < df_confirm["ema_slow"].iloc[-1] and
        df_regime["ema_fast"].iloc[-1] < df_regime["ema_mid"].iloc[-1] < df_regime["ema_slow"].iloc[-1]
    )

def ema_separation_ok(df_confirm) -> bool:
    last = df_confirm.iloc[-1]
    price = float(last["close"])
    if price <= 0:
        return False
    if pd.isna(last["ema_fast"]) or pd.isna(last["ema_mid"]) or pd.isna(last["ema_slow"]):
        return False

    sep1 = abs(float(last["ema_fast"]) - float(last["ema_mid"])) / price
    sep2 = abs(float(last["ema_mid"]) - float(last["ema_slow"])) / price
    return (sep1 >= MIN_EMA_SEP_PCT) and (sep2 >= MIN_EMA_SEP_PCT)

def strong_displacement(df) -> bool:
    last = df.iloc[-1]
    # NaN guards
    for k in ("atr", "atr_sma", "volume", "vol_sma"):
        if k not in last or pd.isna(last[k]):
            return False

    atr = float(last["atr"])
    atr_base = float(last["atr_sma"])
    vol = float(last["volume"])
    vol_sma = float(last["vol_sma"])

    if atr <= 0 or atr_base <= 0 or vol_sma <= 0:
        return False

    return (atr >= atr_base * ATR_MULT) and (vol >= vol_sma * VOL_MULT)

def momentum_candle_ok(last, direction: str) -> bool:
    rng = float(last["range"])
    if pd.isna(rng) or rng <= 0:
        return False

    o = float(last["open"])
    c = float(last["close"])
    h = float(last["high"])
    l = float(last["low"])

    if direction == "LONG":
        body = c - o
        if body <= 0 or body < BODY_PCT * rng:
            return False
        upper_wick = h - c
        if upper_wick > MAX_WICK_FRAC * rng:
            return False
        return True

    body = o - c
    if body <= 0 or body < BODY_PCT * rng:
        return False
    lower_wick = c - l
    if lower_wick > MAX_WICK_FRAC * rng:
        return False
    return True

# ======================================================
# STRUCTURE BREAKOUT (FIXED: rolling structure, non-stale)
# ======================================================

def breakout_levels(df_exec, direction: str):
    if len(df_exec) < STRUCT_LOOKBACK + 5:
        return None

    last_close = float(df_exec["close"].iloc[-1])

    if direction == "LONG":
        prev_high = df_exec["high"].rolling(STRUCT_LOOKBACK).max().shift(1).iloc[-1]
        if pd.isna(prev_high) or prev_high <= 0:
            return None
        break_level = float(prev_high) * (1.0 + BREAK_BUFFER)
        if last_close > break_level:
            return {"break_level": float(break_level), "swing": float(prev_high)}
        return None

    prev_low = df_exec["low"].rolling(STRUCT_LOOKBACK).min().shift(1).iloc[-1]
    if pd.isna(prev_low) or prev_low <= 0:
        return None
    break_level = float(prev_low) * (1.0 - BREAK_BUFFER)
    if last_close < break_level:
        return {"break_level": float(break_level), "swing": float(prev_low)}
    return None

# ======================================================
# PULLBACK ENTRY LOGIC (A vs A+)
# ======================================================

def is_extended_from_ema_fast(last_exec) -> bool:
    price = float(last_exec["close"])
    ema_fast = float(last_exec["ema_fast"])
    if pd.isna(ema_fast) or ema_fast <= 0:
        return True
    dist = abs(price - ema_fast) / ema_fast
    return dist >= EXTENDED_FROM_EMA_FAST_PCT

def choose_entry(direction: str, last_exec, break_level: float) -> tuple:
    """
    Returns (entry_type, entry_price)
    - "NOW": A+ entry reference at close
    - "PULLBACK": A entry reference at limit pullback; tracking begins on fill
    """
    price = float(last_exec["close"])
    ema_fast = float(last_exec["ema_fast"])

    if (not USE_PULLBACK_MODE) or (not is_extended_from_ema_fast(last_exec)) or pd.isna(ema_fast) or ema_fast <= 0:
        return ("NOW", price)

    blended = (PULLBACK_ENTRY_BLEND * float(break_level)) + ((1.0 - PULLBACK_ENTRY_BLEND) * float(ema_fast))

    if direction == "LONG":
        # Pullback wants retest near break level, not above current
        entry = min(blended, price)
        entry = max(entry, float(break_level))
        return ("PULLBACK", float(entry))

    # SHORT: pullback upward after breakdown
    entry = max(blended, price)
    cap = float(price) * (1.0 + SHORT_PULLBACK_CAP_PCT)
    entry = min(entry, cap)
    # For symmetry, don't allow entry below break_level on shorts (optional but sane)
    entry = max(entry, float(break_level))
    return ("PULLBACK", float(entry))

# ======================================================
# TRACKING + REPORTING HELPERS
# ======================================================

def format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    mins = seconds // 60
    hrs = mins // 60
    mins = mins % 60
    if hrs > 0:
        return f"{hrs} hrs {mins} min"
    return f"{mins} min"

def calc_profit_pct(entry: float, price: float, direction: str, leverage: int) -> float:
    # informational estimate; ignores fees/funding/slippage
    if entry <= 0:
        return 0.0
    if direction == "LONG":
        raw = (price - entry) / entry * 100.0
    else:
        raw = (entry - price) / entry * 100.0
    return raw * leverage

def weighted_realized_pct(full_profit_pct: float, alloc_pct: int) -> float:
    return full_profit_pct * (alloc_pct / 100.0)

def build_r_based_tps(entry: float, stop: float, direction: str):
    R = abs(entry - stop)
    if R <= 0:
        return None
    r1, r2, r3 = TP_RATIOS
    if direction == "LONG":
        return [entry + r1 * R, entry + r2 * R, entry + r3 * R]
    return [entry - r1 * R, entry - r2 * R, entry - r3 * R]

def recommended_position_size(entry: float, stop: float, leverage: int):
    """
    Returns (notional_usdt, margin_usdt, risk_usdt, stop_pct)
    Informational only; ignores fees/funding/slippage.
    """
    stop_dist = abs(entry - stop)
    if entry <= 0 or stop_dist <= 0:
        return None

    stop_pct = (stop_dist / entry) * 100.0
    risk_usdt = ACCOUNT_USDT * (RISK_PCT_PER_TRADE / 100.0)

    notional = risk_usdt * (entry / stop_dist)
    notional = max(MIN_NOTIONAL_USDT, min(notional, MAX_NOTIONAL_USDT))

    margin = notional / max(leverage, 1)
    return float(notional), float(margin), float(risk_usdt), float(stop_pct)

def expected_tp_label(entry_type: str, ema_sep_ok_flag: bool, confirm_disp_ok_flag: bool):
    if entry_type == "PULLBACK":
        return "TP1 most likely (TP2 possible)"
    if ema_sep_ok_flag and confirm_disp_ok_flag:
        return "TP2 most likely (TP3 runner possible)"
    return "TP1 most likely (TP2 possible)"

# ======================================================
# SIGNAL BUILDER + REGISTER FOR TRACKING
# ======================================================

def build_trade(ex_name: str, symbol: str, direction: str, entry_price: float, atr: float,
                entry_type: str, break_level: float, ema_sep_ok_flag: bool, confirm_disp_ok_flag: bool):
    stop = entry_price - STOP_ATR_MULT * atr if direction == "LONG" else entry_price + STOP_ATR_MULT * atr
    stop_pct = abs(entry_price - stop) / entry_price * 100.0 if entry_price > 0 else 999.0

    # enforce stop window
    if stop_pct < STOP_MIN_PCT or stop_pct > STOP_MAX_PCT:
        return None

    leverage = LEV_TIGHT if stop_pct < LEV_TIGHT_STOP_PCT else LEV_NORMAL
    risk = "LOW" if leverage >= 50 else "MEDIUM"

    tps = build_r_based_tps(entry_price, stop, direction)
    if not tps:
        return None

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": direction,
        "entry": float(entry_price),
        "break_level": float(break_level),
        "stop": float(stop),
        "tps": [float(tps[0]), float(tps[1]), float(tps[2])],
        "tp_allocs": TP_ALLOCS[:],
        "tp_hits": [False, False, False],
        "leverage": int(leverage),
        "risk": risk,
        "entry_type": entry_type,  # NOW or PULLBACK
        "status": "ACTIVE" if entry_type == "NOW" else "PENDING",
        "created_ts": now,
        "start_ts": now if entry_type == "NOW" else None,
        "filled_ts": now if entry_type == "NOW" else None,
        "realized_pct": 0.0,
        "ema_sep_ok": bool(ema_sep_ok_flag),
        "confirm_disp_ok": bool(confirm_disp_ok_flag),
    }

def send_signal(trade: dict):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    direction = trade["direction"]
    entry_type = trade["entry_type"]
    status = trade["status"]

    header = "📌 FUTURES LIMIT " + direction + (" (A+) ENTER NOW" if entry_type == "NOW" else " (A) PULLBACK LIMIT")

    pos = recommended_position_size(trade["entry"], trade["stop"], trade["leverage"])
    if pos:
        notional, margin, risk_usdt, stop_pct = pos
    else:
        notional, margin, risk_usdt, stop_pct = 0.0, 0.0, 0.0, 0.0

    tp1, tp2, tp3 = trade["tps"]
    ret1 = calc_profit_pct(trade["entry"], tp1, direction, trade["leverage"])
    ret2 = calc_profit_pct(trade["entry"], tp2, direction, trade["leverage"])
    ret3 = calc_profit_pct(trade["entry"], tp3, direction, trade["leverage"])

    r1, r2, r3 = TP_RATIOS
    rr_text = f"RR targets: 1:{r1:g} / 1:{r2:g} / 1:{r3:g}"
    tp_expect = expected_tp_label(entry_type, trade.get("ema_sep_ok", False), trade.get("confirm_disp_ok", False))

    msg = (
        f"{header}\n\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n"
        f"Entry: {round(trade['entry'], 6)}\n"
        f"Break level: {round(trade['break_level'], 6)}\n"
        f"Stop: {round(trade['stop'], 6)} ({stop_pct:.2f}%)\n\n"
        f"TP1: {round(tp1, 6)} ({TP_ALLOCS[0]}%)\n"
        f"TP2: {round(tp2, 6)} ({TP_ALLOCS[1]}%)\n"
        f"TP3: {round(tp3, 6)} ({TP_ALLOCS[2]}%)\n\n"
        f"Leverage (info): {trade['leverage']}x\n"
        f"Risk Level: {trade['risk']}\n"
        f"{rr_text}\n"
        f"Expected TP: {tp_expect}\n"
        f"Status: {status}\n\n"
        f"Position (info): {notional:.0f} USDT notional\n"
        f"Margin (info): {margin:.1f} USDT\n"
        f"Risk (info): ~{risk_usdt:.2f} USDT (@{RISK_PCT_PER_TRADE:.2f}%)\n\n"
        f"Est Return @TP1: {ret1:.1f}% | @TP2: {ret2:.1f}% | @TP3: {ret3:.1f}%\n"
        f"Time: {ts}"
    )

    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} {direction} {entry_type}")

    # Register trade for tracking
    trade_key = f"{trade['ex_name']}|{trade['symbol']}|{direction}|{int(time.time())}"
    with open_trades_lock:
        open_trades[trade_key] = trade

# ======================================================
# TRACKER LOOP (handles PENDING fills + ACTIVE TP/SL)
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
                    t = open_trades.get(k)
                if not t:
                    continue

                ex = get_ex_cached(t["ex_name"])
                if not ex:
                    continue

                # Pending expiry (FIX)
                if t.get("status") == "PENDING":
                    created_ts = int(t.get("created_ts") or 0)
                    if created_ts and (time.time() - created_ts) > PENDING_EXPIRY_SECS:
                        send_telegram(
                            f"🟨 LIMIT EXPIRED (no fill)\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {t['direction']}\n"
                            f"Entry: {t['entry']}\n"
                            f"Age: {format_duration(int(time.time() - created_ts))}"
                        )
                        with open_trades_lock:
                            open_trades.pop(k, None)
                        continue

                ticker = ex.fetch_ticker(t["symbol"])
                last_price = float(ticker.get("last") or ticker.get("close") or 0)
                if last_price <= 0:
                    continue

                direction = t["direction"]
                entry = float(t["entry"])
                leverage = int(t["leverage"])

                # -------------------------
                # PENDING: wait for fill
                # -------------------------
                if t.get("status") == "PENDING":
                    filled = (last_price <= entry) if direction == "LONG" else (last_price >= entry)
                    if filled:
                        t["status"] = "ACTIVE"
                        t["filled_ts"] = int(time.time())
                        t["start_ts"] = int(time.time())

                        send_telegram(
                            f"🟦 LIMIT FILLED\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {direction}\n"
                            f"Entry: {entry}\n"
                            f"Fill Price: {last_price}"
                        )

                        with open_trades_lock:
                            if k in open_trades:
                                open_trades[k] = t
                    continue

                # -------------------------
                # ACTIVE: manage stop / tps
                # -------------------------
                elapsed = int(time.time() - int(t["start_ts"]))
                duration = format_duration(elapsed)

                stop = float(t["stop"])
                stop_hit = (last_price <= stop) if direction == "LONG" else (last_price >= stop)

                if stop_hit:
                    pnl = calc_profit_pct(entry, last_price, direction, leverage)
                    send_telegram(
                        f"❌ STOP HIT\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Side: {direction}\n"
                        f"Profit (full-size): {pnl:.1f}%\n"
                        f"Cumulative realized: {t.get('realized_pct', 0.0):.1f}%\n"
                        f"Time in trade: {duration}\n"
                        f"Price: {last_price}"
                    )

                    # Stop-hit penalty cooldown (FIX: include exchange)
                    apply_stop_penalty(t["ex_name"], t["symbol"], direction)

                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # TP HITS
                tps = t["tps"]
                allocs = t.get("tp_allocs", TP_ALLOCS)
                hit_any = False

                for i, tp in enumerate(tps):
                    if t["tp_hits"][i]:
                        continue

                    tp_hit = (last_price >= tp) if direction == "LONG" else (last_price <= tp)
                    if tp_hit:
                        t["tp_hits"][i] = True
                        hit_any = True

                        full_pnl_at_tp = calc_profit_pct(entry, tp, direction, leverage)
                        realized_add = weighted_realized_pct(full_pnl_at_tp, allocs[i])
                        t["realized_pct"] = float(t.get("realized_pct", 0.0)) + realized_add

                        label = "TP1" if i == 0 else ("TP2" if i == 1 else "TP3")

                        send_telegram(
                            f"✅ {label} HIT ({allocs[i]}%)\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {direction}\n"
                            f"Profit (full-size): {full_pnl_at_tp:.1f}%\n"
                            f"Realized add: {realized_add:.1f}%\n"
                            f"Cumulative realized: {t['realized_pct']:.1f}%\n"
                            f"Time in trade: {duration}\n"
                            f"Hit Price: {tp}"
                        )

                if hit_any:
                    with open_trades_lock:
                        if k in open_trades:
                            open_trades[k]["tp_hits"] = t["tp_hits"]
                            open_trades[k]["realized_pct"] = t["realized_pct"]

                if all(t["tp_hits"]):
                    send_telegram(
                        f"🏁 ALL TARGETS HIT\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Side: {direction}\n"
                        f"Cumulative realized: {t['realized_pct']:.1f}%\n"
                        f"Time in trade: {duration}"
                    )
                    with open_trades_lock:
                        open_trades.pop(k, None)

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# MAIN LOOP
# ======================================================

def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        for ex_name in EXCHANGES:
            ex = get_ex_cached(ex_name)
            if not ex:
                continue

            movers = detect_top_movers(ex)

            for symbol in movers:
                try:
                    df_exec = get_df(ex, symbol, TF_EXEC)
                    df_confirm = get_df(ex, symbol, TF_CONFIRM)
                    df_regime = get_df(ex, symbol, TF_REGIME)
                    if df_exec is None or df_confirm is None or df_regime is None:
                        continue

                    # EMA separation gate
                    ema_ok = ema_separation_ok(df_confirm)
                    if not ema_ok:
                        continue

                    # displacement gates (FIXED + NaN guarded)
                    exec_disp_ok = strong_displacement(df_exec)
                    if not exec_disp_ok:
                        continue

                    confirm_disp_ok = True
                    if REQUIRE_CONFIRM_DISPLACEMENT:
                        confirm_disp_ok = strong_displacement(df_confirm)
                        if not confirm_disp_ok:
                            continue

                    last_exec = df_exec.iloc[-1]
                    atr = float(last_exec.get("atr") or 0.0)
                    if atr <= 0 or pd.isna(atr):
                        continue

                    # LONG
                    if trend_long(df_exec, df_confirm, df_regime):
                        lv = breakout_levels(df_exec, "LONG")
                        if lv and momentum_candle_ok(last_exec, "LONG"):
                            if allow(ex_name, symbol, "LONG"):
                                entry_type, entry_price = choose_entry("LONG", last_exec, lv["break_level"])
                                trade = build_trade(
                                    ex_name, symbol, "LONG", entry_price, atr, entry_type,
                                    lv["break_level"], ema_ok, confirm_disp_ok
                                )
                                if trade:
                                    send_signal(trade)

                    # SHORT
                    if trend_short(df_exec, df_confirm, df_regime):
                        lv = breakout_levels(df_exec, "SHORT")
                        if lv and momentum_candle_ok(last_exec, "SHORT"):
                            if allow(ex_name, symbol, "SHORT"):
                                entry_type, entry_price = choose_entry("SHORT", last_exec, lv["break_level"])
                                trade = build_trade(
                                    ex_name, symbol, "SHORT", entry_price, atr, entry_type,
                                    lv["break_level"], ema_ok, confirm_disp_ok
                                )
                                if trade:
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
    return "FUTURES ELITE+ MOMENTUM BOT RUNNING (INFO ONLY) — OKX + KUCOIN FUTURES"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
