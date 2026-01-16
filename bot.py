# ======================================================
# FUTURES SCALP ELITE — QUICK PROFIT SIGNAL BOT (INFO ONLY)
# OKX + KUCOIN FUTURES • TOP MOVERS • 3-TF MICRO TREND
# SCALP SETUP: VWAP/EMA PULLBACK + RSI RESET + MOMENTUM RECLAIM
# TIGHT STOPS • FAST TPs • TIME-BASED EXIT • STOP-PENALTY COOLDOWN
# TP/SL TRACKING • POSITION SIZING (INFO)
# SAME STRUCTURE AS YOUR FIRST BOT (Render-ready)
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

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("FUTURES_SCALP_ELITE")

# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Multi-chat (same as your first bot)
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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 12))        # seconds
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 6))       # seconds

# Universe size and mover selection
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 140))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 16))

# Cooldowns
WINDOW = int(os.getenv("WINDOW", 900))  # 15 min default (scalp = faster recycling)
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 3600))  # 1h default

# Exchanges (OKX + KuCoin Futures)
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]  # hard clamp

# Timeframes (scalp)
TF_EXEC = os.getenv("TF_EXEC", "1m")        # entries
TF_CONFIRM = os.getenv("TF_CONFIRM", "5m")  # micro-trend
TF_REGIME = os.getenv("TF_REGIME", "15m")   # broader context

# Trend alignment (EMA stack)
EMA_FAST = int(os.getenv("EMA_FAST", 9))
EMA_MID = int(os.getenv("EMA_MID", 20))
EMA_SLOW = int(os.getenv("EMA_SLOW", 50))

# VWAP / RSI / ATR
RSI_LEN = int(os.getenv("RSI_LEN", 14))
ATR_LEN = int(os.getenv("ATR_LEN", 14))

# Scalp quality filters
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 8_000_000))  # lower than swing bot
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 18))                  # tighter spread for scalps
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# "Top movers" for scalps: focus on pairs moving NOW
MOVER_LOOKBACK_BARS = int(os.getenv("MOVER_LOOKBACK_BARS", 12))  # on TF_CONFIRM (e.g., 12 bars of 5m = 1h)
MOVER_MIN_ABS_PCT = float(os.getenv("MOVER_MIN_ABS_PCT", 0.8))    # at least ~0.8% move over lookback to be interesting

# Entry logic: pullback + reclaim
PULLBACK_MAX_DIST_EMA20_PCT = float(os.getenv("PULLBACK_MAX_DIST_EMA20_PCT", 0.004))  # 0.40% max from EMA20 for pullback area
RECLAIM_BUFFER_PCT = float(os.getenv("RECLAIM_BUFFER_PCT", 0.0006))                   # 0.06% reclaim buffer
RSI_LONG_MAX = float(os.getenv("RSI_LONG_MAX", 55))     # pullback RSI should reset <= 55 in uptrend
RSI_SHORT_MIN = float(os.getenv("RSI_SHORT_MIN", 45))   # pullback RSI should reset >= 45 in downtrend

# Momentum candle (loose; scalp needs frequency)
BODY_PCT = float(os.getenv("BODY_PCT", 0.45))
MAX_WICK_FRAC = float(os.getenv("MAX_WICK_FRAC", 0.55))

# Risk model (tight)
STOP_ATR_MULT = float(os.getenv("STOP_ATR_MULT", 0.9))  # tighter than swing bot
STOP_MIN_PCT = float(os.getenv("STOP_MIN_PCT", 0.20))
STOP_MAX_PCT = float(os.getenv("STOP_MAX_PCT", 0.60))

# Leverage mapping (informational)
LEV_TIGHT = int(os.getenv("LEV_TIGHT", 50))
LEV_NORMAL = int(os.getenv("LEV_NORMAL", 25))
LEV_TIGHT_STOP_PCT = float(os.getenv("LEV_TIGHT_STOP_PCT", 0.40))

# Fast TP model (R-based, smaller targets)
TP_ALLOCS = [50, 30, 20]
TP_RATIOS_RAW = os.getenv("TP_RATIOS", "0.6,1.0,1.4")  # scalp-style
TP_RATIOS = [float(x.strip()) for x in TP_RATIOS_RAW.split(",") if x.strip()]
if len(TP_RATIOS) != 3:
    TP_RATIOS = [0.6, 1.0, 1.4]

# Time-based exit (scalp reality)
MAX_TRADE_LIFETIME_SECS = int(os.getenv("MAX_TRADE_LIFETIME_SECS", 20 * 60))  # 20 minutes

# Position sizing (informational only)
ACCOUNT_USDT = float(os.getenv("ACCOUNT_USDT", 1000))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", 0.4))
MAX_NOTIONAL_USDT = float(os.getenv("MAX_NOTIONAL_USDT", 5000))
MIN_NOTIONAL_USDT = float(os.getenv("MIN_NOTIONAL_USDT", 25))

# Pullback entry mode (scalp-friendly: default ON)
USE_PULLBACK_MODE = os.getenv("USE_PULLBACK_MODE", "1") == "1"
PULLBACK_ENTRY_BLEND = float(os.getenv("PULLBACK_ENTRY_BLEND", 0.65))  # lean toward reclaim level
PENDING_EXPIRY_SECS = int(os.getenv("PENDING_EXPIRY_SECS", 60 * 60))    # 1h max pending

# ======================================================
# STATE
# ======================================================

recent_signals = {}
penalty_cooldowns = {}

open_trades = {}
open_trades_lock = threading.Lock()

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
    r1, r2, r3 = TP_RATIOS
    msg = (
        "⚡ FUTURES SCALP ELITE (INFO ONLY)\n\n"
        "Style: Micro-trend pullback + reclaim (scalp)\n"
        f"TFs: {TF_EXEC} / {TF_CONFIRM} / {TF_REGIME}\n"
        f"Trend: EMA{EMA_FAST}/{EMA_MID}/{EMA_SLOW}\n"
        f"Filters: spread≤{MAX_SPREAD_BPS}bps | 24h qv≥{MIN_QUOTE_VOL_USDT/1e6:.0f}M\n"
        f"Pullback: dist≤{PULLBACK_MAX_DIST_EMA20_PCT*100:.2f}% from EMA{EMA_MID} | reclaim buf {RECLAIM_BUFFER_PCT*100:.2f}%\n"
        f"RSI reset: long≤{RSI_LONG_MAX} | short≥{RSI_SHORT_MIN}\n"
        f"Stop: {STOP_ATR_MULT}×ATR | window {STOP_MIN_PCT:.2f}%–{STOP_MAX_PCT:.2f}%\n"
        f"TP ratios (R): 1:{r1:g} / 1:{r2:g} / 1:{r3:g} | splits {TP_ALLOCS[0]}/{TP_ALLOCS[1]}/{TP_ALLOCS[2]}\n"
        f"Max trade time: {MAX_TRADE_LIFETIME_SECS//60} min\n"
        f"Cooldown: {WINDOW}s | Stop-penalty: {STOP_PENALTY_WINDOW}s\n"
        f"Position sizing (info): acct={ACCOUNT_USDT:.0f} USDT | risk={RISK_PCT_PER_TRADE:.2f}%\n\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n"
        f"Scan: {SCAN_INTERVAL}s | Track: {TRACK_INTERVAL}s\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

# ======================================================
# DUPLICATE + PENALTY COOLDOWN
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
# INDICATORS
# ======================================================

def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(length).mean()
    loss = (-delta.clip(upper=0)).rolling(length).mean()
    rs = gain / (loss + 1e-12)
    return 100 - (100 / (1 + rs))

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    # EMAs
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_mid"]  = df["close"].ewm(span=EMA_MID, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # Volume baseline
    df["vol_sma"] = df["volume"].rolling(20).mean()

    # ATR
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()

    # Candle range
    df["range"] = df["high"] - df["low"]

    # RSI
    df["rsi"] = _rsi(df["close"], RSI_LEN)

    # VWAP (sessionless approximation: rolling VWAP over 50 bars on TF_EXEC / TF_CONFIRM)
    vwap_len = 50
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    df["vwap"] = pv.rolling(vwap_len).sum() / (df["volume"].rolling(vwap_len).sum() + 1e-12)

    return df

def get_df(ex, symbol: str, tf: str):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=220)
        df = pd.DataFrame(data, columns=["ts","open","high","low","close","volume"])
        return add_indicators(df)
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

EX_INSTANCES = {}

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
# TOP MOVERS (scalp-oriented)
# ======================================================

def detect_top_movers(ex):
    """
    For scalps, we want coins moving right now (intrahour) with liquidity/spread filters already applied.
    We score using TF_CONFIRM close-to-close change over MOVER_LOOKBACK_BARS + volume ratio.
    """
    movers = []
    pairs = build_quality_universe(ex)

    for s in pairs:
        df = get_df(ex, s, TF_CONFIRM)
        if df is None or len(df) < max(50, MOVER_LOOKBACK_BARS + 10):
            continue

        base = df["close"].iloc[-(MOVER_LOOKBACK_BARS + 1)]
        last = df["close"].iloc[-1]
        if base <= 0:
            continue

        pct_change = (last - base) / base * 100.0
        abs_change = abs(pct_change)

        # require some movement to be worth scalping
        if abs_change < MOVER_MIN_ABS_PCT:
            continue

        vol_ratio = float(df["volume"].iloc[-1]) / (float(df["vol_sma"].iloc[-1]) + 1e-9)
        score = abs_change * 0.65 + vol_ratio * 0.35
        movers.append((s, score))

    movers.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers[:TOP_MOVER_COUNT]]

# ======================================================
# CORE STRATEGY (Scalp)
# ======================================================

def trend_long(df_exec, df_confirm, df_regime) -> bool:
    return (
        df_confirm["ema_fast"].iloc[-1] > df_confirm["ema_mid"].iloc[-1] > df_confirm["ema_slow"].iloc[-1] and
        df_regime["ema_fast"].iloc[-1] > df_regime["ema_mid"].iloc[-1] > df_regime["ema_slow"].iloc[-1]
    )

def trend_short(df_exec, df_confirm, df_regime) -> bool:
    return (
        df_confirm["ema_fast"].iloc[-1] < df_confirm["ema_mid"].iloc[-1] < df_confirm["ema_slow"].iloc[-1] and
        df_regime["ema_fast"].iloc[-1] < df_regime["ema_mid"].iloc[-1] < df_regime["ema_slow"].iloc[-1]
    )

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
        # cap rejection wick
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

def pullback_zone_ok(last_exec, direction: str) -> bool:
    price = float(last_exec["close"])
    ema20 = float(last_exec["ema_mid"])
    if ema20 <= 0:
        return False
    dist = abs(price - ema20) / ema20
    return dist <= PULLBACK_MAX_DIST_EMA20_PCT

def reclaim_trigger(last_exec, direction: str) -> float:
    """
    Reclaim = price recovers above VWAP+buffer (long) or below VWAP-buffer (short)
    Returns reclaim_level for reference (not guaranteed fill).
    """
    vwap = float(last_exec["vwap"])
    if direction == "LONG":
        return vwap * (1.0 + RECLAIM_BUFFER_PCT)
    return vwap * (1.0 - RECLAIM_BUFFER_PCT)

def rsi_reset_ok(last_exec, direction: str) -> bool:
    rsi = float(last_exec["rsi"])
    if pd.isna(rsi):
        return False
    if direction == "LONG":
        return rsi <= RSI_LONG_MAX
    return rsi >= RSI_SHORT_MIN

def choose_entry(direction: str, last_exec, reclaim_level: float) -> tuple:
    """
    Scalp logic:
    - If pullback mode ON: set a PULLBACK limit around reclaim_level blended with EMA20
    - Otherwise: NOW at close
    """
    price = float(last_exec["close"])
    ema20 = float(last_exec["ema_mid"])
    if pd.isna(ema20) or ema20 <= 0 or not USE_PULLBACK_MODE:
        return ("NOW", price)

    blended = (PULLBACK_ENTRY_BLEND * float(reclaim_level)) + ((1.0 - PULLBACK_ENTRY_BLEND) * float(ema20))

    if direction == "LONG":
        entry = min(blended, price)
        return ("PULLBACK", float(entry))
    entry = max(blended, price)
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
    stop_dist = abs(entry - stop)
    if entry <= 0 or stop_dist <= 0:
        return None
    stop_pct = (stop_dist / entry) * 100.0
    risk_usdt = ACCOUNT_USDT * (RISK_PCT_PER_TRADE / 100.0)

    notional = risk_usdt * (entry / stop_dist)
    notional = max(MIN_NOTIONAL_USDT, min(notional, MAX_NOTIONAL_USDT))

    margin = notional / max(leverage, 1)
    return float(notional), float(margin), float(risk_usdt), float(stop_pct)

# ======================================================
# SIGNAL BUILDER + REGISTER FOR TRACKING
# ======================================================

def build_trade(ex_name: str, symbol: str, direction: str, entry_price: float, atr: float,
                entry_type: str, reclaim_level: float):
    stop = entry_price - STOP_ATR_MULT * atr if direction == "LONG" else entry_price + STOP_ATR_MULT * atr
    stop_pct = abs(entry_price - stop) / entry_price * 100.0 if entry_price > 0 else 999.0

    if stop_pct < STOP_MIN_PCT or stop_pct > STOP_MAX_PCT:
        return None

    leverage = LEV_TIGHT if stop_pct < LEV_TIGHT_STOP_PCT else LEV_NORMAL
    risk = "LOW" if leverage >= 45 else "MEDIUM"

    tps = build_r_based_tps(entry_price, stop, direction)
    if not tps:
        return None

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": direction,
        "entry": float(entry_price),
        "reclaim_level": float(reclaim_level),
        "stop": float(stop),
        "tps": [float(tps[0]), float(tps[1]), float(tps[2])],
        "tp_allocs": TP_ALLOCS[:],
        "tp_hits": [False, False, False],
        "leverage": int(leverage),
        "risk": risk,
        "entry_type": entry_type,  # NOW / PULLBACK
        "status": "ACTIVE" if entry_type == "NOW" else "PENDING",
        "created_ts": now,
        "start_ts": now if entry_type == "NOW" else None,
        "filled_ts": now if entry_type == "NOW" else None,
        "realized_pct": 0.0,
    }

def send_signal(trade: dict):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    direction = trade["direction"]
    entry_type = trade["entry_type"]
    status = trade["status"]

    header = "⚡ SCALP " + direction + (" (A+) ENTER NOW" if entry_type == "NOW" else " (A) PULLBACK LIMIT")

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

    msg = (
        f"{header}\n\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n"
        f"Entry: {round(trade['entry'], 6)}\n"
        f"Reclaim lvl: {round(trade['reclaim_level'], 6)}\n"
        f"Stop: {round(trade['stop'], 6)} ({stop_pct:.2f}%)\n\n"
        f"TP1: {round(tp1, 6)} ({TP_ALLOCS[0]}%)\n"
        f"TP2: {round(tp2, 6)} ({TP_ALLOCS[1]}%)\n"
        f"TP3: {round(tp3, 6)} ({TP_ALLOCS[2]}%)\n\n"
        f"RR targets (R): 1:{r1:g} / 1:{r2:g} / 1:{r3:g}\n"
        f"Leverage (info): {trade['leverage']}x\n"
        f"Risk Level: {trade['risk']}\n"
        f"Status: {status}\n"
        f"Max lifetime: {MAX_TRADE_LIFETIME_SECS//60} min\n\n"
        f"Position (info): {notional:.0f} USDT notional | Margin {margin:.1f} USDT\n"
        f"Risk (info): ~{risk_usdt:.2f} USDT (@{RISK_PCT_PER_TRADE:.2f}%)\n\n"
        f"Est Return @TP1: {ret1:.1f}% | @TP2: {ret2:.1f}% | @TP3: {ret3:.1f}%\n"
        f"Time: {ts}\n\n"
        "⚠️ Info only. Not financial advice."
    )

    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} {direction} {entry_type}")

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

                ticker = ex.fetch_ticker(t["symbol"])
                last_price = float(ticker.get("last") or ticker.get("close") or 0)
                if last_price <= 0:
                    continue

                direction = t["direction"]
                entry = float(t["entry"])
                leverage = int(t["leverage"])

                # -------------------------
                # PENDING: expiry + fill
                # -------------------------
                if t.get("status") == "PENDING":
                    created_ts = int(t.get("created_ts") or 0)
                    if created_ts and (time.time() - created_ts) > PENDING_EXPIRY_SECS:
                        send_telegram(
                            f"🟨 LIMIT EXPIRED (no fill)\n\n"
                            f"Pair: {t['symbol']} ({t['ex_name']})\n"
                            f"Side: {direction}\n"
                            f"Entry: {entry}\n"
                            f"Age: {format_duration(int(time.time() - created_ts))}"
                        )
                        with open_trades_lock:
                            open_trades.pop(k, None)
                        continue

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
                # ACTIVE: time exit / stop / TP
                # -------------------------
                elapsed = int(time.time() - int(t["start_ts"]))
                duration = format_duration(elapsed)

                # Time-based exit (scalp: if it doesn't move, dump it)
                if elapsed >= MAX_TRADE_LIFETIME_SECS:
                    pnl = calc_profit_pct(entry, last_price, direction, leverage)
                    send_telegram(
                        f"⏱️ TIME EXIT\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Side: {direction}\n"
                        f"Profit (full-size): {pnl:.1f}%\n"
                        f"Cumulative realized: {t.get('realized_pct', 0.0):.1f}%\n"
                        f"Time in trade: {duration}\n"
                        f"Price: {last_price}"
                    )
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

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
                    apply_stop_penalty(t["ex_name"], t["symbol"], direction)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

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
            ex_name = ex_name.strip()
            if not ex_name:
                continue

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

                    last_exec = df_exec.iloc[-1]
                    atr = float(last_exec.get("atr") or 0.0)
                    if atr <= 0 or pd.isna(atr):
                        continue

                    # LONG scalp: micro uptrend, pullback zone, RSI reset, reclaim VWAP, momentum candle
                    if trend_long(df_exec, df_confirm, df_regime):
                        if pullback_zone_ok(last_exec, "LONG") and rsi_reset_ok(last_exec, "LONG"):
                            reclaim_lvl = reclaim_trigger(last_exec, "LONG")
                            # confirm reclaim is "nearby" (avoid chasing far away)
                            if float(last_exec["close"]) >= reclaim_lvl * (1.0 - 0.0015) and momentum_candle_ok(last_exec, "LONG"):
                                if allow(ex_name, symbol, "LONG"):
                                    entry_type, entry_price = choose_entry("LONG", last_exec, reclaim_lvl)
                                    trade = build_trade(ex_name, symbol, "LONG", entry_price, atr, entry_type, reclaim_lvl)
                                    if trade:
                                        send_signal(trade)

                    # SHORT scalp: micro downtrend, pullback zone, RSI reset, reclaim VWAP down, momentum candle
                    if trend_short(df_exec, df_confirm, df_regime):
                        if pullback_zone_ok(last_exec, "SHORT") and rsi_reset_ok(last_exec, "SHORT"):
                            reclaim_lvl = reclaim_trigger(last_exec, "SHORT")
                            if float(last_exec["close"]) <= reclaim_lvl * (1.0 + 0.0015) and momentum_candle_ok(last_exec, "SHORT"):
                                if allow(ex_name, symbol, "SHORT"):
                                    entry_type, entry_price = choose_entry("SHORT", last_exec, reclaim_lvl)
                                    trade = build_trade(ex_name, symbol, "SHORT", entry_price, atr, entry_type, reclaim_lvl)
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
    return "FUTURES SCALP ELITE BOT RUNNING (INFO ONLY) — OKX + KUCOIN FUTURES"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
