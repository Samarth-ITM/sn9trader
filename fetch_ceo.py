import argparse
import csv
import datetime
import logging
import os
import sqlite3
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup
import yfinance as yf

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
CIKS_PATH = os.path.join(BASE_DIR, "ciks.csv")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Sn9Trader manual_analysis@sn9trader.com",
    "Accept-Encoding": "gzip, deflate"
}

def get_forward_returns(ticker, start_date_str):
    try:
        start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except ValueError:
        return None, None

    end_date = start_date + datetime.timedelta(days=200)
    
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
        for days in [90, 180]:
            target_date = start_date + datetime.timedelta(days=days)
            future_prices = prices[prices.index.date >= target_date]
            if not future_prices.empty:
                 exit_price = future_prices.iloc[0]
                 if isinstance(exit_price, pd.Series):
                      exit_price = exit_price.iloc[0]
                 returns[days] = (exit_price - entry_price) / entry_price
            else:
                 returns[days] = None
                 
        return returns.get(90), returns.get(180)
    except Exception as e:
        log.warning(f"Error fetching yfinance data for {ticker}: {e}")
        return None, None

def fetch_sec_submissions(cik):
    url = f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json"
    try:
        time.sleep(1.1) # Strict SEC rate limit is 10 requests per second, 1.1s is very safe
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            return response.json()
        log.warning(f"Failed to fetch submissions for CIK {cik}: Status {response.status_code}")
    except Exception as e:
        log.error(f"Error fetching submissions for CIK {cik}: {e}")
    return None

def fetch_and_parse_xml(cik, accession_number, primary_doc):
    acc_no_dashes = accession_number.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{primary_doc}"
    
    try:
        time.sleep(1.1)
        response = requests.get(url, headers=HEADERS, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "xml")
            
            # Check for purchase (transactionCode == 'P')
            purchases = []
            
            transactions = soup.find_all("nonDerivativeTransaction")
            for tx in transactions:
                code_tag = tx.find("transactionCode")
                if code_tag and code_tag.text == "P":
                    date_tag = tx.find("transactionDate")
                    date = date_tag.find("value").text if date_tag and date_tag.find("value") else None
                    
                    shares_tag = tx.find("transactionShares")
                    shares = float(shares_tag.find("value").text) if shares_tag and shares_tag.find("value") else 0.0
                    
                    price_tag = tx.find("transactionPricePerShare")
                    price = float(price_tag.find("value").text) if price_tag and price_tag.find("value") else 0.0
                    
                    if date and shares > 0:
                        purchases.append({
                            "transaction_date": date,
                            "shares": shares,
                            "price": price
                        })
            return purchases
    except Exception as e:
        log.error(f"Error parsing XML for {cik} / {accession_number}: {e}")
    return []

def main():
    parser = argparse.ArgumentParser(description="Fetch CEO Trades")
    parser.add_argument("--test", type=int, help="Limit the number of CEOs to process")
    args = parser.parse_args()

    ceos = []
    if os.path.exists(CIKS_PATH):
        with open(CIKS_PATH, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('cik') and row.get('name') and row.get('ticker'):
                    ceos.append(row)
    else:
        log.error(f"{CIKS_PATH} not found.")
        return

    if args.test:
        ceos = ceos[:args.test]

    log.info(f"Processing {len(ceos)} CEOs...")

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        for ceo in ceos:
            cik = ceo["cik"]
            name = ceo["name"]
            ticker = ceo["ticker"]
            
            log.info(f"Fetching data for {name} ({ticker}) - CIK {cik}")
            
            subs = fetch_sec_submissions(cik)
            if not subs:
                continue
                
            recent = subs.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accession_numbers = recent.get("accessionNumber", [])
            primary_docs = recent.get("primaryDocument", [])
            filing_dates = recent.get("filingDate", [])
            
            # Find Form 4s
            form_4_indices = [i for i, form in enumerate(forms) if form == "4"]
            
            total_purchases_found = 0
            
            # Only process the most recent 5 Form 4s to save time, unless looking for historicals
            for idx in form_4_indices[:5]: 
                acc = accession_numbers[idx]
                doc = primary_docs[idx]
                f_date = filing_dates[idx]
                
                if not doc.endswith(".xml"):
                     continue # Skip non-xml primary docs
                
                purchases = fetch_and_parse_xml(cik, acc, doc)
                for p in purchases:
                    # Found a purchase! Calculate forward returns
                    r90, r180 = get_forward_returns(ticker, p["transaction_date"])
                    
                    try:
                        cursor.execute('''
                            INSERT OR IGNORE INTO ceo_trades (
                                name, ticker, filing_date, transaction_date, shares, price, cik,
                                forward_return_90d, forward_return_180d
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            name, ticker, f_date, p["transaction_date"], p["shares"], p["price"], cik,
                            r90, r180
                        ))
                        total_purchases_found += 1
                    except Exception as e:
                        log.warning(f"Failed to insert CEO trade: {e}")
            
            conn.commit()
            
            if total_purchases_found == 0:
                 log.info(f"CEO {name}: 0 purchases found.")
            else:
                 log.info(f"CEO {name}: {total_purchases_found} purchases recorded.")

if __name__ == "__main__":
    main()
