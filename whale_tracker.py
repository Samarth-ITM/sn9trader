"""On-chain whale tracker via Etherscan. Cron: */5 * * * *"""

import logging
import os
import sqlite3
import time

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
EXCHANGE_PREFIXES = (
    "0x28c6c06298d514db55e5743bf21d60",
    "binance", "coinbase", "kraken", "okx",
)
RATE_LIMIT = 0.21  # ~5 calls/sec

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def get_env(*keys, default=""):
    for k in keys:
        v = os.getenv(k)
        if v:
            return v.strip("'\"")
    return default


def load_wallets():
    csv_path = get_env("WHALE_WALLETS_CSV")
    if csv_path and os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    raw = get_env("WHALE_WALLETS")
    return [w.strip() for w in raw.split(",") if w.strip()] if raw else []


def is_exchange(addr):
    if not addr:
        return False
    low = addr.lower()
    return any(low.startswith(p) or p in low for p in EXCHANGE_PREFIXES)


def get_token_price(token_addr, api_key):
    # CoinGecko contract price (ethereum)
    url = f"https://api.coingecko.com/api/v3/simple/token_price/ethereum"
    params = {"contract_addresses": token_addr, "vs_currencies": "usd"}
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        return float(data.get(token_addr.lower(), {}).get("usd", 0))
    except Exception:
        return 0.0


def get_state(conn, key):
    row = conn.execute("SELECT value FROM whale_state WHERE key=?", (key,)).fetchone()
    return int(row[0]) if row else 0


def set_state(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO whale_state (key, value) VALUES (?, ?)",
        (key, str(value)),
    )
    conn.commit()


def detect_direction(from_addr, to_addr):
    from_ex = is_exchange(from_addr)
    to_ex = is_exchange(to_addr)
    if to_ex and not from_ex:
        return "wallet_to_exchange"
    if from_ex and not to_ex:
        return "exchange_to_wallet"
    return "other"


def fetch_token_tx(wallet, api_key):
    params = {
        "module": "account", "action": "tokentx", "address": wallet,
        "sort": "desc", "page": 1, "offset": 100, "apikey": api_key,
    }
    resp = requests.get("https://api.etherscan.io/api", params=params, timeout=30)
    data = resp.json()
    if data.get("status") != "1":
        return []
    return data.get("result", [])


def main():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    api_key = get_env("ETHERSCAN_API_KEY", "ETHERSCAN_KEY")
    wallets = load_wallets()
    if not wallets:
        log.warning("No whale wallets configured in WHALE_WALLETS / WHALE_WALLETS_CSV")
        return
    if not api_key:
        log.error("ETHERSCAN_KEY required")
        return

    inserted = alerts = 0
    price_cache = {}
    with sqlite3.connect(DB_PATH) as conn:
        for wallet in wallets:
            last_ts = get_state(conn, f"last_ts_{wallet}")
            try:
                txs = fetch_token_tx(wallet, api_key)
            except Exception as exc:
                log.warning("Wallet %s: %s", wallet[:10], exc)
                time.sleep(RATE_LIMIT)
                continue

            max_ts = last_ts
            for tx in txs:
                ts = int(tx.get("timeStamp", 0))
                if ts <= last_ts:
                    continue
                max_ts = max(max_ts, ts)
                token = tx.get("tokenSymbol", "UNKNOWN")
                contract = tx.get("contractAddress", "")
                decimals = int(tx.get("tokenDecimal", 18))
                value = int(tx.get("value", 0)) / (10 ** decimals)
                if contract not in price_cache:
                    price_cache[contract] = get_token_price(contract, api_key)
                    time.sleep(RATE_LIMIT)
                usd = value * price_cache[contract]
                if usd < 100_000:
                    continue
                direction = detect_direction(tx.get("from", ""), tx.get("to", ""))
                alert = 1 if direction == "exchange_to_wallet" else 0
                try:
                    cur = conn.execute(
                        """INSERT OR IGNORE INTO whale_tx
                           (wallet,token,amount_usd,tx_hash,timestamp,direction,alert_flag)
                           VALUES (?,?,?,?,?,?,?)""",
                        (wallet, token, usd, tx.get("hash"), str(ts), direction, alert),
                    )
                    if cur.rowcount:
                        inserted += 1
                        if alert:
                            alerts += 1
                            log.info("ACCUMULATION %s $%.0f %s", token, usd, wallet[:10])
                except sqlite3.Error as exc:
                    log.warning("Insert error: %s", exc)
            if max_ts > last_ts:
                set_state(conn, f"last_ts_{wallet}", max_ts)
            time.sleep(RATE_LIMIT)

    print(f"Whale tracker: {inserted} new txs, {alerts} accumulation alerts")


if __name__ == "__main__":
    main()
