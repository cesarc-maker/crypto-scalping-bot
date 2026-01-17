# ======================================================
# NEW FUTURES SIGNAL BOT (INFO ONLY — NO EXECUTION)
# OKX + KUCOIN FUTURES • TOP MOVERS • 1m/5m
#
# MODULES:
# 1) DEMAND ZONE LONGS:
#    - Zone built on 5m: last bearish candle before impulse up
#    - Entry trigger on 1m: candle CLOSES inside zone
#    - TP: strict 1:1 (1R)
#    - Leverage: "max" = your configured LEV_MAX (info only)
#
# 2) 5–6% PUMP -> FIB LONGS:
#    - Pump detected on 5m lookback
#    - Fib band (0.5–0.618 by default)
#    - Entry trigger on 1m: candle CLOSES inside fib band
#    - TP: strict 1:1 (1R)
#
# SAFETY NET (RECOMMENDATION ONLY):
# - After TP hit: send BE+buffer stop suggestion + "Likely next target" (VWAP/BBmid/EMA)
#
# PERFORMANCE:
# - After every 20 CLOSED trades: win rate / loss rate report
#   (Win = realized_pct > 0)
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("NEW_FUTURES_SIGNAL_BOT")

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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 12))
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 5))

# Universe size
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 160))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 18))

# Cooldowns
WINDOW = int(os.getenv("WINDOW", 900))                 # 15 min duplicate cooldown
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 3600))  # 1h

# Exchanges
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]

# Timeframes
TF_TRIGGER = os.getenv("TF_TRIGGER", "1m")   # entries
TF_STRUCT = os.getenv("TF_STRUCT", "5m")     # zones / pumps / fib

# Liquidity/spread filters
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 8_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 15))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Indicators
EMA_LEN = int(os.getenv("EMA_LEN", 20))
RSI_LEN = int(os.getenv("RSI_LEN", 14))
ATR_LEN = int(os.getenv("ATR_LEN", 14))

# Stop window guardrails (still useful)
STOP_MIN_PCT = float(os.getenv("STOP_MIN_PCT", 0.18))
STOP_MAX_PCT = float(os.getenv("STOP_MAX_PCT", 0.75))

# “Max leverage” cap (info only)
LEV_MAX = int(os.getenv("LEV_MAX", 50))

# Strict 1:1 (1R only)
STRICT_1R_ONLY = os.getenv("STRICT_1R_ONLY", "1") == "1"

# Safety net
BE_BUFFER_BPS = float(os.getenv("BE_BUFFER_BPS", 8.0))  # 0.08%

# Performance reporting
CLOSED_TRADES_REPORT_N = int(os.getenv("CLOSED_TRADES_REPORT_N", 20))

# --- Demand zone settings ---
ENABLE_DEMAND_ZONE = os.getenv("ENABLE_DEMAND_ZONE", "1") == "1"
IMPULSE_BARS = int(os.getenv("IMPULSE_BARS", 3))          # number of 5m bars after OB
IMPULSE_PCT = float(os.getenv("IMPULSE_PCT", 0.015))      # 1.5% impulse threshold
ZONE_STOP_PAD_ATR = float(os.getenv("ZONE_STOP_PAD_ATR", 0.20))  # stop below zone low by 0.2 ATR(1m)

# --- Pump -> Fib settings ---
ENABLE_PUMP_FIB = os.getenv("ENABLE_PUMP_FIB", "1") == "1"
PUMP_LOOKBACK_BARS = int(os.getenv("PUMP_LOOKBACK_BARS", 24))  # 2h if 5m
PUMP_MIN_PCT = float(os.getenv("PUMP_MIN_PCT", 0.05))          # 5%
FIB_A = float(os.getenv("FIB_A", 0.50))
FIB_B = float(os.getenv("FIB_B", 0.618))
FIB_STOP_PAD_ATR = float(os.getenv("FIB_STOP_PAD_ATR", 0.20))  # stop below swing low by 0.2 ATR(1m)

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
    msg = (
        "✅ NEW FUTURES SIGNAL BOT (INFO ONLY)\n\n"
        f"TFs: trigger={TF_TRIGGER} / structure={TF_STRUCT}\n"
        f"Modules: DemandZone={ENABLE_DEMAND_ZONE} | PumpFib={ENABLE_PUMP_FIB}\n"
        f"DemandZone: impulse {IMPULSE_BARS} bars, >= {IMPULSE_PCT*100:.2f}%\n"
        f"PumpFib: pump >= {PUMP_MIN_PCT*100:.1f}% lookback {PUMP_LOOKBACK_BARS} bars, fib {FIB_A:g}–{FIB_B:g}\n"
        f"Entry rule: 1m CLOSE inside zone/band\n"
        f"TP: strict 1:1 (1R)\n"
        f"Leverage cap (info): {LEV_MAX}x\n"
        f"Filters: spread≤{MAX_SPREAD_BPS}bps | 24h qv≥{MIN_QUOTE_VOL_USDT/1e6:.0f}M\n"
        f"Perf report: every {CLOSED_TRADES_REPORT_N} closed trades\n\n"
        "⚠️ Info only. Not financial advice. No execution."
    )
    send_telegram(msg)

# ======================================================
# COOLDOWNS
# ======================================================

def _cd_key(ex_name: str, symbol: str, direction: str, strat: str) -> str:
    return f"{ex_name}_{symbol}_{direction}_{strat}"

def allow_signal(ex_name: str, symbol: str, direction: str, strat: str) -> bool:
    now = time.time()
    key = _cd_key(ex_name, symbol, direction, strat)

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

def apply_stop_penalty(ex_name: str, symbol: str, direction: str, strat: str):
    now = time.time()
    key = _cd_key(ex_name, symbol, direction, strat)
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

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df["ema"] = df["close"].ewm(span=EMA_LEN, adjust=False).mean()

    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_LEN).mean()

    df["rsi"] = _rsi(df["close"], RSI_LEN)

    # BB mid only (for "likely target")
    mid = df["close"].rolling(20).mean()
    df["bb_mid"] = mid

    # Rolling VWAP approximation (60 bars)
    vwap_len = 60
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    df["vwap"] = pv.rolling(vwap_len).sum() / (df["volume"].rolling(vwap_len).sum() + 1e-12)

    return df

def get_df(ex, symbol: str, tf: str, limit: int = 220):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        return add_indicators(df)
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

EX_INSTANCES = {}

def get_ex_cached(name: str):
    if name in EX_INSTANCES and EX_INSTANCES[name]:
        return EX_INSTANCES[name]
    ex = get_ex(name)
    EX_INSTANCES[name] = ex
    return ex

# ======================================================
# UNIVERSE + MOVERS
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
            continue
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

def detect_top_movers(ex):
    movers = []
    pairs = build_quality_universe(ex)

    # Score movers using 5m close change over ~30 mins (6 bars)
    for s in pairs:
        df = get_df(ex, s, TF_STRUCT, limit=120)
        if df is None or len(df) < 80:
            continue
        base = float(df["close"].iloc[-7])
        last = float(df["close"].iloc[-1])
        if base <= 0:
            continue
        abs_pct = abs((last - base) / base) * 100.0
        movers.append((s, abs_pct))

    movers.sort(key=lambda x: x[1], reverse=True)
    return [m[0] for m in movers[:TOP_MOVER_COUNT]]

# ======================================================
# STRATEGY HELPERS
# ======================================================

def in_zone(close_price: float, z_low: float, z_high: float) -> bool:
    lo = min(z_low, z_high)
    hi = max(z_low, z_high)
    return lo <= close_price <= hi

def build_1r_tp(entry: float, stop: float, direction: str) -> float:
    R = abs(entry - stop)
    if R <= 0:
        return 0.0
    return entry + R if direction == "LONG" else entry - R

def stop_pct(entry: float, stop: float) -> float:
    if entry <= 0:
        return 999.0
    return abs(entry - stop) / entry * 100.0

def breakeven_stop(entry: float, direction: str, buffer_bps: float) -> float:
    if entry <= 0:
        return 0.0
    buf = entry * (buffer_bps / 10_000.0)
    return entry + buf if direction == "LONG" else entry - buf

def recommended_tp_likely(df_1m: pd.DataFrame, direction: str, entry: float) -> tuple:
    """
    "Likely next target" for mean reversion behavior: choose nearest magnet in favorable direction
    from VWAP / BB mid / EMA computed on 1m.
    """
    last = df_1m.iloc[-1]
    vwap = float(last.get("vwap") or 0.0)
    bb_mid = float(last.get("bb_mid") or 0.0)
    ema = float(last.get("ema") or 0.0)

    candidates = []
    if vwap > 0: candidates.append(("VWAP", vwap))
    if bb_mid > 0: candidates.append(("BB Mid", bb_mid))
    if ema > 0: candidates.append(("EMA", ema))
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

def find_bullish_demand_zone_5m(df5: pd.DataFrame):
    """
    Bullish demand zone:
    - Find bearish candle on 5m
    - Followed by impulse up over next IMPULSE_BARS 5m candles >= IMPULSE_PCT
    Zone = [low, open] of bearish candle
    """
    if df5 is None or len(df5) < (IMPULSE_BARS + 12):
        return None

    # Scan recent history (newest-first)
    for i in range(len(df5) - IMPULSE_BARS - 2, 10, -1):
        c = df5.iloc[i]
        o = float(c["open"]); cl = float(c["close"])
        if cl >= o:
            continue  # bearish only

        impulse_start = float(df5.iloc[i+1]["close"])
        impulse_end = float(df5.iloc[i+IMPULSE_BARS]["close"])
        if impulse_start <= 0:
            continue
        impulse = (impulse_end - impulse_start) / impulse_start
        if impulse < IMPULSE_PCT:
            continue

        zone_low = float(c["low"])
        zone_high = float(c["open"])   # body top for red candle
        return (zone_low, zone_high, i)

    return None

def detect_pump_swing_5m(df5: pd.DataFrame):
    """
    Detect pump >= PUMP_MIN_PCT within lookback bars:
    - pick swing low
    - pick swing high after that low
    """
    if df5 is None or len(df5) < (PUMP_LOOKBACK_BARS + 10):
        return None

    look = df5.iloc[-PUMP_LOOKBACK_BARS:].copy()
    look["low"] = look["low"].astype(float)
    look["high"] = look["high"].astype(float)

    # idxmin/idxmax with original index
    low_idx = int(look["low"].idxmin())
    low_price = float(df5.loc[low_idx, "low"])

    after = df5.loc[low_idx:].iloc[-PUMP_LOOKBACK_BARS:].copy()
    after["high"] = after["high"].astype(float)
    high_idx = int(after["high"].idxmax())
    high_price = float(df5.loc[high_idx, "high"])

    if low_price <= 0 or high_idx <= low_idx:
        return None

    pump = (high_price - low_price) / low_price
    if pump < PUMP_MIN_PCT:
        return None

    return (low_price, high_price, low_idx, high_idx, pump)

def fib_band(low_price: float, high_price: float, a: float, b: float):
    rng = high_price - low_price
    if rng <= 0:
        return None
    p1 = high_price - a * rng
    p2 = high_price - b * rng
    return (min(p1, p2), max(p1, p2))

def calc_profit_pct(entry: float, price: float, direction: str, leverage: int) -> float:
    if entry <= 0:
        return 0.0
    raw = ((price - entry) / entry) * 100.0 if direction == "LONG" else ((entry - price) / entry) * 100.0
    return raw * leverage

# ======================================================
# PERFORMANCE REPORTING
# ======================================================

def send_performance_report(last_n: int):
    with closed_lock:
        sample = closed_trades[-last_n:] if len(closed_trades) >= last_n else closed_trades[:]
    if not sample:
        return

    wins = sum(1 for r in sample if r["realized_pct"] > 0)
    losses = len(sample) - wins
    stop_count = sum(1 for r in sample if r["outcome"] == "STOP")
    time_count = sum(1 for r in sample if r["outcome"] == "TIME")
    tp_count = sum(1 for r in sample if r["outcome"] == "TP")

    avg_real = sum(r["realized_pct"] for r in sample) / len(sample)
    avg_full = sum(r["pnl_full_pct"] for r in sample) / len(sample)

    msg = (
        f"📊 PERFORMANCE REPORT (last {len(sample)} closed trades)\n\n"
        f"Wins: {wins} | Losses: {losses}\n"
        f"Win rate: {wins/len(sample)*100:.1f}% | Loss rate: {losses/len(sample)*100:.1f}%\n\n"
        f"Outcomes — TP: {tp_count} | STOP: {stop_count} | TIME: {time_count}\n\n"
        f"Avg realized (your TP logic): {avg_real:.2f}%\n"
        f"Avg full-size exit PnL (gross est.): {avg_full:.2f}%\n\n"
        "Notes: Win = realized_pct > 0. Estimates ignore fees/slippage."
    )
    send_telegram(msg)

def record_closed_trade(trade: dict, outcome: str, exit_price: float, pnl_full_pct: float):
    row = {
        "ts": int(time.time()),
        "ex": trade.get("ex_name"),
        "symbol": trade.get("symbol"),
        "side": trade.get("direction"),
        "strategy": trade.get("strategy"),
        "outcome": outcome,  # TP | STOP | TIME
        "pnl_full_pct": float(pnl_full_pct),
        "realized_pct": float(trade.get("realized_pct", 0.0)),
        "elapsed_sec": int(time.time() - int(trade.get("start_ts", time.time()))),
        "exit_price": float(exit_price),
    }
    with closed_lock:
        closed_trades.append(row)
        n = len(closed_trades)

    if n % CLOSED_TRADES_REPORT_N == 0:
        send_performance_report(CLOSED_TRADES_REPORT_N)

# ======================================================
# TRADE OBJECT + SIGNAL SENDING
# ======================================================

def build_trade(ex_name: str, symbol: str, direction: str, strategy: str,
                entry: float, stop: float, df_1m: pd.DataFrame,
                extra_note: str = ""):
    sp = stop_pct(entry, stop)
    if sp < STOP_MIN_PCT or sp > STOP_MAX_PCT:
        return None

    leverage = int(LEV_MAX)  # your "max leverage" cap (info only)
    tp = build_1r_tp(entry, stop, direction) if STRICT_1R_ONLY else build_1r_tp(entry, stop, direction)
    if tp <= 0:
        return None

    lab, tp_likely = recommended_tp_likely(df_1m, direction, entry)

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": direction,
        "strategy": strategy,
        "entry": float(entry),
        "stop": float(stop),
        "tp": float(tp),
        "leverage": leverage,
        "created_ts": now,
        "start_ts": now,
        "realized_pct": 0.0,       # for strict 1R, realized == pnl at TP
        "tp_hit": False,
        "tp_likely_label": lab or "",
        "tp_likely": float(tp_likely) if tp_likely else 0.0,
        "extra_note": extra_note or "",
    }

def send_signal(trade: dict):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sp = stop_pct(trade["entry"], trade["stop"])

    likely_line = ""
    if trade.get("tp_likely", 0.0) > 0 and trade.get("tp_likely_label", ""):
        likely_line = f"🎯 Likely next target: {trade['tp_likely_label']} @ {round(trade['tp_likely'], 6)}\n"

    msg = (
        f"📣 SIGNAL — {trade['direction']} (INFO ONLY)\n"
        f"Strategy: {trade['strategy']}\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n\n"
        f"Entry: {round(trade['entry'], 6)}\n"
        f"Stop:  {round(trade['stop'], 6)} ({sp:.2f}%)\n"
        f"TP (1R): {round(trade['tp'], 6)}\n\n"
        f"Leverage (info): {trade['leverage']}x\n"
        f"{likely_line}"
        f"{('Note: ' + trade['extra_note'] + chr(10)) if trade.get('extra_note') else ''}"
        f"Time: {ts}\n\n"
        "⚠️ Info only. Not financial advice. No execution."
    )

    send_telegram(msg)
    log.info(f"Signal sent → {trade['ex_name']} {trade['symbol']} {trade['direction']} [{trade['strategy']}]")

    k = f"{trade['ex_name']}|{trade['symbol']}|{trade['direction']}|{trade['strategy']}|{int(time.time())}"
    with open_trades_lock:
        open_trades[k] = trade

def send_safety_net_reco(trade: dict):
    entry = float(trade.get("entry", 0.0))
    direction = trade.get("direction", "LONG")
    be = breakeven_stop(entry, direction, BE_BUFFER_BPS)

    msg = (
        "🛡️ SAFETY NET (recommendation)\n"
        f"- Consider moving stop → BE+buffer ({BE_BUFFER_BPS:g}bps): {round(be, 6)}\n"
    )

    likely = float(trade.get("tp_likely", 0.0) or 0.0)
    likely_label = trade.get("tp_likely_label", "") or ""
    if likely > 0 and likely_label:
        msg += f"- If continuing, next likely tag: {likely_label} @ {round(likely, 6)}\n"

    msg += "⚠️ Info only. Not financial advice."
    send_telegram(msg)

# ======================================================
# TRACKER LOOP (TP / SL / TIME EXIT)
# ======================================================

MAX_TRADE_LIFETIME_SECS = int(os.getenv("MAX_TRADE_LIFETIME_SECS", 15 * 60))

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

                entry = float(t["entry"])
                stop = float(t["stop"])
                tp = float(t["tp"])
                direction = t["direction"]
                leverage = int(t["leverage"])
                elapsed = int(time.time() - int(t["start_ts"]))

                # TIME EXIT
                if elapsed >= MAX_TRADE_LIFETIME_SECS:
                    pnl = calc_profit_pct(entry, last_price, direction, leverage)
                    send_telegram(
                        f"⏱️ TIME EXIT\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Strategy: {t['strategy']}\n"
                        f"Side: {direction}\n"
                        f"Profit (full-size gross est.): {pnl:.1f}%\n"
                        f"Price: {last_price}"
                    )
                    record_closed_trade(t, outcome="TIME", exit_price=last_price, pnl_full_pct=pnl)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # STOP HIT
                stop_hit = (last_price <= stop) if direction == "LONG" else (last_price >= stop)
                if stop_hit:
                    pnl = calc_profit_pct(entry, last_price, direction, leverage)
                    send_telegram(
                        f"❌ STOP HIT\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Strategy: {t['strategy']}\n"
                        f"Side: {direction}\n"
                        f"Profit (full-size gross est.): {pnl:.1f}%\n"
                        f"Price: {last_price}"
                    )
                    record_closed_trade(t, outcome="STOP", exit_price=last_price, pnl_full_pct=pnl)
                    apply_stop_penalty(t["ex_name"], t["symbol"], direction, t["strategy"])
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # TP HIT (strict 1R)
                tp_hit = (last_price >= tp) if direction == "LONG" else (last_price <= tp)
                if tp_hit:
                    pnl_tp = calc_profit_pct(entry, tp, direction, leverage)
                    t["tp_hit"] = True
                    t["realized_pct"] = float(pnl_tp)  # strict: all out at 1R

                    send_telegram(
                        f"✅ TP HIT (1R)\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\n"
                        f"Strategy: {t['strategy']}\n"
                        f"Side: {direction}\n"
                        f"Profit (full-size gross est.): {pnl_tp:.1f}%\n"
                        f"Hit Price: {tp}"
                    )

                    # Safety net recommendations after TP hit (even though trade is "done")
                    send_safety_net_reco(t)

                    record_closed_trade(t, outcome="TP", exit_price=tp, pnl_full_pct=pnl_tp)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# SCANNER LOOP
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
                    df_1m = get_df(ex, symbol, TF_TRIGGER, limit=220)
                    df_5m = get_df(ex, symbol, TF_STRUCT, limit=240)
                    if df_1m is None or df_5m is None or len(df_1m) < 80 or len(df_5m) < 80:
                        continue

                    last_1m = df_1m.iloc[-1]
                    close_1m = float(last_1m["close"])
                    atr_1m = float(last_1m.get("atr") or 0.0)
                    if close_1m <= 0 or atr_1m <= 0 or pd.isna(atr_1m):
                        continue

                    # ======================================================
                    # 1) DEMAND ZONE LONG
                    # ======================================================
                    if ENABLE_DEMAND_ZONE:
                        dz = find_bullish_demand_zone_5m(df_5m)
                        if dz:
                            z_low, z_high, _ = dz
                            if in_zone(close_1m, z_low, z_high):
                                strat = "DEMAND_ZONE"
                                if allow_signal(ex_name, symbol, "LONG", strat):
                                    entry = close_1m
                                    stop = float(z_low) - (ZONE_STOP_PAD_ATR * atr_1m)
                                    sp = stop_pct(entry, stop)
                                    if STOP_MIN_PCT <= sp <= STOP_MAX_PCT:
                                        note = f"1m close inside DZ [{z_low:.6f}, {z_high:.6f}]"
                                        trade = build_trade(ex_name, symbol, "LONG", strat, entry, stop, df_1m, extra_note=note)
                                        if trade:
                                            send_signal(trade)

                    # ======================================================
                    # 2) PUMP -> FIB LONG
                    # ======================================================
                    if ENABLE_PUMP_FIB:
                        swing = detect_pump_swing_5m(df_5m)
                        if swing:
                            low_p, high_p, _, _, pump = swing
                            band = fib_band(low_p, high_p, FIB_A, FIB_B)
                            if band:
                                b_low, b_high = band
                                if in_zone(close_1m, b_low, b_high):
                                    strat = "PUMP_FIB"
                                    if allow_signal(ex_name, symbol, "LONG", strat):
                                        entry = close_1m
                                        stop = float(low_p) - (FIB_STOP_PAD_ATR * atr_1m)
                                        sp = stop_pct(entry, stop)
                                        if STOP_MIN_PCT <= sp <= STOP_MAX_PCT:
                                            note = f"Pump={pump*100:.1f}% | FibBand [{b_low:.6f}, {b_high:.6f}]"
                                            trade = build_trade(ex_name, symbol, "LONG", strat, entry, stop, df_1m, extra_note=note)
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
    return "NEW FUTURES SIGNAL BOT RUNNING (INFO ONLY) — DemandZone + PumpFib (1m/5m)"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
