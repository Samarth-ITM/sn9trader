"""Paper trading engine. Cron: 0 22 * * * (daily) + every 6h for entries"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
POSITION_SIZE = 10_000
SLIP_BUY = 1.001
SLIP_SELL = 0.999
RISK_FREE = 0.05

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def get_price(ticker):
    t = ticker.replace(".", "-")
    try:
        data = yf.Ticker(t).history(period="1d")
        if data.empty:
            return None
        return float(data["Close"].iloc[-1])
    except Exception:
        return None


def open_positions(conn):
    rows = conn.execute(
        """SELECT s.id, s.ticker, s.suggested_hold, s.created_at
           FROM signals s
           WHERE s.paper_status IS NULL"""
    ).fetchall()
    opened = 0
    for sid, ticker, hold, created in rows:
        price = get_price(ticker)
        if not price:
            continue
        entry = price * SLIP_BUY
        shares = POSITION_SIZE / entry
        conn.execute(
            """INSERT INTO paper_portfolio
               (signal_id,entry_price,entry_date,shares) VALUES (?,?,?,?)""",
            (sid, entry, datetime.utcnow().isoformat(), shares),
        )
        conn.execute("UPDATE signals SET paper_status='open' WHERE id=?", (sid,))
        opened += 1
        log.info("Opened %s @ %.2f", ticker, entry)
    conn.commit()
    return opened


def close_positions(conn):
    rows = conn.execute(
        """SELECT pp.signal_id, pp.entry_price, pp.entry_date, pp.shares, s.ticker, s.suggested_hold
           FROM paper_portfolio pp
           JOIN signals s ON s.id = pp.signal_id
           WHERE pp.exit_date IS NULL AND s.paper_status='open'"""
    ).fetchall()
    closed = 0
    for sid, entry, entry_date, shares, ticker, hold in rows:
        try:
            entry_dt = datetime.fromisoformat(entry_date)
        except ValueError:
            continue
        if datetime.utcnow() < entry_dt + timedelta(days=hold or 90):
            continue
        price = get_price(ticker)
        if not price:
            continue
        exit_p = price * SLIP_SELL
        pnl = (exit_p - entry) * shares
        conn.execute(
            """UPDATE paper_portfolio SET exit_price=?, exit_date=?, pnl=?
               WHERE signal_id=? AND exit_date IS NULL""",
            (exit_p, datetime.utcnow().isoformat(), pnl, sid),
        )
        conn.execute("UPDATE signals SET paper_status='closed' WHERE id=?", (sid,))
        closed += 1
        log.info("Closed %s pnl=%.2f", ticker, pnl)
    conn.commit()
    return closed


def weekly_report(conn):
    if datetime.utcnow().weekday() != 0:  # Monday
        return None
    rows = conn.execute(
        "SELECT pnl, entry_date, exit_date FROM paper_portfolio WHERE pnl IS NOT NULL"
    ).fetchall()
    if not rows:
        return None
    pnls = [r[0] for r in rows]
    total_return = sum(pnls) / POSITION_SIZE / max(len(pnls), 1) * 100
    rets = pd.Series(pnls) / POSITION_SIZE
    sharpe = (rets.mean() - RISK_FREE / 52) / (rets.std() + 1e-9) * np.sqrt(52)
    cum = rets.cumsum()
    max_dd = float((cum - cum.cummax()).min()) * 100
    try:
        spy = yf.download("SPY", period="3mo", progress=False, auto_adjust=True)
        spy_ret = (float(spy["Close"].iloc[-1]) / float(spy["Close"].iloc[0]) - 1) * 100
    except Exception:
        spy_ret = 0.0
    report = {
        "total_return_pct": round(total_return, 2),
        "sharpe": round(float(sharpe), 2),
        "max_drawdown_pct": round(max_dd, 2),
        "vs_spy_pct": round(total_return - spy_ret, 2),
        "trades": len(pnls),
    }
    return report


def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    with sqlite3.connect(DB_PATH) as conn:
        opened = open_positions(conn)
        closed = close_positions(conn)
        report = weekly_report(conn)
    print(f"Paper trader: opened={opened} closed={closed}")
    if report:
        print(f"Weekly report: {report}")
        try:
            from telegram_send import send_signal_sync
            send_signal_sync({
                "ticker": "PORTFOLIO",
                "confidence": 0,
                "sources_list": [f"return={report['total_return_pct']}%",
                                 f"sharpe={report['sharpe']}",
                                 f"max_dd={report['max_drawdown_pct']}%",
                                 f"vs_spy={report['vs_spy_pct']}%"],
                "hold_days": 0,
                "legal_trail_path": "",
            })
        except Exception as exc:
            log.warning("Report telegram skipped: %s", exc)


if __name__ == "__main__":
    main()
