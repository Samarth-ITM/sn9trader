"""Combine multi-source signals. Cron: 0 */6 * * *"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
LEGAL_DIR = os.path.join(BASE_DIR, "legal_trail")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def since_24h():
    return (datetime.utcnow() - timedelta(hours=24)).isoformat()


def get_tickers(conn):
    tickers = set()
    cutoff = since_24h()
    for tbl, col in [("congress_trades", "ticker"), ("ceo_trades", "ticker"),
                     ("options_flow", "ticker")]:
        rows = conn.execute(
            f"SELECT DISTINCT {col} FROM {tbl} WHERE 1=1"
        ).fetchall()
        tickers.update(r[0] for r in rows if r[0] and r[0] != "US_SCAN")
    rows = conn.execute(
        "SELECT DISTINCT token FROM whale_tx WHERE timestamp > ?",
        (str(int((datetime.utcnow() - timedelta(hours=24)).timestamp())),),
    ).fetchall()
    for (token,) in rows:
        if token:
            tickers.add(token)
    return tickers


def congress_score(conn, ticker):
    rows = conn.execute(
        """SELECT win_rate FROM congress_trades
           WHERE ticker=? AND disclosure_date >= date('now','-1 day')""",
        (ticker,),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT win_rate FROM congress_trades WHERE ticker=? AND win_rate IS NOT NULL",
            (ticker,),
        ).fetchall()
    if not rows:
        return 0.0, []
    rates = [r[0] for r in rows if r[0] is not None]
    if not rates:
        return 0.0, []
    avg = sum(rates) / len(rates)
    mult = 1.0 if len(rates) == 1 else 1.0
    return avg * mult, [f"congress({len(rates)})"]


def ceo_score(conn, ticker):
    rows = conn.execute(
        """SELECT win_rate, name FROM ceo_trades
           WHERE ticker=? AND filing_date >= date('now','-1 day')""",
        (ticker,),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT win_rate, name FROM ceo_trades WHERE ticker=? AND win_rate IS NOT NULL",
            (ticker,),
        ).fetchall()
    if not rows:
        return 0.0, []
    rates = [r[0] for r in rows if r[0] is not None]
    insiders = len({r[1] for r in rows})
    if not rates:
        return 0.0, []
    avg = sum(rates) / len(rates)
    mult = 2.0 if insiders > 1 else 1.0
    return avg * mult, [f"ceo({insiders})"]


def whale_score(conn, ticker):
    rows = conn.execute(
        """SELECT direction FROM whale_tx
           WHERE token=? ORDER BY timestamp DESC LIMIT 5""",
        (ticker,),
    ).fetchall()
    if not rows:
        return 0.0, []
    dirs = [r[0] for r in rows]
    if "exchange_to_wallet" in dirs:
        return 0.7, ["whale_accumulation"]
    if "wallet_to_exchange" in dirs:
        return 0.3, ["whale_distribution"]
    return 0.0, []


def options_score(conn, ticker):
    rows = conn.execute(
        """SELECT sentiment, oi_change FROM options_flow
           WHERE ticker IN (?, 'NIFTY', 'US_SCAN')
           ORDER BY detected_at DESC LIMIT 5""",
        (ticker,),
    ).fetchall()
    for sentiment, oi in rows:
        if sentiment == "bullish" or (oi and oi > 0.3):
            return 0.8, ["call_oi_spike"]
        if sentiment == "bearish" or (oi and oi < -0.3):
            return 0.2, ["put_oi_spike"]
    return 0.0, []


def apply_social_penalty(conn, ticker, confidence, sources):
    if "congress" not in str(sources) and "ceo" not in str(sources):
        return confidence
    neg = conn.execute(
        """SELECT COUNT(*) FROM social_accuracy
           WHERE ticker=? AND accuracy_score < 0.3
           AND timestamp >= datetime('now','-7 days')""",
        (ticker,),
    ).fetchone()[0]
    if neg >= 2:
        return max(0, confidence - 10)
    return confidence


def build_legal_trail(ticker, confidence, sources, hold_days):
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")
    path = os.path.join(LEGAL_DIR, f"{ticker}_{ts}.json")
    payload = {
        "timestamp_utc": datetime.utcnow().isoformat(),
        "ticker": ticker,
        "confidence_score": confidence,
        "source_urls": [
            "https://housestockwatcher.com/",
            "https://www.sec.gov/edgar/",
            "https://etherscan.io/",
            "https://www.nseindia.com/option-chain",
        ],
        "reasoning_text": f"Composite signal from {', '.join(sources)}",
        "suggested_hold_days": hold_days,
    }
    os.makedirs(LEGAL_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    created = 0
    with sqlite3.connect(DB_PATH) as conn:
        for ticker in get_tickers(conn):
            c_s, c_src = congress_score(conn, ticker)
            e_s, e_src = ceo_score(conn, ticker)
            w_s, w_src = whale_score(conn, ticker)
            o_s, o_src = options_score(conn, ticker)
            sources = c_src + e_src + w_src + o_src
            num_sources = sum(1 for s in (c_s, e_s, w_s, o_s) if s > 0)
            if num_sources == 0:
                continue

            base = c_s * 0.35 + e_s * 0.30 + w_s * 0.20 + o_s * 0.15
            if num_sources > 1:
                base *= 1 + (num_sources - 1) * 0.15
            confidence = min(100, base * 100)
            confidence = apply_social_penalty(conn, ticker, confidence, sources)

            if confidence <= 70:
                continue

            hold = 90 if c_s > e_s else (180 if e_s > 0 else 30)
            trail = build_legal_trail(ticker, confidence, sources, hold)
            conn.execute(
                """INSERT INTO signals
                   (ticker,confidence,sources_json,created_at,suggested_hold,legal_trail_path)
                   VALUES (?,?,?,?,?,?)""",
                (ticker, confidence, json.dumps(sources),
                 datetime.utcnow().isoformat(), hold, trail),
            )
            created += 1
            log.info("Signal %s confidence=%.1f", ticker, confidence)
            try:
                from telegram_send import send_signal_sync
                send_signal_sync({
                    "ticker": ticker, "confidence": confidence,
                    "sources_list": sources, "hold_days": hold,
                    "legal_trail_path": trail,
                })
            except Exception as exc:
                log.warning("Telegram send skipped: %s", exc)
        conn.commit()
    print(f"Signal combiner: {created} signals created")


if __name__ == "__main__":
    main()
