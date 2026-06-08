import argparse
import datetime
import json
import logging
import os
import sqlite3
import time

import requests
import yfinance as yf
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

POLITICIANS = [
    "Pelosi", "McConnell", "Schiff", "Lofgren", "Sessions",
    "Tuberville", "Kelly", "Gillibrand", "Warren", "Hawley"
]

def get_forward_returns(ticker, start_date_str):
    try:
        # Convert date string to datetime
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None, None, None, None

    end_date = start_date + datetime.timedelta(days=200) # Fetch enough data
    
    try:
        data = yf.download(ticker, start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"), progress=False)
        if data.empty:
            return None, None, None, None
            
        prices = data['Close']
        if isinstance(prices, type(data)): # Sometimes yfinance returns a dataframe for 'Close' if multiple tickers, but here it should be Series
            if ticker in prices.columns:
                prices = prices[ticker]
            else:
                return None, None, None, None

        if prices.empty:
             return None, None, None, None
             
        entry_price = prices.iloc[0]
        if isinstance(entry_price, pd.Series):
             entry_price = entry_price.iloc[0]
        
        returns = {}
        for days in [30, 60, 90, 180]:
            target_date = start_date + datetime.timedelta(days=days)
            # Find the closest trading day after or exactly on target_date
            future_prices = prices[prices.index.date >= target_date]
            if not future_prices.empty:
                 exit_price = future_prices.iloc[0]
                 if isinstance(exit_price, pd.Series):
                      exit_price = exit_price.iloc[0]
                 returns[days] = (exit_price - entry_price) / entry_price
            else:
                 returns[days] = None
                 
        return returns.get(30), returns.get(60), returns.get(90), returns.get(180)
    except Exception as e:
        log.warning(f"Error fetching yfinance data for {ticker}: {e}")
        return None, None, None, None

def fetch_congress_trades(api_key, limit=None, fresh=False):
    headers = {
        "accept": "application/json",
        "X-CSRFToken": "TyTJwjuEC7VV7mOqZ622haRaaUr0x0Ng4nrwSRFKQs7vdoBcJlK9qjAS69ghzhFu",
        "Authorization": f"Token {api_key}"
    }
    
    trades = []
    
    cutoff_date = None
    if fresh:
        cutoff_date = datetime.datetime.now().date() - datetime.timedelta(days=30)

    for politician in POLITICIANS:
        url = f"https://api.quiverquant.com/beta/live/congresstrading?politician={politician}"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                for item in data:
                    # Filter by fresh if requested
                    if fresh and cutoff_date:
                        try:
                            tx_date = datetime.datetime.strptime(item.get("TransactionDate", ""), "%Y-%m-%d").date()
                            if tx_date < cutoff_date:
                                continue
                        except ValueError:
                            pass
                            
                    trades.append({
                        "name": item.get("Representative"),
                        "ticker": item.get("Ticker"),
                        "transaction_date": item.get("TransactionDate"),
                        "disclosure_date": item.get("ReportDate"),
                        "amount_range": item.get("Amount"),
                        "party": item.get("Party")
                    })
                    if limit and len(trades) >= limit:
                        return trades
            else:
                 log.warning(f"Failed to fetch data for {politician}. Status: {response.status_code}")
                 if response.status_code == 401:
                      log.error("Unauthorized: Invalid Quiver API Key.")
                      break # Don't loop if key is invalid
        except Exception as e:
            log.error(f"Error fetching data for {politician}: {e}")
            
    return trades

def main():
    parser = argparse.ArgumentParser(description="Fetch Congress Trades")
    parser.add_argument("--test", type=int, help="Limit the number of trades to process")
    parser.add_argument("--fresh", action="store_true", help="Only process trades from the last 30 days")
    args = parser.parse_args()

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    api_key = os.getenv("QUIVER_API_KEY")
    
    if not api_key:
         log.error("QUIVER_API_KEY not found in .env")
         return

    log.info("Fetching Congress trades...")
    trades = fetch_congress_trades(api_key, limit=args.test, fresh=args.fresh)
    log.info(f"Fetched {len(trades)} trades.")
    
    import pandas as pd # Import here for get_forward_returns scope

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        count = 0
        for trade in trades:
             # Calculate returns
             if not trade["ticker"] or not trade["transaction_date"]:
                  continue
                  
             r30, r60, r90, r180 = get_forward_returns(trade["ticker"], trade["transaction_date"])
             
             try:
                 cursor.execute('''
                     INSERT OR IGNORE INTO congress_trades (
                         name, ticker, transaction_date, disclosure_date, amount_range, party,
                         forward_return_30d, forward_return_60d, forward_return_90d, forward_return_180d
                     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                 ''', (
                     trade["name"], trade["ticker"], trade["transaction_date"], trade["disclosure_date"],
                     trade["amount_range"], trade["party"], r30, r60, r90, r180
                 ))
                 count += 1
             except Exception as e:
                 log.warning(f"Failed to insert trade: {e}")
                 
        conn.commit()
        log.info(f"Successfully processed and stored {count} trades.")

if __name__ == "__main__":
    main()
