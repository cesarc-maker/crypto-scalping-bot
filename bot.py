# ======================================================
# FUTURES SCALP ELITE — HIGH HIT-RATE BOT (INFO ONLY)
# OKX + KUCOIN FUTURES • TOP MOVERS • 3-TF FILTER
# MEAN REVERSION: EXTREME AWAY FROM VWAP + BB + RSI, RE-ENTRY CANDLE
# TP LADDER: MIN 1:1 (1R) + FOLLOW-THROUGH
# SAFETY NET: AFTER ANY TP HIT, SEND "LIKELY NEXT TARGET" + BE+BUFFER STOP RECOMMENDATION
# PERFORMANCE: AFTER 20 CLOSED TRADES, SEND WIN/LOSS REPORT
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
log = logging.getLogger("FUTURES_SCALP_HITRATE")

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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 10))
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 5))

# Universe size and mover selection
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 160))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 18))

# Cooldowns
WINDOW = int(os.getenv("WINDOW", 900))  # 15 min
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 3600))  # 1h

# Exchanges
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]

# Timeframes (scalp)
TF_EXEC = os.getenv("TF_EXEC", "1m")        # entries
TF_CONFIRM = os.getenv("TF_CONFIRM", "5m")  # filter
TF_REGIME = os.getenv("TF_REGIME", "15m")   # regime

# Liquidity/spread filters (scalps need tight)
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 8_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 15))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Indicators
EMA_LEN = int(os.getenv("EMA_LEN", 20))
RSI_LEN = int(os.getenv("RSI_LEN", 14))
ATR_LEN = int(os.getenv("ATR_LEN", 14))

# Mean reversion thresholds
VWAP_DEV_PCT = float(os.getenv("VWAP_DEV_PCT", 0.006))     # 0.60% away from VWAP = extreme
BB_LEN = int(os.getenv("BB_LEN", 20))
BB_STD = float(os.getenv("BB_STD", 2.0))
REQUIRE_BB_TAG = os.getenv("REQUIRE_BB_TAG", "1") == "1"

RSI_LONG_MAX = float(os.getenv("RSI_LONG_MAX", 30))   # oversold for LONG
RSI_SHORT_MIN = float(os.getenv("RSI_SHORT_MIN", 70)) # overbought for SHORT

# Regime filter (avoid fading strong trends)
ADX_LEN = int(os.getenv("ADX_LEN", 14))
ADX_MAX = float(os.getenv("ADX_MAX", 18))  # only fade if ADX is low-ish (range)

# Entry confirmation: require re-entry candle back inside BB
REENTRY_REQUIRED = os.getenv("REENTRY_REQUIRED", "1") == "1"

# Risk model (tight)
STOP_ATR_MULT = float(os.getenv("STOP_ATR_MULT", 0.8))
STOP_MIN_PCT = float(os.getenv("STOP_MIN_PCT", 0.18))
STOP_MAX_PCT = float(os.getenv("STOP_MAX_PCT", 0.55))

# Leverage (info)
LEV_TIGHT = int(os.getenv("LEV_TIGHT", 45))
LEV_NORMAL = int(os.getenv("LEV_NORMAL", 20))
LEV_TIGHT_STOP_PCT = float(os.getenv("LEV_TIGHT_STOP_PCT", 0.35))

# TP allocations
TP_ALLOCS = [60, 25, 15]

# ✅ TP ladder: MIN 1R at TP1, then follow-through
TP_RATIOS_RAW = os.getenv("TP_RATIOS", "1.0,1.5,2.0")
TP_RATIOS = [float(x.strip()) for x in TP_RATIOS_RAW.split(",") if x.strip()]
if len(TP_RATIOS) != 3 or TP_RATIOS[0] < 1.0:
    TP_RATIOS = [1.0, 1.5, 2.0]

# Time-based exit
MAX_TRADE_LIFETIME_SECS = int(os.getenv("MAX_TRADE_LIFETIME_SECS", 15 * 60))  # 15 min

# Position sizing (info)
ACCOUNT_USDT = float(os.getenv("ACCOUNT_USDT", 1000))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", 0.35))
MAX_NOTIONAL_USDT = float(os.getenv("MAX_NOTIONAL_USDT", 5000))
MIN_NOTIONAL_USDT = float(os.getenv("MIN_NOTIONAL_USDT", 25))

# Pending expiry (not used for NOW mode)
USE_PULLBACK_MODE = os.getenv("USE_PULLBACK_MODE", "0") == "1"
PENDING_EXPIRY_SECS = int(os.getenv("PENDING_EXPIRY_SECS", 45 * 60))

# Safety net
BE_BUFFER_BPS = float(os.getenv("BE_BUFFER_BPS", 8.0))  # 8 bps = 0.08%

# Performance reporting
CLOSED_TRADES_REPORT_N = int(os.getenv("CLOSED_TRADES_REPORT_N", 20))

# ======================================================
# STATE
# ======================================================

recent_signals = {}
penalty_cooldowns = {}
cooldown_lock = threading.Lock()

open_trades = {}
open_trades_lock = threading.Lock()

closed_trades = []
closed_lock = threading.Lock()

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
        "✅ FUTURES SCALP — HIGH HIT-RATE (INFO ONLY)\n\n"
        "Style: VWAP mean reversion in low-trend regime\n"
        f"TFs: {TF_EXEC} / {TF_CONFIRM} / {TF_REGIME}\n"
        f"Extreme: |price−VWAP| ≥ {VWAP_DEV_PCT*100:.2f}%\n"
        f"BB tag req: {REQUIRE_BB_TAG} | RSI: long≤{RSI_LONG_MAX} short≥{RSI_SHORT_MIN}\n"
        f"Re-entry req: {REENTRY_REQUIRED} (prev outside BB, current back inside)\n"
        f"Regime: ADX({ADX_LEN}) ≤ {ADX_MAX}\n"
        f"Stop: {STOP_ATR_MULT}×ATR | window {STOP_MIN_PCT:.2f}%–{STOP_MAX_PCT:.2f}%\n"
        f"TP ratios (R): 1:{r1:g} / 1:{r2:g} / 1:{r3:g} | splits {TP_ALLOCS[0]}/{TP_ALLOCS[1]}/{TP_ALLOCS[2]}\n"
        f"Safety net: after TP hit → BE+buffer ({BE_BUFFER_BPS:g}bps) + Likely TP (VWAP/BBmid/EMA)\n"
        f"Max trade time: {MAX_TRADE_LIFETIME_SECS//60} min\n"
        f"Filters: spread≤{MAX_SPREAD_BPS}bps | 24h qv≥{MIN_QUOTE_VOL_USDT/1e6:.0f}M\n"
        f"Perf report: every {CLOSED_TRADES_REPORT_N} closed trades\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n\n"
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

    with cooldown_lock:
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
    with cooldown_lock:
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

def _adx(df: pd.DataFrame, length: int) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    prev_close = close.shift(1)
    tr1 = (high - low)
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(length).mean()
    plus_di = 100 * (plus_dm.rolling(length).mean() / (atr + 1e-12))
    minus_di = 100 * (minus_dm.rolling(length).mean() / (atr + 1e-12))
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-12)) * 100
    adx = dx.rolling(length).mean()
    return adx

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema"] = df["close"].ewm(span=EMA_LEN, adjust=False).mean()
    df["vol_sma"] = df["volume"].rolling(20).mean()

    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()

    df["range"] = df["high"] - df["low"]
    df["rsi"] = _rsi(df["close"], RSI_LEN)

    mid = df["close"].rolling(BB_LEN).mean()
    sd = df["close"].rolling(BB_LEN).std()
    df["bb_mid"] = mid
    df["bb_up"] = mid + BB_STD * sd
    df["bb_dn"] = mid - BB_STD * sd

    # Rolling VWAP approximation (60 bars)
    vwap_len = 60
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    df["vwap"] = pv.rolling(vwap_len).sum() / (df["volume"].rolling(vwap_len).sum() + 1e-12)

    df["adx"] = _adx(df, ADX_LEN)
    return df

def get_df(ex, symbol: str, tf: str, limit: int = 160):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=limit)
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
        if bid is None or ask is None:
            continue  # require bid/ask to enforce spread filter reliably

        try:
            bid = float(bid)
            ask = float(ask)
        except Exception:
            continue

        if bid <= 0 or ask <= 0:
            continue

        spread_bps = ((ask - bid) / bid) * 10_000
        if spread_bps > MAX_SPREAD_BPS:
            continue

        out.append((symbol, qv))

    out.sort(key=lambda x: x[1], reverse=True)
    return [s for s, _ in out[:PAIR_LIMIT]]

# ======================================================
# TOP MOVERS
# ======================================================

def detect_top_movers(ex):
    movers = []
    pairs = build_quality_universe(ex)
    for s in pairs:
        df = get_df(ex, s, TF_CONFIRM, limit=120)
        if df is None or len(df) < 80:
            continue

        base = df["close"].iloc[-7]  # 30 mins ago on 5m TF
        last = df["close"].iloc[-1]
        if base <= 0:
            continue

        abs_pct = abs((last - base) / base * 100.0)
        vol_ratio = float(df["volume"].iloc[-1]) / (float(df["vol_sma"].iloc[-1]) + 1e-9)
        score = abs_pct * 0.7 + vol_ratio * 0.3
        movers.append((s, score))

    movers.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers[:TOP_MOVER_COUNT]]

# ======================================================
# CORE STRATEGY
# ======================================================

def regime_ok(df_regime) -> bool:
    last = df_regime.iloc[-1]
    adx = float(last["adx"])
    return (not pd.isna(adx)) and adx <= ADX_MAX

def _reentry_long(df_exec: pd.DataFrame) -> bool:
    # prev closed below BB_dn (outside), current closed back above BB_dn (inside)
    if len(df_exec) < 3:
        return False
    prev = df_exec.iloc[-2]
    last = df_exec.iloc[-1]
    return float(prev["close"]) < float(prev["bb_dn"]) and float(last["close"]) > float(last["bb_dn"])

def _reentry_short(df_exec: pd.DataFrame) -> bool:
    # prev closed above BB_up (outside), current closed back below BB_up (inside)
    if len(df_exec) < 3:
        return False
    prev = df_exec.iloc[-2]
    last = df_exec.iloc[-1]
    return float(prev["close"]) > float(prev["bb_up"]) and float(last["close"]) < float(last["bb_up"])

def extreme_long(df_exec) -> bool:
    last = df_exec.iloc[-1]
    price = float(last["close"])
    vwap = float(last["vwap"])
    if vwap <= 0:
        return False

    dev = (vwap - price) / vwap  # positive when below vwap
    if dev < VWAP_DEV_PCT:
        return False

    if float(last["rsi"]) > RSI_LONG_MAX:
        return False

    if REQUIRE_BB_TAG:
        # Require that the extreme actually tagged below lower band at least on prev candle
        # (more stable than checking only last candle)
        prev = df_exec.iloc[-2]
        if not (float(prev["close"]) < float(prev["bb_dn"]) or price < float(last["bb_dn"])):
            return False

        if REENTRY_REQUIRED and not _reentry_long(df_exec):
            return False

    return True

def extreme_short(df_exec) -> bool:
    last = df_exec.iloc[-1]
    price = float(last["close"])
    vwap = float(last["vwap"])
    if vwap <= 0:
        return False

    dev = (price - vwap) / vwap  # positive when above vwap
    if dev < VWAP_DEV_PCT:
        return False

    if float(last["rsi"]) < RSI_SHORT_MIN:
        return False

    if REQUIRE_BB_TAG:
        prev = df_exec.iloc[-2]
        if not (float(prev["close"]) > float(prev["bb_up"]) or price > float(last["bb_up"])):
            return False

        if REENTRY_REQUIRED and not _reentry_short(df_exec):
            return False

    return True

def choose_entry(direction: str, last_exec, vwap: float) -> tuple:
    # Mean reversion: enter NOW (info only)
    return ("NOW", float(last_exec["close"]))

# ======================================================
# "LIKELY" TP + SAFETY NET
# ======================================================

def recommended_tp_likely(df_exec: pd.DataFrame, direction: str, entry: float) -> tuple:
    """
    Returns (label, price) for a high-likelihood mean-reversion target.
    Chooses closest 'magnet' in the favorable direction: VWAP, BB mid, EMA.
    """
    last = df_exec.iloc[-1]
    vwap = float(last.get("vwap") or 0.0)
    bb_mid = float(last.get("bb_mid") or 0.0)
    ema = float(last.get("ema") or 0.0)

    candidates = []
    if vwap > 0:   candidates.append(("VWAP", vwap))
    if bb_mid > 0: candidates.append(("BB Mid", bb_mid))
    if ema > 0:    candidates.append(("EMA", ema))

    if not candidates:
        return ("", 0.0)

    if direction == "LONG":
        ups = [(lab, px) for lab, px in candidates if px > entry]
        if ups:
            return min(ups, key=lambda x: abs(x[1] - entry))
    else:
        dns = [(lab, px) for lab, px in candidates if px < entry]
        if dns:
            return min(dns, key=lambda x: abs(x[1] - entry))

    return min(candidates, key=lambda x: abs(x[1] - entry))

def breakeven_stop(entry: float, direction: str, buffer_bps: float = 8.0) -> float:
    if entry <= 0:
        return 0.0
    buf = entry * (buffer_bps / 10_000.0)
    return (entry + buf) if direction == "LONG" else (entry - buf)

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
# PERFORMANCE REPORTING (every N closed trades)
# ======================================================

def send_performance_report(last_n: int = 20):
    with closed_lock:
        sample = closed_trades[-last_n:] if len(closed_trades) >= last_n else closed_trades[:]

    if not sample:
        return

    # Win definition: realized_pct > 0 (you banked something)
    wins = sum(1 for r in sample if r["realized_pct"] > 0)
    losses = len(sample) - wins

    stop_count = sum(1 for r in sample if r["outcome"] == "STOP")
    time_count = sum(1 for r in sample if r["outcome"] == "TIME")
    alltp_count = sum(1 for r in sample if r["outcome"] == "ALL_TPS")

    avg_realized = sum(r["realized_pct"] for r in sample) / len(sample)
    avg_full = sum(r["pnl_full_pct"] for r in sample) / len(sample)

    msg = (
        f"📊 PERFORMANCE REPORT (last {len(sample)} closed trades)\n\n"
        f"Wins: {wins} | Losses: {losses}\n"
        f"Win rate: {wins/len(sample)*100:.1f}% | Loss rate: {losses/len(sample)*100:.1f}%\n\n"
        f"Outcomes — STOP: {stop_count} | TIME: {time_count} | ALL TPs: {alltp_count}\n\n"
        f"Avg realized (partial TP logic): {avg_realized:.2f}%\n"
        f"Avg full-size exit PnL (gross est.): {avg_full:.2f}%\n\n"
        "Notes: Wins counted as realized_pct > 0. Gross estimates ignore fees/slippage."
    )
    send_telegram(msg)

def record_closed_trade(trade: dict, outcome: str, last_price: float, pnl_full_pct: float):
    row = {
        "ts": int(time.time()),
        "ex": trade.get("ex_name"),
        "symbol": trade.get("symbol"),
        "side": trade.get("direction"),
        "outcome": outcome,  # STOP | TIME | ALL_TPS
        "pnl_full_pct": float(pnl_full_pct),
        "realized_pct": float(trade.get("realized_pct", 0.0)),
        "elapsed_sec": int(time.time() - int(trade.get("start_ts", time.time()))),
        "exit_price": float(last_price),
    }
    with closed_lock:
        closed_trades.append(row)
        n = len(closed_trades)

    if n % CLOSED_TRADES_REPORT_N == 0:
        send_performance_report(last_n=CLOSED_TRADES_REPORT_N)

# ======================================================
# SIGNAL BUILDER + REGISTER FOR TRACKING
# ======================================================

def build_trade(ex_name: str, symbol: str, direction: str, entry_price: float, atr: float, vwap_level: float,
                tp_likely_label: str, tp_likely: float):
    stop = entry_price - STOP_ATR_MULT * atr if direction == "LONG" else entry_price + STOP_ATR_MULT * atr
    stop_pct = abs(entry_price - stop) / entry_price * 100.0 if entry_price > 0 else 999.0

    if stop_pct < STOP_MIN_PCT or stop_pct > STOP_MAX_PCT:
        return None

    leverage = LEV_TIGHT if stop_pct < LEV_TIGHT_STOP_PCT else LEV_NORMAL
    risk = "LOW" if leverage >= 40 else "MEDIUM"

    tps = build_r_based_tps(entry_price, stop, direction)
    if not tps:
        return None

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": direction,
        "entry": float(entry_price),
        "vwap": float(vwap_level),
        "stop": float(stop),
        "tps": [float(tps[0]), float(tps[1]), float(tps[2])],
        "tp_allocs": TP_ALLOCS[:],
        "tp_hits": [False, False, False],
        "tp_likely_label": tp_likely_label or "",
        "tp_likely": float(tp_likely) if tp_likely else 0.0,
        "leverage": int(leverage),
        "risk": risk,
        "entry_type": "NOW",
        "status": "ACTIVE",
        "created_ts": now,
        "start_ts": now,
        "filled_ts": now,
        "realized_pct": 0.0,
    }

def send_signal(trade: dict):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    direction = trade["direction"]
    header = "🎯 HIGH HIT-RATE SCALP " + direction + " (MEAN REVERSION)"

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

    likely_line = ""
    if trade.get("tp_likely", 0.0) > 0 and trade.get("tp_likely_label", ""):
        likely_line = f"\n🎯 Recommended TP (Likely): {trade['tp_likely_label']} @ {round(trade['tp_likely'], 6)}\n"

    msg = (
        f"{header}\n\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n"
        f"Entry: {round(trade['entry'], 6)}\n"
        f"VWAP: {round(trade['vwap'], 6)}\n"
        f"Stop: {round(trade['stop'], 6)} ({stop_pct:.2f}%)\n\n"
        f"TP1: {round(tp1, 6)} ({TP_ALLOCS[0]}%)\n"
        f"TP2: {round(tp2, 6)} ({TP_ALLOCS[1]}%)\n"
        f"TP3: {round(tp3, 6)} ({TP_ALLOCS[2]}%)\n"
        f"{likely_line}"
        f"RR targets (R): 1:{r1:g} / 1:{r2:g} / 1:{r3:g}\n"
        f"Leverage (info): {trade['leverage']}x\n"
        f"Risk Level: {trade['risk']}\n"
        f"Max lifetime: {MAX_TRADE_LIFETIME_SECS//60} min\n\n"
        f"Position (info): {notional:.0f} USDT notional | Margin {margin:.1f} USDT\n"
        f"Risk (info): ~{risk_usdt:.2f} USDT (@{RISK_PCT_PER_TRADE:.2f}%)\n\n"
        f"Est Return @TP1: {ret1:.1f}% | @TP2: {ret2:.1f}% | @TP3: {ret3:.1f}%\n"
        f"Time: {ts}\n\n"
        "⚠️ Info only. Not financial advice."
    )

    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} {direction}")

    trade_key = f"{trade['ex_name']}|{trade['symbol']}|{direction}|{int(time.time())}"
    with open_trades_lock:
        open_trades[trade_key] = trade

# ======================================================
# TRACKER LOOP (ACTIVE TP/SL + TIME EXIT)
# ======================================================

def send_safety_net_reco(trade: dict):
    entry = float(trade.get("entry", 0.0))
    direction = trade.get("direction", "")
    be = breakeven_stop(entry, direction, buffer_bps=BE_BUFFER_BPS)

    msg = (
        "🛡️ SAFETY NET (recommendation)\n"
        f"- Consider moving stop → BE+buffer ({BE_BUFFER_BPS:g}bps): {round(be, 6)}\n"
    )

    likely = float(trade.get("tp_likely", 0.0) or 0.0)
    likely_label = trade.get("tp_likely_label", "") or ""
    if likely > 0 and likely_label:
        msg += f"- Likely next target: {likely_label} @ {round(likely, 6)}\n"

    msg += "⚠️ Info only. Not financial advice."
    send_telegram(msg)

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

                elapsed = int(time.time() - int(t["start_ts"]))
                duration = format_duration(elapsed)

                # time exit
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
                    record_closed_trade(t, outcome="TIME", last_price=last_price, pnl_full_pct=pnl)
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
                    record_closed_trade(t, outcome="STOP", last_price=last_price, pnl_full_pct=pnl)
                    apply_stop_penalty(t["ex_name"], t["symbol"], direction)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # TP hits
                tps = t["tps"]
                allocs = t.get("tp_allocs", TP_ALLOCS)
                hit_any = False
                safety_sent = False

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

                        # ✅ Safety net recommendation after first TP hit in this update cycle
                        if not safety_sent:
                            send_safety_net_reco(t)
                            safety_sent = True

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
                    tp3_price = float(t["tps"][2])
                    pnl = calc_profit_pct(entry, tp3_price, direction, leverage)
                    record_closed_trade(t, outcome="ALL_TPS", last_price=tp3_price, pnl_full_pct=pnl)
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
                    df_exec = get_df(ex, symbol, TF_EXEC, limit=180)
                    df_regime = get_df(ex, symbol, TF_REGIME, limit=180)
                    if df_exec is None or df_regime is None:
                        continue

                    if not regime_ok(df_regime):
                        continue

                    last_exec = df_exec.iloc[-1]
                    atr = float(last_exec.get("atr") or 0.0)
                    if atr <= 0 or pd.isna(atr):
                        continue

                    vwap_lvl = float(last_exec.get("vwap") or 0.0)
                    if vwap_lvl <= 0 or pd.isna(vwap_lvl):
                        continue

                    # LONG
                    if extreme_long(df_exec):
                        if allow(ex_name, symbol, "LONG"):
                            entry_type, entry_price = choose_entry("LONG", last_exec, vwap_lvl)
                            lab, likely_tp = recommended_tp_likely(df_exec, "LONG", entry_price)
                            trade = build_trade(ex_name, symbol, "LONG", entry_price, atr, vwap_lvl, lab, likely_tp)
                            if trade:
                                send_signal(trade)

                    # SHORT
                    if extreme_short(df_exec):
                        if allow(ex_name, symbol, "SHORT"):
                            entry_type, entry_price = choose_entry("SHORT", last_exec, vwap_lvl)
                            lab, likely_tp = recommended_tp_likely(df_exec, "SHORT", entry_price)
                            trade = build_trade(ex_name, symbol, "SHORT", entry_price, atr, vwap_lvl, lab, likely_tp)
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
    return "FUTURES SCALP HIGH HIT-RATE BOT RUNNING (INFO ONLY) — OKX + KUCOIN FUTURES"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
