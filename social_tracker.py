"""READ-ONLY social accuracy tracker (PROMPT 10). No auto-post."""

import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta

import tweepy
import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")

# User provides real IDs; placeholders below
TRACKED_USER_IDS = os.getenv("TWITTER_USER_IDS", "").split(",")
TICKER_RE = __import__("re").compile(r"\$([A-Z]{1,5})\b")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def get_client():
    token = os.getenv("TWITTER_BEARER_TOKEN", "").strip("'\"")
    if not token:
        log.error("TWITTER_BEARER_TOKEN required")
        return None
    return tweepy.Client(bearer_token=token)


def log_tweets(client, conn):
    if not TRACKED_USER_IDS or TRACKED_USER_IDS == [""]:
        log.warning("Set TWITTER_USER_IDS in .env (comma-separated)")
        return 0
    n = 0
    for uid in TRACKED_USER_IDS:
        uid = uid.strip()
        if not uid:
            continue
        try:
            tweets = client.get_users_tweets(
                uid, max_results=10,
                tweet_fields=["created_at", "author_id"],
            )
            if not tweets.data:
                continue
            user = client.get_user(id=uid)
            username = user.data.username if user.data else uid
            for tw in tweets.data:
                tickers = TICKER_RE.findall(tw.text or "")
                for ticker in tickers:
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO social_accuracy
                               (tweet_id,username,ticker,timestamp)
                               VALUES (?,?,?,?)""",
                            (str(tw.id), username, ticker,
                             tw.created_at.isoformat() if tw.created_at else ""),
                        )
                        n += 1
                    except sqlite3.Error:
                        pass
        except Exception as exc:
            log.warning("User %s: %s", uid, exc)
        time.sleep(1)
    conn.commit()
    return n


def score_pending(conn):
    rows = conn.execute(
        """SELECT id, ticker, timestamp FROM social_accuracy
           WHERE price_change_7d IS NULL AND timestamp IS NOT NULL"""
    ).fetchall()
    updated = 0
    for rid, ticker, ts in rows:
        try:
            tweet_dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))
        except ValueError:
            continue
        if datetime.utcnow() < tweet_dt + timedelta(days=7):
            continue
        try:
            start = tweet_dt.strftime("%Y-%m-%d")
            end = (tweet_dt + timedelta(days=10)).strftime("%Y-%m-%d")
            hist = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
            if hist.empty or len(hist) < 2:
                continue
            chg = (float(hist["Close"].iloc[-1]) / float(hist["Close"].iloc[0])) - 1
            score = 1.0 if chg > 0 else 0.0
            conn.execute(
                """UPDATE social_accuracy SET price_change_7d=?, accuracy_score=?
                   WHERE id=?""",
                (chg, score, rid),
            )
            updated += 1
        except Exception as exc:
            log.warning("Score %s: %s", ticker, exc)
    conn.commit()
    return updated


def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    client = get_client()
    with sqlite3.connect(DB_PATH) as conn:
        logged = log_tweets(client, conn) if client else 0
        scored = score_pending(conn)
    print(f"Social tracker: {logged} tweets logged, {scored} scored")


if __name__ == "__main__":
    main()
