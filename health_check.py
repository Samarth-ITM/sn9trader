"""
Health Checker for Sn9Trader.
Checks last run times, database health, disk space, and API keys.
Sends Telegram alerts on anomalies.
Run: python health_check.py
"""

import logging
import os
import shutil
import sqlite3
from datetime import datetime, timedelta
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def get_subscribers():
    if not os.path.exists(DB_PATH):
        return []
    try:
        with sqlite3.connect(DB_PATH) as conn:
            return [r[0] for r in conn.execute("SELECT chat_id FROM subscribers").fetchall()]
    except Exception:
        return []


def send_telegram_alert(msg):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("No TELEGRAM_BOT_TOKEN configured for health alert")
        return

    subs = get_subscribers()
    if not subs:
        log.warning("No Telegram subscribers found to alert")
        return

    for chat_id in subs:
        try:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            resp = requests.post(url, json={"chat_id": chat_id, "text": f"⚠️ <b>Sn9Trader HEALTH ALERT</b>\n{msg}", "parse_mode": "HTML"}, timeout=15)
            resp.raise_for_status()
        except Exception as exc:
            log.warning("Failed to send health alert to %s: %s", chat_id, exc)


def check_log_freshness():
    # Tracker expectations: name -> (expected_interval_minutes, description)
    # Options is disabled, so we won't alert if it hasn't run.
    expectations = {
        "congress": (24 * 60, "Congressional Trades Tracker"),
        "ceo": (6 * 60, "CEO Insider Tracker"),
        "whale": (5, "Whale Activity Tracker"),
        "signals": (6 * 60, "Signal Combiner"),
        "paper": (24 * 60, "Paper Trading Engine")
    }

    issues = []
    now = datetime.now()

    for name, (minutes, desc) in expectations.items():
        log_path = os.path.join(LOGS_DIR, f"{name}.log")
        if not os.path.exists(log_path):
            # If the log does not exist, check if the system just started, otherwise alert
            issues.append(f"{desc} log file does not exist.")
            continue

        mtime = datetime.fromtimestamp(os.path.getmtime(log_path))
        elapsed = (now - mtime).total_seconds() / 60
        limit = minutes * 2

        if elapsed > limit:
            issues.append(f"{desc} has not run in {elapsed/60:.1f} hours (expected every {minutes/60:.1f} hours).")

    return issues


def check_system_stats():
    stats = []
    issues = []
    
    # 1. DB Size
    if os.path.exists(DB_PATH):
        db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
        stats.append(f"DB Size: {db_size_mb:.2f} MB")
        if db_size_mb > 500: # Alert if DB > 500MB
            issues.append(f"Database size is very large: {db_size_mb:.1f} MB.")
    else:
        issues.append("Database file missing.")

    # 2. Disk Space
    usage = shutil.disk_usage(BASE_DIR)
    free_gb = usage.free / (1024 * 1024 * 1024)
    stats.append(f"Free Disk Space: {free_gb:.2f} GB")
    if free_gb < 2.0: # Alert if < 2GB free
        issues.append(f"Low disk space: only {free_gb:.1f} GB free.")

    return stats, issues


def check_api_keys():
    issues = []
    api_key = os.getenv("ETHERSCAN_API_KEY")
    if not api_key:
        # Whale wallets check
        return ["ETHERSCAN_API_KEY is missing in env."]

    # Etherscan test call
    params = {"module": "block", "action": "getblockreward", "blockno": "19000000", "apikey": api_key}
    try:
        resp = requests.get("https://api.etherscan.io/api", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "1":
            issues.append(f"Etherscan API key check failed: {data.get('message', 'invalid status')}")
    except Exception as exc:
        issues.append(f"Etherscan API key connectivity issue: {exc}")

    return issues


def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    
    log.info("Running health check...")
    
    log_issues = check_log_freshness()
    system_stats, system_issues = check_system_stats()
    api_issues = check_api_keys()

    all_issues = log_issues + system_issues + api_issues

    for stat in system_stats:
        log.info("STAT: %s", stat)

    if all_issues:
        alert_msg = "\n".join(f"• {issue}" for issue in all_issues)
        log.error("Health issues detected:\n%s", alert_msg)
        send_telegram_alert(alert_msg)
    else:
        log.info("Health check complete. System is healthy.")


if __name__ == "__main__":
    main()
