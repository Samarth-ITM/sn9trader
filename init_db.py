"""Initialize SQLite database, project folders, and run smoke tests."""

import logging
import os
import sqlite3

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
ENV_EXAMPLE_PATH = os.path.join(BASE_DIR, ".env.example")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def get_env(*keys, default=""):
    for key in keys:
        value = os.getenv(key)
        if value:
            return value.strip("'\"")
    return default


def create_folders():
    for folder in ("legal_trail", "logs", "paper_portfolio"):
        path = os.path.join(BASE_DIR, folder)
        os.makedirs(path, exist_ok=True)
        log.info("Folder ready: %s", path)


def create_env_template():
    if os.path.exists(ENV_EXAMPLE_PATH):
        log.info(".env.example already exists, skipping")
        return
    content = """# Copy to .env and fill in values
ETHERSCAN_KEY=
ETHERSCAN_API_KEY=
TELEGRAM_TOKEN=
TELEGRAM_BOT_TOKEN=
WHALE_WALLETS=
WHALE_WALLETS_CSV=
TWITTER_BEARER_TOKEN=
THETADATA_USER=
THETADATA_PASS=
"""
    with open(ENV_EXAMPLE_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    log.info("Created .env.example")


def create_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            transaction_date TEXT,
            disclosure_date TEXT,
            amount_range TEXT,
            party TEXT,
            forward_return_90d REAL,
            forward_return_180d REAL,
            win_rate REAL,
            UNIQUE(name, ticker, transaction_date, disclosure_date)
        );

        CREATE TABLE IF NOT EXISTS ceo_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            filing_date TEXT,
            transaction_date TEXT,
            shares REAL,
            price REAL,
            cik TEXT,
            forward_return_12m REAL,
            win_rate REAL,
            UNIQUE(cik, transaction_date, shares, filing_date)
        );

        CREATE TABLE IF NOT EXISTS whale_tx (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet TEXT NOT NULL,
            token TEXT,
            amount_usd REAL,
            tx_hash TEXT UNIQUE,
            timestamp TEXT,
            direction TEXT,
            alert_flag INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS whale_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS options_flow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strike REAL,
            expiry TEXT,
            type TEXT,
            oi_change REAL,
            premium REAL,
            detected_at TEXT,
            sentiment TEXT
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            confidence REAL,
            sources_json TEXT,
            created_at TEXT,
            suggested_hold INTEGER,
            legal_trail_path TEXT,
            paper_status TEXT
        );

        CREATE TABLE IF NOT EXISTS paper_portfolio (
            signal_id INTEGER,
            entry_price REAL,
            entry_date TEXT,
            shares REAL,
            exit_price REAL,
            exit_date TEXT,
            pnl REAL,
            FOREIGN KEY (signal_id) REFERENCES signals(id)
        );

        CREATE TABLE IF NOT EXISTS subscribers (
            chat_id TEXT PRIMARY KEY,
            name TEXT,
            subscribed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS options_oi_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT,
            strike REAL,
            expiry TEXT,
            opt_type TEXT,
            oi REAL,
            snapshot_at TEXT
        );

        CREATE TABLE IF NOT EXISTS social_accuracy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tweet_id TEXT UNIQUE,
            username TEXT,
            ticker TEXT,
            timestamp TEXT,
            price_change_7d REAL,
            accuracy_score REAL
        );
    """)
    conn.commit()
    log.info("Database schema created at %s", DB_PATH)


def test_etherscan():
    api_key = get_env("ETHERSCAN_API_KEY", "ETHERSCAN_KEY")
    if not api_key:
        log.warning("No Etherscan API key found; skipping smoke test")
        return True

    params = {"module": "block", "action": "getblocknobytime",
              "timestamp": "1680000000", "closest": "before", "apikey": api_key}
    for url in ("https://api.etherscan.io/v2/api?chainid=1",
                "https://api.etherscan.io/api"):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "1" or data.get("result"):
                log.info("Etherscan API OK (block lookup)")
                return True
            log.warning("Etherscan returned: %s", data.get("message", data))
        except requests.RequestException as exc:
            log.warning("Etherscan request failed (%s): %s", url, exc)

    # Fallback: simple block number query for block 19000000
    params = {"module": "block", "action": "getblockreward",
              "blockno": "19000000", "apikey": api_key}
    try:
        resp = requests.get("https://api.etherscan.io/api", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "1":
            log.info("Etherscan API OK (block 19000000)")
            return True
        log.warning("Etherscan block test failed: %s", data.get("message", data))
    except requests.RequestException as exc:
        log.warning("Etherscan block test error: %s", exc)
    return True  # non-fatal per plan


def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    create_folders()
    create_env_template()
    with sqlite3.connect(DB_PATH) as conn:
        create_schema(conn)
    test_etherscan()
    print("Ready")


if __name__ == "__main__":
    main()
