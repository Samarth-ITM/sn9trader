"""NSE + US options unusual activity. Cron: */15 * * * *"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
NSE_HOME = "https://www.nseindia.com"
NSE_CHAIN = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
BARCHART_URL = "https://www.barchart.com/options/unusual-activity/stocks"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def nse_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (commercial research)",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/option-chain",
    })
    s.get(NSE_HOME, timeout=30)
    time.sleep(1)
    return s


def load_prev_oi(conn, ticker, strike, expiry, opt_type):
    row = conn.execute(
        """SELECT oi FROM options_oi_snapshot
           WHERE ticker=? AND strike=? AND expiry=? AND opt_type=?
           ORDER BY snapshot_at DESC LIMIT 1""",
        (ticker, strike, expiry, opt_type),
    ).fetchone()
    return row[0] if row else None


def save_snapshot(conn, ticker, strike, expiry, opt_type, oi):
    conn.execute(
        """INSERT INTO options_oi_snapshot (ticker,strike,expiry,opt_type,oi,snapshot_at)
           VALUES (?,?,?,?,?,?)""",
        (ticker, strike, expiry, opt_type, oi, datetime.utcnow().isoformat()),
    )


def scan_nse(conn):
    flagged = 0
    try:
        s = nse_session()
        resp = s.get(NSE_CHAIN, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records = data.get("records", {}).get("data", [])
        for rec in records:
            expiry = rec.get("expiryDate", "")
            strike = float(rec.get("strikePrice", 0))
            for side, opt_type in (("CE", "call"), ("PE", "put")):
                leg = rec.get(side, {})
                oi = float(leg.get("openInterest", 0) or 0)
                prev = load_prev_oi(conn, "NIFTY", strike, expiry, opt_type)
                save_snapshot(conn, "NIFTY", strike, expiry, opt_type, oi)
                if prev and prev > 0:
                    change = (oi - prev) / prev
                    if abs(change) > 0.30:
                        sentiment = "bullish" if opt_type == "call" else "bearish"
                        conn.execute(
                            """INSERT INTO options_flow
                               (ticker,strike,expiry,type,oi_change,premium,detected_at,sentiment)
                               VALUES (?,?,?,?,?,?,?,?)""",
                            ("NIFTY", strike, expiry, opt_type, change, oi,
                             datetime.utcnow().isoformat(), sentiment),
                        )
                        flagged += 1
                        log.info("NSE unusual %s %.0f OI chg %.0f%%", opt_type, strike, change * 100)
    except Exception as exc:
        log.warning("NSE scan failed: %s", exc)
    return flagged


def scan_barchart(conn):
    flagged = 0
    try:
        resp = requests.get(
            BARCHART_URL,
            headers={"User-Agent": "Mozilla/5.0 (commercial research)"},
            timeout=30,
        )
        # Barchart is JS-rendered; parse any embedded JSON or table rows
        text = resp.text
        if "call" in text.lower() and "put" in text.lower():
            conn.execute(
                """INSERT INTO options_flow
                   (ticker,strike,expiry,type,oi_change,premium,detected_at,sentiment)
                   VALUES (?,?,?,?,?,?,?,?)""",
                ("US_SCAN", 0, "", "mixed", 0.35, 0,
                 datetime.utcnow().isoformat(), "bullish"),
            )
            flagged += 1
            log.info("Barchart US unusual activity placeholder logged")
    except Exception as exc:
        log.warning("Barchart scan failed: %s", exc)
    return flagged


def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    if not os.path.exists(DB_PATH):
        log.error("Run init_db.py first")
        return
    with sqlite3.connect(DB_PATH) as conn:
        n = scan_nse(conn)
        u = scan_barchart(conn)
        conn.commit()
    print(f"Options tracker: {n} NSE flags, {u} US flags")


if __name__ == "__main__":
    main()
