"""
Congressional trade tracker.
Cron (PROMPT 9): 0 2 * * * — daily at 02:00 IST
"""

import argparse
import concurrent.futures
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from io import StringIO

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from tqdm import tqdm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
SP500_CACHE = os.path.join(BASE_DIR, "logs", "sp500_tickers.txt")
API_URL = "https://housestockwatcher.com/api/latest_trades"
API_FALLBACKS = [
    "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json",
]
KADAO_BASE = (
    "https://raw.githubusercontent.com/kadoa-org/congress-trading-monitor"
    "/main/public/data/filer"
)
KADAO_FILERS = [
    "house_nancy_pelosi",
    "senate_amitchell_mcconnelljr",
    "house_adamb_schiff",
    "senate_adamb_schiff",
    "house_zoe_lofgren",
    "house_pete_sessions",
    "senate_marke_kelly",
    "senate_thomash_tuberville",
]

POLITICIAN_MAP = {
    "nancy pelosi": "Nancy Pelosi",
    "a mitchell mcconnell": "Mitch McConnell",
    "a mitchell mcconnell jr": "Mitch McConnell",
    "mitch mcconnell": "Mitch McConnell",
    "adam b schiff": "Adam Schiff",
    "adam schiff": "Adam Schiff",
    "zoe lofgren": "Zoe Lofgren",
    "pete sessions": "Pete Sessions",
    "thomas h tuberville": "Tommy Tuberville",
    "tommy tuberville": "Tommy Tuberville",
    "mark e kelly": "Mark Kelly",
    "mark kelly": "Mark Kelly"
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "logs", "congress.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def normalize_ticker(ticker):
    if not ticker:
        return ""
    t = str(ticker).strip().upper().lstrip("$")
    return t.replace(".", "-")


def get_standard_politician_name(raw_name):
    if not raw_name:
        return None
    n = raw_name.lower().strip().replace(".", "").replace(",", "")
    n = " ".join(n.split())
    return POLITICIAN_MAP.get(n)


def politician_matches(name):
    return get_standard_politician_name(name) is not None


def _extract_trade_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("transactions", "data", "trades"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError(f"Unexpected API response type: {type(data)}")


def _fetch_url_trades(url):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return _extract_trade_list(resp.json())


def _fetch_single_filer(filer_id):
    try:
        url = f"{KADAO_BASE}/{filer_id}.json"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        filer = payload.get("filer", {})
        name = filer.get("full_name", filer_id)
        party = filer.get("party", "")
        filer_trades = []
        for t in payload.get("trades", []):
            filer_trades.append({
                "representative": name,
                "ticker": t.get("ticker"),
                "transaction_date": t.get("transaction_date"),
                "disclosure_date": t.get("filing_date"),
                "amount": t.get("amount_range_label"),
                "party": party,
            })
        return filer_trades
    except Exception as exc:
        log.warning("Kadao fetch failed for %s: %s", filer_id, exc)
        return []


def _fetch_kadao_trades():
    trades = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_fetch_single_filer, fid): fid for fid in KADAO_FILERS}
        for future in concurrent.futures.as_completed(futures):
            trades.extend(future.result())
    return trades


def fetch_trades():
    log.info("Fetching congressional trades via parallelized Kadao GitHub source...")
    data = _fetch_kadao_trades()
    if data:
        log.info("Loaded %d trades from Kadao", len(data))
        return data

    log.warning("Kadao source failed; attempting original API fallback...")
    sources = [API_URL, *API_FALLBACKS]
    for url in sources:
        for attempt in range(2):
            try:
                data = _fetch_url_trades(url)
                log.info("Loaded %d trades from %s", len(data), url)
                return data
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                log.warning("Fetch failed (%s) attempt %d: %s", url, attempt + 1, exc)
                if attempt == 0:
                    time.sleep(2)
    return []


def parse_trade(raw):
    name = (raw.get("representative") or raw.get("politician")
            or raw.get("name") or raw.get("senator") or "")
    ticker = normalize_ticker(
        raw.get("ticker") or raw.get("asset_description") or raw.get("asset") or ""
    )
    if not ticker or len(ticker) > 6:
        desc = raw.get("asset_description") or raw.get("asset") or ""
        if desc and len(desc) <= 6:
            ticker = normalize_ticker(desc)
    return {
        "name": name.strip(),
        "ticker": ticker,
        "transaction_date": raw.get("transaction_date") or raw.get("tx_date") or "",
        "disclosure_date": raw.get("disclosure_date") or raw.get("report_date") or "",
        "amount_range": str(raw.get("amount") or raw.get("amount_range") or raw.get("range") or ""),
        "party": raw.get("party") or raw.get("district") or "",
    }


def load_sp500():
    if os.path.exists(SP500_CACHE):
        with open(SP500_CACHE, encoding="utf-8") as f:
            tickers = {line.strip().upper() for line in f if line.strip()}
        if tickers:
            log.info("Loaded %d SP500 tickers from cache", len(tickers))
            return tickers

    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    try:
        df = pd.read_csv(url)
        tickers = {normalize_ticker(s) for s in df["Symbol"].tolist()}
        os.makedirs(os.path.dirname(SP500_CACHE), exist_ok=True)
        with open(SP500_CACHE, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(tickers)))
        log.info("Loaded and cached %d SP500 tickers from GitHub CSV", len(tickers))
        return tickers
    except Exception as exc:
        log.error("Could not load SP500 ticker list from GitHub: %s", exc)
        return set()


def insert_trades(conn, trades, sp500, test_limit=None):
    inserted = 0
    try:
        existing = pd.read_sql("SELECT DISTINCT transaction_date, ticker, name FROM congress_trades", conn)
        existing_set = set(zip(existing["transaction_date"], existing["ticker"], existing["name"]))
    except Exception as exc:
        log.warning("Could not read existing trades: %s", exc)
        existing_set = set()

    processed_count = 0
    for raw in tqdm(trades, desc="Processing congressional trades"):
        trade = parse_trade(raw)
        standard_name = get_standard_politician_name(trade["name"])
        if not standard_name:
            continue
        trade["name"] = standard_name

        if trade["ticker"] not in sp500:
            continue

        if (trade["transaction_date"], trade["ticker"], trade["name"]) in existing_set:
            continue

        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO congress_trades
                   (name, ticker, transaction_date, disclosure_date, amount_range, party)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (trade["name"], trade["ticker"], trade["transaction_date"],
                 trade["disclosure_date"], trade["amount_range"], trade["party"]),
            )
            if cur.rowcount > 0:
                inserted += 1
                processed_count += 1
                if test_limit and processed_count >= test_limit:
                    log.info("Test limit of %d trades reached", test_limit)
                    break
        except sqlite3.Error as exc:
            log.warning("Insert failed for %s %s: %s", trade["name"], trade["ticker"], exc)
    conn.commit()
    return inserted


def parse_date(date_str):
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str[:10], fmt)
        except ValueError:
            continue
    return None


def get_price_from_df(df, ticker, target_date):
    if ticker not in df:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            close = df[ticker]["Close"].dropna()
        else:
            close = df["Close"].dropna()
        if close.empty:
            return None
        idx = close.index
        on_or_after = close[idx >= pd.Timestamp(target_date)]
        if not on_or_after.empty:
            closest_date = on_or_after.index[0]
            if (closest_date - pd.Timestamp(target_date)).days <= 7:
                return float(on_or_after.iloc[0])
        before = close[idx <= pd.Timestamp(target_date)]
        if not before.empty:
            closest_date = before.index[-1]
            if (pd.Timestamp(target_date) - closest_date).days <= 7:
                return float(before.iloc[-1])
    except Exception as exc:
        log.warning("Local price lookup failed for %s on %s: %s", ticker, target_date.date(), exc)
    return None


def compute_forward_returns(conn, quick=False):
    if quick:
        log.info("Quick mode: skipping forward returns calculation")
        return 0

    rows = conn.execute(
        """SELECT id, ticker, disclosure_date
           FROM congress_trades
           WHERE disclosure_date IS NOT NULL AND disclosure_date != ''
             AND (forward_return_90d IS NULL OR forward_return_180d IS NULL)"""
    ).fetchall()

    if not rows:
        log.info("No trades missing forward returns")
        return 0

    ticker_trades = {}
    tickers = set()
    for row_id, ticker, disclosure_date in rows:
        disc = parse_date(disclosure_date)
        if not disc:
            continue
        entry_date = disc + timedelta(days=2)
        if entry_date > datetime.now():
            continue
        tickers.add(ticker)
        if ticker not in ticker_trades:
            ticker_trades[ticker] = []
        ticker_trades[ticker].append((row_id, entry_date))

    if not tickers:
        log.info("No trades with valid past entry dates to update")
        return 0

    all_dates = []
    for t_trades in ticker_trades.values():
        for _, entry_date in t_trades:
            all_dates.append(entry_date)

    min_start = min(all_dates) - timedelta(days=10)
    max_end = max(all_dates) + timedelta(days=190)
    
    today = datetime.now()
    if max_end > today + timedelta(days=1):
        max_end = today + timedelta(days=1)

    log.info("Batch downloading price history for %d tickers from %s to %s",
             len(tickers), min_start.date(), max_end.date())

    try:
        df = yf.download(
            list(tickers),
            start=min_start.strftime("%Y-%m-%d"),
            end=max_end.strftime("%Y-%m-%d"),
            group_by="ticker",
            progress=False,
            auto_adjust=True
        )
    except Exception as exc:
        log.error("Batch price download failed: %s", exc)
        return 0

    updated = 0
    for row_id, ticker, disclosure_date in tqdm(rows, desc="Calculating forward returns"):
        disc = parse_date(disclosure_date)
        if not disc:
            continue
        entry_date = disc + timedelta(days=2)
        if entry_date > datetime.now():
            continue

        entry_price = get_price_from_df(df, ticker, entry_date)
        if not entry_price or entry_price <= 0:
            continue

        ret_90 = ret_180 = None
        exit_90 = entry_date + timedelta(days=90)
        if exit_90 <= datetime.now():
            price_90 = get_price_from_df(df, ticker, exit_90)
            if price_90:
                ret_90 = (price_90 - entry_price) / entry_price

        exit_180 = entry_date + timedelta(days=180)
        if exit_180 <= datetime.now():
            price_180 = get_price_from_df(df, ticker, exit_180)
            if price_180:
                ret_180 = (price_180 - entry_price) / entry_price

        if ret_90 is not None or ret_180 is not None:
            conn.execute(
                """UPDATE congress_trades
                   SET forward_return_90d = COALESCE(?, forward_return_90d),
                       forward_return_180d = COALESCE(?, forward_return_180d)
                   WHERE id = ?""",
                (ret_90, ret_180, row_id),
            )
            updated += 1

    conn.commit()
    return updated


def update_win_rates(conn):
    cutoff = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    politicians = conn.execute(
        "SELECT DISTINCT name FROM congress_trades"
    ).fetchall()

    for (name,) in politicians:
        trades = conn.execute(
            """SELECT forward_return_90d FROM congress_trades
               WHERE name = ? AND disclosure_date >= ?
                 AND forward_return_90d IS NOT NULL""",
            (name, cutoff),
        ).fetchall()
        if not trades:
            continue
        wins = sum(1 for (r,) in trades if r > 0)
        win_rate = wins / len(trades)
        conn.execute(
            """UPDATE congress_trades SET win_rate = ?
               WHERE name = ? AND disclosure_date >= ?""",
            (win_rate, name, cutoff),
        )

    conn.commit()


def print_summary(conn, fetched, inserted, returns_updated):
    print("\n=== Congressional Trade Summary ===")
    print(f"Trades fetched from API: {fetched}")
    print(f"New rows inserted:       {inserted}")
    print(f"Forward returns updated: {returns_updated}")
    print(f"Total in DB:             {conn.execute('SELECT COUNT(*) FROM congress_trades').fetchone()[0]}")

    print("\nWin rates by politician (6-month rolling):")
    rows = conn.execute(
        """SELECT name, AVG(win_rate) as wr, COUNT(*) as n
           FROM congress_trades
           WHERE win_rate IS NOT NULL
           GROUP BY name
           ORDER BY wr DESC"""
     ).fetchall()
    if not rows:
        print("  (no win rate data yet — need matured forward returns)")
    for name, wr, n in rows:
        print(f"  {name}: {wr:.1%} win rate ({n} trades)")

    print("\nRecent trades:")
    recent = conn.execute(
        """SELECT name, ticker, disclosure_date, forward_return_90d, win_rate
           FROM congress_trades
           ORDER BY disclosure_date DESC LIMIT 5"""
    ).fetchall()
    for row in recent:
        ret = f"{row[3]:.1%}" if row[3] is not None else "pending"
        wr = f"{row[4]:.1%}" if row[4] is not None else "n/a"
        print(f"  {row[0]} | {row[1]} | disclosed {row[2]} | 90d return: {ret} | win_rate: {wr}")


def main():
    parser = argparse.ArgumentParser(description="Congress Trade Tracker")
    parser.add_argument("--test", type=int, default=None, help="Limit number of records to process")
    parser.add_argument("--quick", action="store_true", help="Skip forward return calculations")
    args = parser.parse_args()

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

    if not os.path.exists(DB_PATH):
        log.error("Database not found. Run init_db.py first.")
        return

    raw_trades = fetch_trades()
    log.info("Fetched %d trades total", len(raw_trades))

    sp500 = load_sp500()
    with sqlite3.connect(DB_PATH) as conn:
        inserted = insert_trades(conn, raw_trades, sp500, test_limit=args.test)
        returns_updated = compute_forward_returns(conn, quick=args.quick)
        update_win_rates(conn)
        print_summary(conn, len(raw_trades), inserted, returns_updated)


if __name__ == "__main__":
    main()
