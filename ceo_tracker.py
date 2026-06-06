"""CEO Form 4 insider purchase tracker. Cron: 0 */6 * * *"""

import csv
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
CIKS_PATH = os.path.join(BASE_DIR, "ciks.csv")
HEADERS = {"User-Agent": "Mozilla/5.0 (commercial research)"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def load_ciks():
    with open(CIKS_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_filings(cik):
    url = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        f"?action=getcompany&CIK={cik}&type=4&count=10"
    )
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_form4_links(html):
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.select("a[href*='Archives/edgar/data']"):
        href = a.get("href", "")
        if href.endswith(".xml") or "-index.htm" in href:
            links.append("https://www.sec.gov" + href if href.startswith("/") else href)
    return links[:5]


def parse_purchases(xml_text, name, ticker, cik):
    soup = BeautifulSoup(xml_text, "xml")
    if not soup.find("ownershipDocument"):
        soup = BeautifulSoup(xml_text, "html.parser")
    trades = []
    for txn in soup.find_all(re.compile("nonDerivativeTransaction|NonDerivativeTransaction")):
        code = txn.find(re.compile("transactionCoding|transactionCode"))
        code_val = ""
        if code:
            tc = code.find(re.compile("transactionCode"))
            code_val = (tc.get_text(strip=True) if tc else code.get_text(strip=True))[:1]
        if code_val != "P":
            continue
        td = txn.find(re.compile("transactionDate|value"))
        date_el = td.find("value") if td and td.find("value") else td
        txn_date = date_el.get_text(strip=True)[:10] if date_el else ""
        shares_el = txn.find(re.compile("transactionShares|transactionAmounts"))
        shares = 0.0
        if shares_el:
            v = shares_el.find("value")
            if v:
                try:
                    shares = float(v.get_text(strip=True).replace(",", ""))
                except ValueError:
                    pass
        price_el = txn.find(re.compile("transactionPricePerShare|pricePerShare"))
        price = None
        if price_el:
            v = price_el.find("value")
            if v:
                try:
                    price = float(v.get_text(strip=True).replace(",", ""))
                except ValueError:
                    pass
        if not txn_date:
            continue
        trades.append({
            "name": name, "ticker": ticker, "cik": cik,
            "transaction_date": txn_date, "filing_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "shares": shares, "price": price,
        })
    return trades


def insert_trades(conn, trades):
    n = 0
    for t in trades:
        cur = conn.execute(
            """INSERT OR IGNORE INTO ceo_trades
               (name,ticker,filing_date,transaction_date,shares,price,cik)
               VALUES (?,?,?,?,?,?,?)""",
            (t["name"], t["ticker"], t["filing_date"], t["transaction_date"],
             t["shares"], t["price"], t["cik"]),
        )
        if cur.rowcount:
            n += 1
    conn.commit()
    return n


def compute_returns(conn):
    rows = conn.execute(
        """SELECT id, ticker, transaction_date FROM ceo_trades
           WHERE forward_return_12m IS NULL AND transaction_date IS NOT NULL"""
    ).fetchall()
    updated = 0
    for rid, ticker, txn_date in rows:
        try:
            start = datetime.strptime(txn_date[:10], "%Y-%m-%d")
        except ValueError:
            continue
        end = start + timedelta(days=365)
        if end > datetime.now():
            continue
        try:
            hist = yf.download(
                ticker.replace(".", "-"),
                start=start.strftime("%Y-%m-%d"),
                end=(end + timedelta(days=5)).strftime("%Y-%m-%d"),
                progress=False, auto_adjust=True,
            )
            if hist.empty or len(hist) < 2:
                continue
            entry = float(hist["Close"].iloc[0])
            exit_p = float(hist["Close"].iloc[-1])
            ret = (exit_p - entry) / entry
            conn.execute(
                "UPDATE ceo_trades SET forward_return_12m=? WHERE id=?", (ret, rid)
            )
            updated += 1
        except Exception as exc:
            log.warning("Return calc %s: %s", ticker, exc)
        time.sleep(0.5)
    conn.commit()
    return updated


def update_win_rates(conn):
    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    for (name,) in conn.execute("SELECT DISTINCT name FROM ceo_trades").fetchall():
        rows = conn.execute(
            """SELECT forward_return_12m FROM ceo_trades
               WHERE name=? AND transaction_date>=? AND forward_return_12m IS NOT NULL""",
            (name, cutoff),
        ).fetchall()
        if not rows:
            continue
        wr = sum(1 for (r,) in rows if r > 0) / len(rows)
        conn.execute(
            "UPDATE ceo_trades SET win_rate=? WHERE name=? AND transaction_date>=?",
            (wr, name, cutoff),
        )
    conn.commit()


def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    if not os.path.exists(DB_PATH):
        log.error("Run init_db.py first")
        return
    ciks = load_ciks()
    total_new = 0
    with sqlite3.connect(DB_PATH) as conn:
        for row in ciks:
            cik = row["cik"].strip().zfill(10)
            try:
                html = fetch_filings(cik)
                time.sleep(0.5)
                for link in parse_form4_links(html):
                    if link.endswith(".xml"):
                        xml = requests.get(link, headers=HEADERS, timeout=30).text
                        trades = parse_purchases(
                            xml, row["name"], row["ticker"].upper(), cik
                        )
                        total_new += insert_trades(conn, trades)
                        time.sleep(0.5)
            except Exception as exc:
                log.warning("CIK %s (%s): %s", cik, row["ticker"], exc)
        ret_up = compute_returns(conn)
        update_win_rates(conn)
    print(f"CEO tracker: {total_new} new trades, {ret_up} returns updated")


if __name__ == "__main__":
    main()
