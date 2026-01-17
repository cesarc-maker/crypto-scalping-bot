# ======================================================
# BIG MOVE CATCHER — LONG + SHORT (INFO ONLY • NO EXECUTION)
# OKX + KUCOIN FUTURES • TOP MOVERS • 5m expansion • 1m retest trigger
#
# GOAL:
# - Only trade BIG moves: 5%–6% price expansion (per coin) over the last N 5m candles
# - Enter on breakout/breakdown -> retest -> reclaim (confirmed by 1m CLOSE)
# - Use RSI bias:
#     RSI(5m) <= 30  => allow LONG setups
#     RSI(5m) >= 70  => allow SHORT setups
#   (Opposite direction is blocked.)
#
# RISK / EXIT (TRACKING ONLY):
# - TP1 = strict 1R (partial) + runner trail (Chandelier ATR)
# - Stop = beyond retest extreme +/- ATR pad
# - Time exit
# - After every 20 CLOSED trades: report win rate / loss rate
#
# ⚠️ INFO ONLY. NOT FINANCIAL ADVICE. NO EXECUTION.
# ======================================================

import os
import time
import threading
import logging
from datetime import datetime, timezone

import ccxt
import pandas as pd
import requests
from flask import Flask

# ======================================================
# LOGGING
# ======================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("BIG_MOVE_CATCHER")

# ======================================================
# CONFIG (env vars)
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

# Timing
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 12))
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 5))

# Universe
PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 160))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 18))

# Filters (liquidity / spread)
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 8_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 15))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"

# Exchanges
EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]

# TFs
TF_STRUCT = os.getenv("TF_STRUCT", "5m")
TF_TRIGGER = os.getenv("TF_TRIGGER", "1m")

# Indicators
EMA_LEN = int(os.getenv("EMA_LEN", 20))
ATR_LEN = int(os.getenv("ATR_LEN", 14))
RSI_LEN = int(os.getenv("RSI_LEN", 14))

# BIG MOVE expansion (5–6%)
EXP_BARS_5M = int(os.getenv("EXP_BARS_5M", 6))          # 6x5m = 30 minutes
EXP_MIN_PCT = float(os.getenv("EXP_MIN_PCT", 0.05))     # 5%
EXP_MAX_PCT = float(os.getenv("EXP_MAX_PCT", 0.06))     # 6%

# RSI bias (HARD gate)
RSI_LONG_MAX = float(os.getenv("RSI_LONG_MAX", 30))     # <=30 => LONG allowed
RSI_SHORT_MIN = float(os.getenv("RSI_SHORT_MIN", 70))   # >=70 => SHORT allowed

# Entry tolerances
BREAKOUT_BUFFER_PCT = float(os.getenv("BREAKOUT_BUFFER_PCT", 0.0010))  # 0.10%
RETEST_TOL_PCT = float(os.getenv("RETEST_TOL_PCT", 0.0020))            # 0.20%
SETUP_EXPIRY_SECS = int(os.getenv("SETUP_EXPIRY_SECS", 2 * 60 * 60))   # 2 hours

# Risk / stop guardrails
STOP_ATR_PAD = float(os.getenv("STOP_ATR_PAD", 0.80))   # pad beyond retest extreme by 0.8x ATR(1m)
STOP_MIN_PCT = float(os.getenv("STOP_MIN_PCT", 0.30))
STOP_MAX_PCT = float(os.getenv("STOP_MAX_PCT", 2.50))

# TP / runner
TP1_ALLOC_PCT = int(os.getenv("TP1_ALLOC_PCT", 30))
TRAIL_ATR_MULT = float(os.getenv("TRAIL_ATR_MULT", 4.0))  # bigger moves need room
BE_BUFFER_BPS = float(os.getenv("BE_BUFFER_BPS", 8.0))

# "Leverage" info only
LEV_INFO = int(os.getenv("LEV_INFO", 50))

# Cooldowns
DUP_WINDOW = int(os.getenv("DUP_WINDOW", 900))
STOP_PENALTY_WINDOW = int(os.getenv("STOP_PENALTY_WINDOW", 3600))

# Time exit
MAX_TRADE_LIFETIME_SECS = int(os.getenv("MAX_TRADE_LIFETIME_SECS", 30 * 60))

# Performance report
REPORT_EVERY_N = int(os.getenv("REPORT_EVERY_N", 20))

# ======================================================
# STATE
# ======================================================

cooldown_lock = threading.Lock()
recent_signals = {}
penalty_cooldowns = {}

setups_lock = threading.Lock()
# setups key: ex|symbol|dir
# value: {stage, level, created_ts, exp_pct, rsi5, retest_extreme}
setups = {}

open_trades_lock = threading.Lock()
open_trades = {}

closed_lock = threading.Lock()
closed_trades = []  # list of dicts

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
    chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]

    for cid in CHAT_IDS:
        for ch in chunks:
            try:
                url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
                resp = requests.post(url, json={"chat_id": cid, "text": ch}, timeout=10)
                if resp.status_code != 200:
                    log.error(f"Telegram send failed ({resp.status_code}) {cid}: {resp.text[:200]}")
            except Exception as e:
                log.error(f"Telegram error {cid}: {e}")

def send_startup():
    msg = (
        "✅ BIG MOVE CATCHER (INFO ONLY)\n\n"
        f"TFs: {TF_STRUCT} structure / {TF_TRIGGER} trigger\n"
        f"Big move (5m): {EXP_MIN_PCT*100:.1f}%–{EXP_MAX_PCT*100:.1f}% over last {EXP_BARS_5M} candles\n"
        "Entry: breakout/breakdown → retest → reclaim (1m CLOSE)\n"
        f"RSI gate (5m): LONG if RSI≤{RSI_LONG_MAX:g}, SHORT if RSI≥{RSI_SHORT_MIN:g}\n"
        f"Stop pad: retest extreme ± {STOP_ATR_PAD:g}×ATR(1m) | stop window {STOP_MIN_PCT:.2f}%–{STOP_MAX_PCT:.2f}%\n"
        f"TP1: 1R ({TP1_ALLOC_PCT}%) + runner trail {TRAIL_ATR_MULT:g}×ATR(1m)\n"
        f"Leverage (info): {LEV_INFO}x\n"
        f"Report: every {REPORT_EVERY_N} closed trades\n\n"
        "⚠️ Info only. Not financial advice. No execution."
    )
    send_telegram(msg)

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

    # "magnet" levels for runner hints
    df["bb_mid"] = df["close"].rolling(20).mean()
    vwap_len = 60
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = tp * df["volume"]
    df["vwap"] = pv.rolling(vwap_len).sum() / (df["volume"].rolling(vwap_len).sum() + 1e-12)

    return df

def get_df(ex, symbol: str, tf: str, limit: int = 240):
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = df[c].astype(float)
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
            bid = float(bid); ask = float(ask)
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
# COOLDOWNS
# ======================================================

def _sig_key(ex_name: str, symbol: str, direction: str) -> str:
    return f"{ex_name}_{symbol}_{direction}_BIGMOVE"

def allow_signal(ex_name: str, symbol: str, direction: str) -> bool:
    now = time.time()
    key = _sig_key(ex_name, symbol, direction)

    with cooldown_lock:
        pen = penalty_cooldowns.get(key)
        if pen and now < pen:
            return False
        last = recent_signals.get(key)
        if last is None or (now - last) > DUP_WINDOW:
            recent_signals[key] = now
            return True
    return False

def apply_stop_penalty(ex_name: str, symbol: str, direction: str):
    now = time.time()
    key = _sig_key(ex_name, symbol, direction)
    with cooldown_lock:
        penalty_cooldowns[key] = now + STOP_PENALTY_WINDOW
        recent_signals[key] = now

# ======================================================
# BIG MOVE DETECTION (5–6% window)
# ======================================================

def detect_big_move(df5: pd.DataFrame):
    """
    Returns:
      (direction, level, exp_pct_signed, rsi5)
      direction: "LONG" for pump, "SHORT" for dump, "" for none
      level: breakout (LONG=max high) or breakdown (SHORT=min low) within window
    """
    if df5 is None or len(df5) < (EXP_BARS_5M + 30):
        return ("", 0.0, 0.0, 50.0)

    n = EXP_BARS_5M
    start = float(df5["close"].iloc[-(n+1)])
    end = float(df5["close"].iloc[-1])
    if start <= 0:
        return ("", 0.0, 0.0, 50.0)

    move = (end - start) / start  # signed
    rsi5 = float(df5["rsi"].iloc[-1]) if not pd.isna(df5["rsi"].iloc[-1]) else 50.0

    window = df5.iloc[-(n+1):]

    # PUMP 5–6%
    if move >= EXP_MIN_PCT and move <= EXP_MAX_PCT:
        level = float(window["high"].max())
        return ("LONG", level, move, rsi5)

    # DUMP 5–6%
    if move <= -EXP_MIN_PCT and abs(move) <= EXP_MAX_PCT:
        level = float(window["low"].min())
        return ("SHORT", level, move, rsi5)

    return ("", 0.0, move, rsi5)

def rsi_gate(direction: str, rsi5: float) -> bool:
    if direction == "LONG":
        return rsi5 <= RSI_LONG_MAX
    if direction == "SHORT":
        return rsi5 >= RSI_SHORT_MIN
    return False

# ======================================================
# ENTRY RULES (1m CLOSE)
# ======================================================

def above_level(close_px: float, level: float) -> bool:
    return close_px > level * (1.0 + BREAKOUT_BUFFER_PCT)

def below_level(close_px: float, level: float) -> bool:
    return close_px < level * (1.0 - BREAKOUT_BUFFER_PCT)

def retest_long(close_px: float, level: float) -> bool:
    return close_px <= level * (1.0 + RETEST_TOL_PCT)

def retest_short(close_px: float, level: float) -> bool:
    return close_px >= level * (1.0 - RETEST_TOL_PCT)

# ======================================================
# TRADE MATH
# ======================================================

def stop_pct(entry: float, stop: float) -> float:
    if entry <= 0:
        return 999.0
    return abs(entry - stop) / entry * 100.0

def tp_1r(entry: float, stop: float, direction: str) -> float:
    R = abs(entry - stop)
    if R <= 0:
        return 0.0
    return entry + R if direction == "LONG" else entry - R

def breakeven_stop(entry: float, direction: str, buffer_bps: float) -> float:
    if entry <= 0:
        return 0.0
    buf = entry * (buffer_bps / 10_000.0)
    return entry + buf if direction == "LONG" else entry - buf

def likely_magnet(df_1m: pd.DataFrame, direction: str, entry: float):
    if df_1m is None or len(df_1m) < 60:
        return ("", 0.0)

    last = df_1m.iloc[-1]
    vwap = float(last.get("vwap") or 0.0)
    bbm = float(last.get("bb_mid") or 0.0)
    ema = float(last.get("ema") or 0.0)

    cands = []
    if direction == "LONG":
        if vwap > entry: cands.append(("VWAP", vwap))
        if bbm > entry: cands.append(("BB Mid", bbm))
        if ema > entry: cands.append(("EMA", ema))
    else:
        if 0 < vwap < entry: cands.append(("VWAP", vwap))
        if 0 < bbm < entry: cands.append(("BB Mid", bbm))
        if 0 < ema < entry: cands.append(("EMA", ema))

    if not cands:
        return ("", 0.0)

    return min(cands, key=lambda x: abs(x[1] - entry))

# ======================================================
# PERFORMANCE REPORT
# ======================================================

def send_report():
    with closed_lock:
        if not closed_trades:
            return
        sample = closed_trades[-REPORT_EVERY_N:] if len(closed_trades) >= REPORT_EVERY_N else closed_trades[:]

    wins = sum(1 for r in sample if r["outcome"] == "WIN")
    losses = len(sample) - wins
    avg_r = sum(r["r_mult"] for r in sample) / len(sample)

    msg = (
        f"📊 REPORT (last {len(sample)} closed)\n\n"
        f"Wins: {wins} | Losses: {losses}\n"
        f"Win rate: {wins/len(sample)*100:.1f}% | Loss rate: {losses/len(sample)*100:.1f}%\n"
        f"Avg R (approx): {avg_r:.2f}R\n\n"
        "Notes: tracking ignores fees/slippage."
    )
    send_telegram(msg)

def record_closed(trade: dict, outcome: str, exit_price: float):
    entry = float(trade["entry"])
    stop0 = float(trade["stop0"])
    direction = trade["direction"]

    R = abs(entry - stop0)
    r_mult = 0.0
    if R > 0:
        if direction == "LONG":
            r_mult = (exit_price - entry) / R
        else:
            r_mult = (entry - exit_price) / R

    row = {
        "ts": int(time.time()),
        "symbol": trade["symbol"],
        "ex": trade["ex_name"],
        "direction": direction,
        "outcome": outcome,   # WIN/LOSS
        "r_mult": float(r_mult),
        "tp1_hit": bool(trade.get("tp1_hit", False)),
    }

    with closed_lock:
        closed_trades.append(row)
        n = len(closed_trades)

    if n % REPORT_EVERY_N == 0:
        send_report()

# ======================================================
# TRADE BUILD + SIGNAL SEND
# ======================================================

def build_trade(ex_name: str, symbol: str, direction: str, level: float, df_1m: pd.DataFrame, retest_extreme: float):
    last = df_1m.iloc[-1]
    entry = float(last["close"])
    atr = float(last.get("atr") or 0.0)
    if entry <= 0 or atr <= 0 or pd.isna(atr):
        return None

    if direction == "LONG":
        stop0 = float(retest_extreme) - STOP_ATR_PAD * atr
    else:
        stop0 = float(retest_extreme) + STOP_ATR_PAD * atr

    sp = stop_pct(entry, stop0)
    if sp < STOP_MIN_PCT or sp > STOP_MAX_PCT:
        return None

    tp1 = tp_1r(entry, stop0, direction)
    if tp1 <= 0:
        return None

    lab, mag = likely_magnet(df_1m, direction, entry)

    now = int(time.time())
    return {
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": direction,
        "level": float(level),
        "entry": float(entry),
        "stop0": float(stop0),
        "tp1": float(tp1),
        "tp1_hit": False,
        "runner_peak_or_trough": float(entry),  # LONG uses peak, SHORT uses trough
        "runner_stop": float(stop0),
        "magnet_label": lab,
        "magnet_px": float(mag) if mag else 0.0,
        "created_ts": now,
        "start_ts": now,
    }

def send_signal(trade: dict, exp_pct: float, rsi5: float):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sp = stop_pct(trade["entry"], trade["stop0"])

    magnet_line = ""
    if trade.get("magnet_label") and trade.get("magnet_px", 0) > 0:
        magnet_line = f"🎯 Likely magnet: {trade['magnet_label']} @ {round(trade['magnet_px'], 6)}\n"

    msg = (
        f"🔥 BIG MOVE SIGNAL — {trade['direction']} (INFO ONLY)\n\n"
        f"Exchange: {trade['ex_name']}\n"
        f"Pair: {trade['symbol']}\n"
        f"Expansion(5m): {exp_pct*100:.1f}% | RSI(5m): {rsi5:.1f}\n"
        f"Level: {round(trade['level'], 6)}\n"
        f"Model: Breakout/Breakdown → Retest → Reclaim (1m CLOSE)\n\n"
        f"Entry: {round(trade['entry'], 6)}\n"
        f"Stop:  {round(trade['stop0'], 6)} ({sp:.2f}%)\n"
        f"TP1:   {round(trade['tp1'], 6)} (1R, {TP1_ALLOC_PCT}%)\n"
        f"Runner trail: {TRAIL_ATR_MULT:g}×ATR(1m)\n"
        f"Leverage (info): {LEV_INFO}x\n\n"
        f"{magnet_line}"
        f"Time: {ts}\n\n"
        "⚠️ Info only. Not financial advice."
    )
    send_telegram(msg)

# ======================================================
# TRACKER LOOP (TP1 + runner trail + stop + time)
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
                px = float(ticker.get("last") or ticker.get("close") or 0)
                if px <= 0:
                    continue

                direction = t["direction"]
                entry = float(t["entry"])
                stop0 = float(t["stop0"])
                tp1 = float(t["tp1"])

                elapsed = int(time.time() - int(t["start_ts"]))
                if elapsed >= MAX_TRADE_LIFETIME_SECS:
                    send_telegram(
                        f"⏱️ TIME EXIT\n\nPair: {t['symbol']} ({t['ex_name']})\nSide: {direction}\nPrice: {px}\n⚠️ Info only."
                    )
                    outcome = "WIN" if (px > entry if direction == "LONG" else px < entry) else "LOSS"
                    record_closed(t, outcome, px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # stop before TP1
                stop_hit = (px <= stop0) if direction == "LONG" else (px >= stop0)
                if stop_hit and not t.get("tp1_hit", False):
                    send_telegram(
                        f"❌ STOP HIT\n\nPair: {t['symbol']} ({t['ex_name']})\nSide: {direction}\nPrice: {px}\n⚠️ Info only."
                    )
                    apply_stop_penalty(t["ex_name"], t["symbol"], direction)
                    record_closed(t, "LOSS", px)
                    with open_trades_lock:
                        open_trades.pop(k, None)
                    continue

                # TP1 hit
                tp_hit = (px >= tp1) if direction == "LONG" else (px <= tp1)
                if not t.get("tp1_hit", False) and tp_hit:
                    t["tp1_hit"] = True
                    be = breakeven_stop(entry, direction, BE_BUFFER_BPS)
                    send_telegram(
                        f"✅ TP1 HIT (1R) — {TP1_ALLOC_PCT}%\n\n"
                        f"Pair: {t['symbol']} ({t['ex_name']})\nSide: {direction}\nHit: {tp1}\n\n"
                        f"🛡️ Safety net (recommendation): consider stop → BE+buffer ({BE_BUFFER_BPS:g}bps) = {round(be, 6)}\n"
                        f"{('🎯 Likely magnet: ' + t['magnet_label'] + ' @ ' + str(round(t['magnet_px'], 6))) if t.get('magnet_label') and t.get('magnet_px',0)>0 else ''}\n"
                        "⚠️ Info only."
                    )
                    with open_trades_lock:
                        if k in open_trades:
                            open_trades[k]["tp1_hit"] = True

                # runner trail (only meaningful after TP1)
                if t.get("tp1_hit", False):
                    df_1m = get_df(ex, t["symbol"], TF_TRIGGER, limit=90)
                    if df_1m is None or len(df_1m) < 30:
                        continue
                    atr = float(df_1m.iloc[-1].get("atr") or 0.0)
                    if atr <= 0 or pd.isna(atr):
                        continue

                    if direction == "LONG":
                        peak = max(float(t.get("runner_peak_or_trough", entry)), px)
                        new_stop = peak - TRAIL_ATR_MULT * atr
                        # never loosen downward
                        t["runner_peak_or_trough"] = peak
                        t["runner_stop"] = max(float(t.get("runner_stop", stop0)), new_stop, stop0)

                        if px <= float(t["runner_stop"]):
                            send_telegram(
                                f"🏁 RUNNER STOP HIT\n\nPair: {t['symbol']} ({t['ex_name']})\nSide: LONG\nRunner stop: {round(t['runner_stop'], 6)}\nExit: {px}\n⚠️ Info only."
                            )
                            record_closed(t, "WIN", px)  # TP1 hit => treat as WIN
                            with open_trades_lock:
                                open_trades.pop(k, None)
                            continue

                    else:
                        trough = min(float(t.get("runner_peak_or_trough", entry)), px)
                        new_stop = trough + TRAIL_ATR_MULT * atr
                        # never loosen upward (for shorts, stop should move down, so we take min)
                        t["runner_peak_or_trough"] = trough
                        t["runner_stop"] = min(float(t.get("runner_stop", stop0)), new_stop, stop0)

                        if px >= float(t["runner_stop"]):
                            send_telegram(
                                f"🏁 RUNNER STOP HIT\n\nPair: {t['symbol']} ({t['ex_name']})\nSide: SHORT\nRunner stop: {round(t['runner_stop'], 6)}\nExit: {px}\n⚠️ Info only."
                            )
                            record_closed(t, "WIN", px)
                            with open_trades_lock:
                                open_trades.pop(k, None)
                            continue

                    with open_trades_lock:
                        if k in open_trades:
                            open_trades[k]["runner_peak_or_trough"] = t["runner_peak_or_trough"]
                            open_trades[k]["runner_stop"] = t["runner_stop"]

            except Exception as e:
                log.error(f"Tracker error {k}: {e}")

# ======================================================
# SCANNER LOOP (setups + state machine)
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
                    df_5m = get_df(ex, symbol, TF_STRUCT, limit=240)
                    df_1m = get_df(ex, symbol, TF_TRIGGER, limit=200)
                    if df_5m is None or df_1m is None or len(df_5m) < 80 or len(df_1m) < 80:
                        continue

                    close_1m = float(df_1m.iloc[-1]["close"])
                    if close_1m <= 0:
                        continue

                    # 1) detect fresh big move
                    direction, level, exp_pct, rsi5 = detect_big_move(df_5m)
                    if direction and level > 0 and rsi_gate(direction, rsi5):
                        skey = f"{ex_name}|{symbol}|{direction}"
                        now = int(time.time())
                        with setups_lock:
                            if skey not in setups:
                                setups[skey] = {
                                    "stage": "WAIT_BREAK" if direction == "LONG" else "WAIT_BREAK",
                                    "level": float(level),
                                    "created_ts": now,
                                    "exp_pct": float(exp_pct),
                                    "rsi5": float(rsi5),
                                    "retest_extreme": None,  # LONG retest_low, SHORT retest_high
                                }
                            else:
                                # refresh metadata & level
                                setups[skey]["level"] = float(level)
                                setups[skey]["exp_pct"] = float(exp_pct)
                                setups[skey]["rsi5"] = float(rsi5)

                    # 2) expire setups
                    now = int(time.time())
                    with setups_lock:
                        for k in list(setups.keys()):
                            if now - int(setups[k].get("created_ts", now)) > SETUP_EXPIRY_SECS:
                                setups.pop(k, None)

                    # 3) run state machines for both directions if setup exists
                    for dirx in ("LONG", "SHORT"):
                        skey = f"{ex_name}|{symbol}|{dirx}"
                        with setups_lock:
                            s = setups.get(skey)
                        if not s:
                            continue

                        level = float(s["level"])
                        stage = s.get("stage", "WAIT_BREAK")

                        # Stage A: WAIT_BREAK (breakout/breakdown)
                        if stage == "WAIT_BREAK":
                            if dirx == "LONG" and above_level(close_1m, level):
                                with setups_lock:
                                    setups[skey]["stage"] = "WAIT_RETEST"
                            elif dirx == "SHORT" and below_level(close_1m, level):
                                with setups_lock:
                                    setups[skey]["stage"] = "WAIT_RETEST"

                        # Stage B: WAIT_RETEST
                        elif stage == "WAIT_RETEST":
                            if dirx == "LONG" and retest_long(close_1m, level):
                                recent = df_1m.iloc[-12:]
                                retest_low = float(recent["low"].min())
                                with setups_lock:
                                    setups[skey]["stage"] = "WAIT_RECLAIM"
                                    setups[skey]["retest_extreme"] = retest_low

                            elif dirx == "SHORT" and retest_short(close_1m, level):
                                recent = df_1m.iloc[-12:]
                                retest_high = float(recent["high"].max())
                                with setups_lock:
                                    setups[skey]["stage"] = "WAIT_RECLAIM"
                                    setups[skey]["retest_extreme"] = retest_high

                        # Stage C: WAIT_RECLAIM (signal)
                        elif stage == "WAIT_RECLAIM":
                            ret_ext = float(s.get("retest_extreme") or 0.0)
                            if ret_ext <= 0:
                                recent = df_1m.iloc[-12:]
                                ret_ext = float(recent["low"].min()) if dirx == "LONG" else float(recent["high"].max())
                                with setups_lock:
                                    setups[skey]["retest_extreme"] = ret_ext

                            trigger = (above_level(close_1m, level) if dirx == "LONG" else below_level(close_1m, level))
                            if trigger and allow_signal(ex_name, symbol, dirx):
                                trade = build_trade(ex_name, symbol, dirx, level, df_1m, ret_ext)
                                if trade:
                                    send_signal(trade, exp_pct=float(s.get("exp_pct", 0.0)), rsi5=float(s.get("rsi5", 50.0)))

                                    tk = f"{ex_name}|{symbol}|{dirx}|{int(time.time())}"
                                    with open_trades_lock:
                                        open_trades[tk] = trade

                                # clear setup after firing
                                with setups_lock:
                                    setups.pop(skey, None)

                except Exception as e:
                    log.error(f"Scanner error {ex_name} {symbol}: {e}")

        time.sleep(SCAN_INTERVAL)

# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)

@app.route("/")
def home():
    return "BIG MOVE CATCHER RUNNING (INFO ONLY) — 5–6% expansion + RSI gate + 1m retest"

if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
