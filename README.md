# Sn9Trader Manual Analysis Tool

This project is a manual trading analysis tool. It tracks and ranks the profitability of:
1. Congress Members (via Quiver Quantitative)
2. Corporate CEOs (via SEC EDGAR Form 4 filings)
3. Crypto Whales (via Arkham or Etherscan)

**Disclaimer**: This tool is for educational purposes only. It is not financial advice. 
There is no automated trading, paper trading, or backtesting engine included.

## Setup

1. Copy `.env.example` to `.env` and fill in your API keys.
   - `QUIVER_API_KEY` is required for Congress data.
   - `WHALE_WALLETS` is required for Whale data.
2. Run `python init_db.py` to initialize the database.

## Usage

1. **Fetch Data**
   Run the fetchers to collect data and calculate returns.
   ```bash
   python fetch_congress.py
   python fetch_ceo.py
   python fetch_whales.py
   ```
   *Note: You can use the `--test N` flag to limit the number of entries processed for testing.*

2. **Rank Insiders**
   Run the analysis script to calculate composite scores and view the rankings.
   ```bash
   python rank_insiders.py
   ```
