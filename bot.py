import os
import time
import math
import requests
from flask import Flask, jsonify
from typing import List, Dict

# ==============================
# ENVIRONMENT VARIABLES
# ==============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
CHAT_ID2 = os.getenv("CHAT_ID2")
CHAT_IDS = os.getenv("CHAT_IDS", "")  # comma-separated

ALL_CHAT_IDS = list(
    filter(
        None,
        [CHAT_ID, CHAT_ID2] + CHAT_IDS.split(",")
    )
)

# ==============================
# FLASK HEALTH CHECK (RENDER)
# ==============================

app = Flask(__name__)

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


# ==============================
# TELEGRAM UTIL
# ==============================

def send_telegram_message(text: str):
    for chat_id in ALL_CHAT_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown"
                },
                timeout=10
            )
        except Exception as e:
            print(f"Telegram error: {e}")


# ==============================
# CONFIG — STRICT MODE
# ==============================

ATR_MULTIPLIER = 1.5
VOLUME_MULTIPLIER = 1.5

MAX_STOP_PCT = 1.00
SCALP_STOP_MAX = 0.25

# ==============================
# CORE DATA STRUCTURES
# ==============================

class Signal:
    def __init__(
        self,
        pair: str,
        side: str,
        trade_type: str,
        risk_level: str,
        entry: List[float],
        stop: float,
        tps: List[float],
        leverage: int,
        margin_pct: float,
        reason: List[str]
    ):
        self.pair = pair
        self.side = side
        self.trade_type = trade_type
        self.risk_level = risk_level
        self.entry = entry
        self.stop = stop
        self.tps = tps
        self.leverage = leverage
        self.margin_pct = margin_pct
        self.reason = reason

    def format(self) -> str:
        tps_text = "\n".join([f"TP{i+1}: {tp}" for i, tp in enumerate(self.tps)])

        reasons = "\n".join([f"- {r}" for r in self.reason])

        return f"""
*Pair:* {self.pair}
*Side:* {self.side}
*Trade Type:* {self.trade_type}
*Risk Level:* {self.risk_level}

*Entry:* {self.entry[0]} – {self.entry[1]}
*Stop:* {self.stop}

{tps_text}

*Leverage:* {self.leverage}x
*Margin:* {self.margin_pct}%

*Reason:*
{reasons}
"""


# ==============================
# HELPER FUNCTIONS
# ==============================

def stop_pct(entry: float, stop: float) -> float:
    return abs(entry - stop) / entry * 100


def classify_trade(stop_percentage: float) -> str:
    if stop_percentage <= SCALP_STOP_MAX:
        return "SCALP"
    elif stop_percentage <= MAX_STOP_PCT:
        return "LIMIT"
    else:
        return "REJECT"


def risk_level_from_leverage(leverage: int) -> str:
    if leverage <= 20:
        return "LOW"
    elif leverage <= 40:
        return "MEDIUM"
    return "HIGH"


def leverage_from_stop(stop_percentage: float) -> int:
    if stop_percentage < 0.30:
        return 100
    if stop_percentage < 0.60:
        return 50
    if stop_percentage < 1.00:
        return 25
    return 15


# ==============================
# PLACEHOLDER: MARKET SCANNER
# ==============================

def scan_market() -> List[Signal]:
    """
    This function is where your EXISTING scanner logic plugs in.
    EMA, ATR, Volume, Structure, S/D, Movers — unchanged.
    """

    signals: List[Signal] = []

    # ---- EXAMPLE STATIC SIGNAL (REPLACE WITH REAL DATA) ----
    entry_zone = [141.60, 142.00]
    stop = 139.95
    stop_percentage = stop_pct(entry_zone[0], stop)

    trade_type = classify_trade(stop_percentage)
    if trade_type == "REJECT":
        return []

    leverage_cap = leverage_from_stop(stop_percentage)
    leverage = min(leverage_cap, 25)

    signal = Signal(
        pair="SOL/USDT",
        side="Long",
        trade_type=trade_type,
        risk_level=risk_level_from_leverage(leverage),
        entry=entry_zone,
        stop=stop,
        tps=[144.20, 145.00, 148.80] if trade_type == "LIMIT" else [144.20, 145.00],
        leverage=leverage,
        margin_pct=0.8 if trade_type == "LIMIT" else 0.3,
        reason=[
            "ATR + volume displacement confirmed",
            "Clean swing structure break",
            "50–61.8% pullback into value",
            "Clear upside liquidity targets"
        ]
    )

    signals.append(signal)
    return signals


# ==============================
# MAIN LOOP
# ==============================

def main_loop():
    send_telegram_message("🚀 *Signal Bot Started (Strict Mode)*")

    while True:
        try:
            signals = scan_market()
            for sig in signals:
                send_telegram_message(sig.format())
            time.sleep(60)  # scan interval
        except Exception as e:
            print(f"Runtime error: {e}")
            time.sleep(10)


# ==============================
# ENTRY POINT
# ==============================

if __name__ == "__main__":
    from threading import Thread
    Thread(target=main_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
