"""Initialize SQLite database with the simplified schema for the manual analysis tool."""

import logging
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

def create_schema(conn):
    # Drop existing tables to ensure a clean slate
    tables_to_drop = [
        "congress_trades", "ceo_trades", "whale_tx", "whale_state",
        "options_flow", "signals", "paper_portfolio", "subscribers",
        "options_oi_snapshot", "social_accuracy", "rankings"
    ]
    for table in tables_to_drop:
        conn.execute(f"DROP TABLE IF EXISTS {table}")

    # Create the new schema
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS congress_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            ticker TEXT NOT NULL,
            transaction_date TEXT,
            disclosure_date TEXT,
            amount_range TEXT,
            party TEXT,
            forward_return_30d REAL,
            forward_return_60d REAL,
            forward_return_90d REAL,
            forward_return_180d REAL,
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
            forward_return_90d REAL,
            forward_return_180d REAL,
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
            forward_return_7d REAL,
            forward_return_30d REAL
        );

        CREATE TABLE IF NOT EXISTS rankings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            name_or_wallet TEXT NOT NULL,
            score_json TEXT,
            timestamp TEXT
        );
    """)
    conn.commit()
    log.info("Clean database schema created at %s", DB_PATH)

def main():
    with sqlite3.connect(DB_PATH) as conn:
        create_schema(conn)
    print("Database initialized successfully.")

if __name__ == "__main__":
    main()
