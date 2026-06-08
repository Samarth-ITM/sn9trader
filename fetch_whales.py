import argparse
import datetime
import logging
import os
import sqlite3
import time

import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Map tokens to yfinance tickers for return calculation
TOKEN_MAP = {
    "WETH": "ETH-USD",
    "ETH": "ETH-USD",
    "WBTC": "BTC-USD",
    "BTC": "BTC-USD",
    "LINK": "LINK-USD",
    "UNI": "UNI-USD",
    "PEPE": "PEPE-USD",
    "SHIB": "SHIB-USD",
    "MKR": "MKR-USD"
}

def get_forward_returns(ticker, start_date_str):
    try:
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None, None

    end_date = start_date + datetime.timedelta(days=45)
    
    try:
        data = yf.download(ticker, start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"), progress=False)
        if data.empty:
            return None, None
            
        prices = data['Close']
        if isinstance(prices, type(data)): 
            if ticker in prices.columns:
                prices = prices[ticker]
            else:
                return None, None

        if prices.empty:
             return None, None
             
        entry_price = prices.iloc[0]
        if isinstance(entry_price, pd.Series):
             entry_price = entry_price.iloc[0]
        
        returns = {}
        for days in [7, 30]:
            target_date = start_date + datetime.timedelta(days=days)
            future_prices = prices[prices.index.date >= target_date]
            if not future_prices.empty:
                 exit_price = future_prices.iloc[0]
                 if isinstance(exit_price, pd.Series):
                      exit_price = exit_price.iloc[0]
                 returns[days] = (exit_price - entry_price) / entry_price
            else:
                 returns[days] = None
                 
        return returns.get(7), returns.get(30)
    except Exception as e:
        log.warning(f"Error fetching yfinance data for {ticker}: {e}")
        return None, None

def fetch_etherscan_txs(wallet, api_key, limit=None):
    txs = []
    # Fetch ERC-20 transfers
    url = f"https://api.etherscan.io/api?module=account&action=tokentx&address={wallet}&page=1&offset={limit or 100}&sort=desc&apikey={api_key}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "1":
                for item in data.get("result", []):
                    token_symbol = item.get("tokenSymbol", "")
                    # Ignore stablecoins for "trading" returns
                    if token_symbol in ["USDC", "USDT", "DAI"]:
                        continue
                        
                    decimals = int(item.get("tokenDecimal", 18))
                    value = float(item.get("value", 0)) / (10 ** decimals)
                    
                    # We need a rough USD value to filter > $100k.
                    # As a hack for Etherscan, we just assume they are trading large amounts 
                    # if the token amount is large (e.g. > 50 ETH).
                    # A robust solution uses Arkham which gives exact USD.
                    if token_symbol in ["WETH", "ETH"] and value < 30: # < $100k approx
                        continue
                    if token_symbol in ["WBTC", "BTC"] and value < 1.5:
                        continue
                        
                    timestamp = int(item.get("timeStamp", 0))
                    date_str = datetime.datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d')
                    
                    direction = "OUT" if item.get("from", "").lower() == wallet.lower() else "IN"
                    
                    txs.append({
                        "wallet": wallet,
                        "token": token_symbol,
                        "amount": value,
                        "usd_value": value * 3000 if token_symbol in ["WETH", "ETH"] else value * 60000 if token_symbol == "WBTC" else value * 10, # Very rough approx
                        "tx_hash": item.get("hash"),
                        "date": date_str,
                        "direction": direction
                    })
    except Exception as e:
        log.error(f"Etherscan fetch error for {wallet}: {e}")
    return txs

def fetch_arkham_txs(wallet, api_key, limit=None):
    # Arkham Intelligence API
    headers = {"API-Key": api_key}
    url = f"https://api.arkhamintelligence.com/transfers?base={wallet}&limit={limit or 50}"
    txs = []
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            for item in data.get("transfers", []):
                usd = item.get("usd")
                if usd and usd > 100000: # > $100k
                    token_symbol = item.get("token", {}).get("symbol", "")
                    if token_symbol in ["USDC", "USDT", "DAI"]:
                        continue
                        
                    timestamp = item.get("timestamp")
                    date_str = timestamp[:10] if timestamp else None
                    if not date_str:
                        continue
                        
                    direction = "OUT" if item.get("fromAddress", "").lower() == wallet.lower() else "IN"
                    
                    txs.append({
                        "wallet": wallet,
                        "token": token_symbol,
                        "amount": item.get("unitValue", 0),
                        "usd_value": usd,
                        "tx_hash": item.get("transactionHash"),
                        "date": date_str,
                        "direction": direction
                    })
        else:
             log.warning(f"Arkham API returned {response.status_code}")
    except Exception as e:
         log.error(f"Arkham fetch error: {e}")
    return txs

def main():
    parser = argparse.ArgumentParser(description="Fetch Whale Transactions")
    parser.add_argument("--test", type=int, help="Limit the number of transactions to process")
    args = parser.parse_args()

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    wallets_str = os.getenv("WHALE_WALLETS", "")
    etherscan_key = os.getenv("ETHERSCAN_API_KEY")
    arkham_key = os.getenv("ARKHAM_API_KEY")
    
    if not wallets_str:
         log.error("WHALE_WALLETS not found in .env")
         return
         
    wallets = [w.strip() for w in wallets_str.split(",") if w.strip()]
    if not wallets:
         log.error("No valid wallets found in WHALE_WALLETS")
         return

    if not etherscan_key and not arkham_key:
         log.error("Neither ETHERSCAN_API_KEY nor ARKHAM_API_KEY found in .env. Exiting.")
         return

    log.info(f"Processing {len(wallets)} whale wallets...")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        for wallet in wallets:
            log.info(f"Fetching transactions for {wallet}")
            
            if arkham_key:
                txs = fetch_arkham_txs(wallet, arkham_key, limit=args.test)
            else:
                txs = fetch_etherscan_txs(wallet, etherscan_key, limit=args.test)
                
            count = 0
            for tx in txs:
                # Find yfinance ticker
                ticker = TOKEN_MAP.get(tx["token"])
                if not ticker:
                    continue # Cannot track return for unknown token
                    
                r7, r30 = get_forward_returns(ticker, tx["date"])
                
                try:
                    cursor.execute('''
                        INSERT OR IGNORE INTO whale_tx (
                            wallet, token, amount_usd, tx_hash, timestamp, direction,
                            forward_return_7d, forward_return_30d
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        tx["wallet"], tx["token"], tx["usd_value"], tx["tx_hash"],
                        tx["date"], tx["direction"], r7, r30
                    ))
                    if cursor.rowcount > 0:
                        count += 1
                except Exception as e:
                    log.warning(f"Failed to insert whale tx: {e}")
            
            conn.commit()
            log.info(f"Stored {count} new transactions for {wallet}.")

if __name__ == "__main__":
    main()
