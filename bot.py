# ======================================================
# FUTURES SCALP ELITE — HIGH HIT-RATE BOT (INFO ONLY)
# OKX + KUCOIN FUTURES • TOP MOVERS • 3-TF FILTER
# MEAN REVERSION: EXTREME AWAY FROM VWAP + BB + RSI, RE-ENTRY CANDLE
# HIGH HIT-RATE TARGETS (SMALL) • TIGHT INVALIDATION
# TIME-BASED EXIT • STOP-PENALTY COOLDOWN • TP/SL TRACKING • POSITION SIZING (INFO)
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

RSI_LONG_MAX = float(os.getenv("RSI_LONG_MAX", 30))  # oversold for LONG
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

# TP allocations (high hit-rate: small TP1/TP2)
TP_ALLOCS = [60, 25, 15]
TP_RATIOS_RAW = os.getenv("TP_RATIOS", "0.35,0.60,0.90")  # small R targets
TP_RATIOS = [float(x.strip()) for x in TP_RATIOS_RAW.split(",") if x.strip()]
if len(TP_RATIOS) != 3:
    TP_RATIOS = [0.35, 0.60, 0.90]

# Time-based exit
MAX_TRADE_LIFETIME_SECS = int(os.getenv("MAX_TRADE_LIFETIME_SECS", 15 * 60))  # 15 min

# Position sizing (info)
ACCOUNT_USDT = float(os.getenv("ACCOUNT_USDT", 1000))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", 0.35))
MAX_NOTIONAL_USDT = float(os.getenv("MAX_NOTIONAL_USDT", 5000))
MIN_NOTIONAL_USDT = float(os.getenv("MIN_NOTIONAL_USDT", 25))

# Pending expiry
USE_PULLBACK_MODE = os.getenv("USE_PULLBACK_MODE", "0") == "1"  # mean reversion usually enters NOW
PENDING_EXPIRY_SECS = int(os.getenv("PENDING_EXPIRY_SECS", 45 * 60))

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
        "✅ FUTURES SCALP — HIGH HIT-RATE (INFO ONLY)\n\n"
        "Style: VWAP mean reversion in low-trend regime\n"
        f"TFs: {TF_EXEC} / {TF_CONFIRM} / {TF_REGIME}\n"
        f"Extreme: |price−VWAP| ≥ {VWAP_DEV_PCT*100:.2f}%\n"
        f"BB tag req: {REQUIRE_BB_TAG} | RSI: long≤{RSI_LONG_MAX} short≥{RSI_SHORT_MIN}\n"
        f"Regime: ADX({ADX_LEN}) ≤ {ADX_MAX}\n"
        f"Stop: {STOP_ATR_MULT}×ATR | window {STOP_MIN_PCT:.2f}%–{STOP_MAX_PCT:.2f}%\n"
        f"TP ratios (R): 1:{r1:g} / 1:{r2:g} / 1:{r3:g} | splits {TP_ALLOCS[0]}/{TP_ALLOCS[1]}/{TP_ALLOCS[2]}\n"
        f"Max trade time: {MAX_TRADE_LIFETIME_SECS//60} min\n"
        f"Filters: spread≤{MAX_SPREAD_BPS}bps | 24h qv≥{MIN_QUOTE_VOL_USDT/1e6:.0f}M\n"
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

def _adx(df: pd.DataFrame, length: int) -> pd.Series:
    # classic ADX
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
    # EMA
    df["ema"] = df["close"].ewm(span=EMA_LEN, adjust=False).mean()

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

    # Bollinger Bands
    mid = df["close"].rolling(BB_LEN).mean()
    sd = df["close"].rolling(BB_LEN).std()
    df["bb_mid"] = mid
    df["bb_up"] = mid + BB_STD * sd
    df["bb_dn"] = mid - BB_STD * sd

    # Rolling VWAP (approx)
    vwap_len = 60
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    df["vwap"] = pv.rolling(vwap_len).sum() / (df["volume"].rolling(vwap_len).sum() + 1e-12)

    # ADX
    df["adx"] = _adx(df, ADX_LEN)

    return df

def get_df(ex, symbol: str, tf: str):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=240)
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
# TOP MOVERS (keep only active/liquid; mean reversion works best with action)
# ======================================================

def detect_top_movers(ex):
    movers = []
    pairs = build_quality_universe(ex)
    for s in pairs:
        df = get_df(ex, s, TF_CONFIRM)
        if df is None or len(df) < 80:
            continue
        # use absolute move over last 30 mins (6 bars of 5m)
        base = df["close"].iloc[-7]
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
# CORE STRATEGY (High hit-rate mean reversion)
# ======================================================

def regime_ok(df_regime) -> bool:
    last = df_regime.iloc[-1]
    adx = float(last["adx"])
    return (not pd.isna(adx)) and adx <= ADX_MAX

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
        if price > float(last["bb_dn"]):
            return False
        if REENTRY_REQUIRED:
            # require re-entry candle: current close back above bb_dn
            prev = df_exec.iloc[-2]
            if float(prev["close"]) <= float(prev["bb_dn"]) and price <= float(last["bb_dn"]):
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
        if price < float(last["bb_up"]):
            return False
        if REENTRY_REQUIRED:
            prev = df_exec.iloc[-2]
            if float(prev["close"]) >= float(prev["bb_up"]) and price >= float(last["bb_up"]):
                return False
    return True

def choose_entry(direction: str, last_exec, vwap: float) -> tuple:
    # Mean reversion: enter NOW; you want the bounce, not a missed limit
    return ("NOW", float(last_exec["close"]))

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

def build_trade(ex_name: str, symbol: str, direction: str, entry_price: float, atr: float, vwap_level: float):
    # Stop beyond recent extreme using ATR (tight)
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

    msg = (
        f"{header}\n\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n"
        f"Entry: {round(trade['entry'], 6)}\n"
        f"VWAP: {round(trade['vwap'], 6)}\n"
        f"Stop: {round(trade['stop'], 6)} ({stop_pct:.2f}%)\n\n"
        f"TP1: {round(tp1, 6)} ({TP_ALLOCS[0]}%)\n"
        f"TP2: {round(tp2, 6)} ({TP_ALLOCS[1]}%)\n"
        f"TP3: {round(tp3, 6)} ({TP_ALLOCS[2]}%)\n\n"
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

                # TP hits
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

                    if not regime_ok(df_regime):
                        continue

                    last_exec = df_exec.iloc[-1]
                    atr = float(last_exec.get("atr") or 0.0)
                    if atr <= 0 or pd.isna(atr):
                        continue

                    # LONG fade: extreme below vwap + RSI oversold (+ BB tag)
                    if extreme_long(df_exec):
                        if allow(ex_name, symbol, "LONG"):
                            vwap_lvl = float(last_exec["vwap"])
                            entry_type, entry_price = choose_entry("LONG", last_exec, vwap_lvl)
                            trade = build_trade(ex_name, symbol, "LONG", entry_price, atr, vwap_lvl)
                            if trade:
                                send_signal(trade)

                    # SHORT fade: extreme above vwap + RSI overbought (+ BB tag)
                    if extreme_short(df_exec):
                        if allow(ex_name, symbol, "SHORT"):
                            vwap_lvl = float(last_exec["vwap"])
                            entry_type, entry_price = choose_entry("SHORT", last_exec, vwap_lvl)
                            trade = build_trade(ex_name, symbol, "SHORT", entry_price, atr, vwap_lvl)
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
