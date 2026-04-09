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
# SANITY CHECK 1M BOT
# PURPOSE:
# - verify Telegram delivery
# - verify exchange connectivity
# - verify cached OHLCV flow
# - verify scanner loop / tracker loop / Flask runtime
# - emit one test signal every new closed 1m candle
#
# THIS IS NOT A TRADING STRATEGY.
# It intentionally produces frequent signals to validate plumbing.
# ======================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("SANITY_CHECK_1M_BOT")

CT = ZoneInfo("America/Chicago")


def ct_time_str() -> str:
    return datetime.now(timezone.utc).astimezone(CT).strftime("%H:%M:%S CT")


def utc_ts() -> int:
    return int(time.time())


# ======================================================
# CONFIG
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

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
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", 5))
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", 10))

EXCHANGES = os.getenv("EXCHANGES", "okx,kucoin_futures").split(",")
EXCHANGES = [e.strip() for e in EXCHANGES if e.strip()]
EXCHANGES = [e for e in EXCHANGES if e in ("okx", "kucoin_futures")]

PAIR_LIMIT = int(os.getenv("PAIR_LIMIT", 40))
TOP_MOVER_COUNT = int(os.getenv("TOP_MOVER_COUNT", 10))
MIN_QUOTE_VOL_USDT = float(os.getenv("MIN_QUOTE_VOL_USDT", 1_000_000))
MAX_SPREAD_BPS = float(os.getenv("MAX_SPREAD_BPS", 50))
ALLOW_ONLY_ACTIVE = os.getenv("ALLOW_ONLY_ACTIVE", "1") == "1"
USE_TOP_MOVERS_ONLY = os.getenv("USE_TOP_MOVERS_ONLY", "1") == "1"

TF_SIGNAL = os.getenv("TF_SIGNAL", "1m")
OHLCV_LIMIT_1M = int(os.getenv("OHLCV_LIMIT_1M", 20))
OHLCV_1M_TTL_SEC = int(os.getenv("OHLCV_1M_TTL_SEC", 3))
UNIVERSE_TTL_SEC = int(os.getenv("UNIVERSE_TTL_SEC", 300))
MOVERS_TTL_SEC = int(os.getenv("MOVERS_TTL_SEC", 20))

REFERENCE_SYMBOL = os.getenv("REFERENCE_SYMBOL", "").strip()
ONE_SIGNAL_PER_CANDLE = os.getenv("ONE_SIGNAL_PER_CANDLE", "1") == "1"
SIGNAL_MODE = os.getenv("SIGNAL_MODE", "candle_color").strip().lower()
if SIGNAL_MODE not in ("candle_color", "alternate"):
    SIGNAL_MODE = "candle_color"

TELEGRAM_API = "https://api.telegram.org"


# ======================================================
# STATE
# ======================================================

symbol_state: Dict[str, Dict[str, Any]] = {}
last_signal_side = "SHORT"
heartbeat_trade: Optional[Dict[str, Any]] = None
heartbeat_trade_lock = threading.Lock()


# ======================================================
# TELEGRAM
# ======================================================


def send_telegram(text: str):
    if not BOT_TOKEN:
        log.error("BOT_TOKEN missing")
        return
    if not CHAT_IDS:
        log.warning("No chat IDs configured")
        return

    max_len = 3800
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]

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
    msg = (
        "🤖 SANITY CHECK 1M BOT STARTED\n\n"
        "Purpose:\n"
        "• verify Telegram delivery\n"
        "• verify exchange connection\n"
        "• verify OHLCV caching\n"
        "• verify scanner loop is alive\n\n"
        f"Exchanges: {', '.join(EXCHANGES)}\n"
        f"Signal timeframe: {TF_SIGNAL}\n"
        f"Signal mode: {SIGNAL_MODE}\n"
        f"Universe mode: {'TOP MOVERS' if USE_TOP_MOVERS_ONLY else 'QUALITY UNIVERSE'}\n"
        f"Started: {ct_time_str()}\n\n"
        "⚠️ Test bot only. Signals are intentionally noisy."
    )
    send_telegram(msg)


# ======================================================
# CACHE
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
# OHLCV
# ======================================================


def get_df_cached(ex_name: str, ex, symbol: str, tf: str, limit: int, ttl_sec: int) -> Optional[pd.DataFrame]:
    key = (ex_name, symbol, tf, limit)
    hit = ohlcv_cache.get(key)
    if hit is not None:
        return hit.copy()
    try:
        data = ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
        ohlcv_cache.set(key, df, ttl_sec)
        return df.copy()
    except Exception as e:
        log.error("Fetch error %s %s %s: %s", ex_name, symbol, tf, e)
        return None


def confirmed_df(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) <= 2:
        return df.iloc[0:0].copy()
    return df.iloc[:-1].copy()


# ======================================================
# SIGNAL GENERATION
# ======================================================


def get_state_bucket(ex_name: str, symbol: str) -> Dict[str, Any]:
    key = f"{ex_name}|{symbol}"
    if key not in symbol_state:
        symbol_state[key] = {"last_signal_ts": 0}
    return symbol_state[key]


def choose_symbol(ex_name: str, ex) -> Optional[str]:
    if REFERENCE_SYMBOL:
        return REFERENCE_SYMBOL
    symbols = detect_top_movers_from_tickers(ex_name, ex) if USE_TOP_MOVERS_ONLY else get_quality_universe(ex_name, ex)
    if not symbols:
        return None
    return symbols[0]


def choose_side(df_1m: pd.DataFrame) -> str:
    global last_signal_side
    if SIGNAL_MODE == "alternate":
        last_signal_side = "LONG" if last_signal_side == "SHORT" else "SHORT"
        return last_signal_side
    last = df_1m.iloc[-1]
    if float(last["close"]) >= float(last["open"]):
        return "LONG"
    return "SHORT"


def build_test_signal(ex_name: str, symbol: str, df_1m: pd.DataFrame) -> Dict[str, Any]:
    last = df_1m.iloc[-1]
    side = choose_side(df_1m)
    entry = float(last["close"])
    trade = {
        "trade_id": f"TEST-{symbol.replace('/', '').replace(':', '')}-{datetime.now(timezone.utc).strftime('%H%M%S')}",
        "ex_name": ex_name,
        "symbol": symbol,
        "direction": side,
        "entry": entry,
        "stop": entry * (0.998 if side == 'LONG' else 1.002),
        "tp": entry * (1.002 if side == 'LONG' else 0.998),
        "status": "ACTIVE",
        "created_ts": utc_ts(),
        "exec_ts": int(last["ts"]),
        "reason": f"sanity check 1m signal on closed candle | open={last['open']} close={last['close']}",
    }
    return trade


def send_signal(trade: Dict[str, Any]):
    emoji = "🟢" if trade["direction"] == "LONG" else "🔴"
    msg = (
        f"{emoji} SANITY SIGNAL\n"
        f"{trade['symbol']} | {trade['direction']}\n"
        f"Trade ID: {trade['trade_id']}\n\n"
        f"Entry: {trade['entry']:.6f}\n"
        f"Stop: {trade['stop']:.6f}\n"
        f"TP: {trade['tp']:.6f}\n"
        f"Reason: {trade['reason']}\n"
        f"Time: {ct_time_str()} | {trade['ex_name'].upper()}\n\n"
        "⚠️ Test bot only. Not a strategy."
    )
    send_telegram(msg)
    log.info("Sanity signal sent | %s %s %s", trade["ex_name"], trade["symbol"], trade["direction"])
    with heartbeat_trade_lock:
        global heartbeat_trade
        heartbeat_trade = trade


# ======================================================
# TRACKER LOOP
# Dummy lifecycle updates so you can see the bot is alive
# ======================================================


def tracker_loop():
    log.info("Tracker loop started.")
    while True:
        time.sleep(TRACK_INTERVAL)
        with heartbeat_trade_lock:
            trade = dict(heartbeat_trade) if heartbeat_trade else None
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
            log.info(
                "Heartbeat tracker | symbol=%s | side=%s | entry=%.6f | live=%.6f",
                trade["symbol"], trade["direction"], trade["entry"], px
            )
        except Exception as e:
            log.error("Tracker error: %s", e)


# ======================================================
# SCANNER LOOP
# ======================================================


def scanner_loop():
    send_startup()
    log.info("Scanner loop started.")

    while True:
        try:
            for ex_name in EXCHANGES:
                ex = get_ex_cached(ex_name)
                if not ex:
                    continue
                if not ensure_markets_loaded(ex_name, ex):
                    continue

                symbol = choose_symbol(ex_name, ex)
                if not symbol:
                    continue

                df_1m = get_df_cached(ex_name, ex, symbol, TF_SIGNAL, OHLCV_LIMIT_1M, OHLCV_1M_TTL_SEC)
                if df_1m is None:
                    continue
                df_1m = confirmed_df(df_1m)
                if len(df_1m) < 3:
                    continue

                st = get_state_bucket(ex_name, symbol)
                exec_ts = int(df_1m.iloc[-1]["ts"])
                if ONE_SIGNAL_PER_CANDLE and int(st.get("last_signal_ts", 0)) == exec_ts:
                    continue

                st["last_signal_ts"] = exec_ts
                trade = build_test_signal(ex_name, symbol, df_1m)
                send_signal(trade)

                # one test signal per scan cycle is enough
                break

        except Exception as e:
            log.error("Scanner loop error: %s", e)

        time.sleep(SCAN_INTERVAL)


# ======================================================
# FLASK SERVER
# ======================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Sanity check 1m bot running"


if __name__ == "__main__":
    threading.Thread(target=scanner_loop, daemon=True).start()
    threading.Thread(target=tracker_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT)
