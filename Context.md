# PROJECT: QUANTITATIVE SIGNAL AGGREGATOR FOR RETAIL EDGE

## Core Philosophy
The stock/crypto market is structurally unfair. Insiders (Congress, CEOs, whales) have information advantage. Retail cannot beat them directly. But retail CAN legally track their PUBLIC FOOTPRINTS after mandatory disclosure and statistically exploit their predictable patterns.

## Goal
Build an automated system that:
1. Collects ALL legally public trading data from Congress, CEOs, on-chain whales, and options flow
2. Backtests historical performance of each signal source
3. Generates confidence-weighted composite signals
4. Paper trades for 90 days validation
5. Produces auditable legal trail for every recommendation

## Data Sources (All Legal, All Free)

### Source 1: Congressional Trades
- API: https://housestockwatcher.com/api (no key)
- Focus: Pelosi, McConnell, Schiff, Lofgren, Sessions
- Lag: 45 days disclosure delay
- Pattern: 6-month forward return after disclosure date + 2 days (accounting for reporting lag)
- Weight in composite: 0.35

### Source 2: CEO Insider Purchases
- Source: SEC EDGAR Form 4 (direct filing access via https://www.sec.gov/Archives/edgar/data/)
- CIK list: Provide static list of 100 top SP500 CEOs + Indian-origin US CEOs
- Filter: transaction_code = 'P' (purchase), not options exercise
- Lag: 2 days filing delay
- Pattern: 12-month forward return, strong signal when multiple insiders buy same stock within 7 days
- Weight: 0.30

### Source 3: On-Chain Whale Moves
- Source: Etherscan API (free tier, 5 calls/sec)
- Wallet list: 50 predefined whale addresses (exchange hot wallets, known VC funds, large holders)
- Monitor: ERC-20 transfers > $100k USD value
- Key pattern: Transfer FROM exchange TO unknown wallet = accumulation (bullish)
- Key pattern: Transfer TO exchange = potential sell (bearish)
- Real-time: Poll every 5 minutes
- Weight: 0.20

### Source 4: Options Unusual Activity
- Source: NSE India option chain (https://www.nseindia.com/option-chain) for Nifty/Bank Nifty
- Also: Use free tier of OptionChain API or scrape Barchart unusual options (US markets)
- Detection: Open interest change >30% in 15 minutes OR premium > $500k notional
- Sentiment: Call OI spike = bullish, Put OI spike = bearish
- Lag: Near real-time (15 min delay)
- Weight: 0.15

## Signal Confidence Formula

```python
base_confidence = (
    congress_score * 0.35 +
    ceo_score * 0.30 +
    whale_score * 0.20 +
    options_score * 0.15
)

# Each source score = historical_win_rate * (1 - volatility_penalty) * recency_bonus
# recency_bonus = 1.2 if last 6 months win_rate > 65% else 1.0

# Multi-source bonus:
if num_sources > 1 and tickers_match:
    base_confidence *= (1 + (num_sources - 1) * 0.15)

# Output threshold: >70 = trade signal, >85 = strong signal
Legal Protection Requirement
For EVERY trade signal generated, the system MUST save a JSON file containing:

timestamp_utc

ticker

confidence_score

list of source_urls (direct links to: HouseStockWatcher page, SEC filing page, Etherscan transaction, Option chain screenshot)

reasoning_text (human readable)

suggested_hold_days (default 90 for congress, 180 for CEO, 30 for crypto)

Output Format (Telegram)
text
🟢 BUY SIGNAL — AAPL
Confidence: 82/100
Sources: Pelosi (2 weeks ago), CEO Cook (3 days ago)
Hold: 90 days
Risk: Medium
Legal trail: /legal_trail/2025-06-05_AAPL.json
System Requirements
Debian minimal VM (already running) (access: ssh samarth@{ip addr} -> cd /sn9trader) 

Python 3.10+

SQLite for database

Telegram bot for alerts

Cron jobs for scheduling

No paid APIs except if free tier exhausted

Database Schema
Tables:

congress_trades (id, name, ticker, transaction_date, disclosure_date, amount_range, party)

ceo_trades (id, name, ticker, filing_date, transaction_date, shares, price, cik)

whale_tx (id, wallet, token, amount_usd, tx_hash, timestamp, direction)

options_flow (id, ticker, strike, expiry, type, oi_change, premium, detected_at)

signals (id, ticker, confidence, sources_json, created_at, suggested_hold, legal_trail_path)

paper_portfolio (signal_id, entry_price, entry_date, shares, exit_price, exit_date, pnl)

Success Criteria:
After 90 days paper trading:

Outperform SPY by >10% absolute

Win rate >55%

Max drawdown <20%

No SEC inquiries (obviously)