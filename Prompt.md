markdown

# CONTEXT

You previously built sn9trader (github.com/Samarth-ITM/sn9trader). The user has discovered critical hallucinations:

- Congressional data 90 days stale
- CEO tracker finds zero purchases
- Whale tracker has zero wallets
- Options tracker inserts fake dummy data
- Sharpe ratio mathematically invalid
- Backtest coverage incomplete
- Weight optimizer statistically meaningless

The user wants to ABANDON the automated trading system and build a SIMPLE, HONEST insider ranking tool for MANUAL trading.

# USER'S GOAL

"Analyse insiders and crypto whales, rank them by profitability and consistency, and use that analysis to inform my MANUAL trades. No automated trading. No fake metrics."

# YOUR TASK

Rewrite the sn9trader codebase to be a SIMPLE analysis tool. Follow these instructions exactly.

## STEP 1: DELETE THESE FILES (Remove completely)

- backtest_engine.py
- optimize_weights.py
- paper_trader.py
- options_tracker.py
- social_tracker.py
- setup_cron.py
- deploy.sh
- health_check.py
- signal_combiner.py

## STEP 2: REWRITE fetch_congress.py (was congress_tracker.py)

**Purpose:** Fetch congressional trades from Quiver Quantitative API only.

**Requirements:**

- Use QUIVER_API_KEY from .env
- Endpoint: `https://api.quiverquant.com/beta/live/congresstrading?politician={name}`
- Track these politicians: Pelosi, McConnell, Schiff, Lofgren, Sessions, Tuberville, Kelly, Gillibrand, Warren, Hawley
- For each trade, store: politician, ticker, transaction_date, disclosure_date, amount_range, type (purchase/sale)
- Use yfinance batch download to get price at transaction_date
- Calculate returns: 30d, 60d, 90d, 180d after transaction_date
- Save to table `congress_trades`
- Add `--test N` flag to limit rows
- Add `--fresh` flag to only fetch last 30 days (avoid stale data)
- Do NOT filter by S&P 500 (let user decide)

## STEP 3: REWRITE fetch_ceo.py (was ceo_tracker.py)

**Purpose:** Fetch CEO Form 4 purchases from SEC EDGAR.

**Requirements:**

- Use CIK list from ciks.csv (keep as is)
- Fetch Form 4 filings for each CIK
- Parse XML for transaction_code = 'P' (purchases only, ignore sales/gifts)
- Rate limit: sleep 1 second between requests to avoid 403
- Store in `ceo_trades` table
- Calculate 90d and 180d forward returns using yfinance
- If no purchases found for a CEO, log: "CEO X: 0 purchases in last 90 days" (this is useful signal)
- Add `--test N` flag

## STEP 4: REWRITE fetch_whales.py (was whale_tracker.py)

**Purpose:** Fetch crypto whale transactions from Arkham OR Etherscan API.

**Requirements:**

- Load wallet addresses from WHALE_WALLETS in .env (comma-separated)
- If ARKHAM_API_KEY exists, use Arkham endpoint
- Else if ETHERSCAN_API_KEY exists, use Etherscan
- Else exit with error: "No crypto API key found"
- For each transaction > $100k USD, store: wallet, token, amount_usd, tx_hash, timestamp, direction (exchange_to_wallet / wallet_to_exchange / wallet_to_wallet)
- Get token price at transaction time using CoinGecko API
- Calculate 7d and 30d forward returns
- Add to `whale_tx` table
- Do NOT fail silently — log all errors
- Add `--test N` flag

## STEP 5: CREATE rank_insiders.py (NEW FILE)

**Purpose:** Query database and output ranked list of insiders/whales.

**Requirements:**

For each politician in congress_trades:

- Calculate:
  - total_trades
  - win_count (return_90d > 0)
  - win_rate = win_count / total_trades
  - avg_return_90d = mean(return_90d)
  - median_return_90d
  - std_dev_return
  - freshness = max(0, 1 - (days_since_last_trade / 90)) # 0-1 scale
  - composite_score = (win_rate _ 0.5) + (avg_return_90d/100 _ 0.3) + (freshness \* 0.2)

For each CEO:

- Same metrics as politicians
- Additional: multiple_insider_bonus = 1.2 if >2 CEOs bought same ticker within 7 days

For each whale wallet:

- win_rate_7d on token trades
- avg_position_size_usd (weight larger trades higher)
- exchange_flow_score = +0.2 if accumulation pattern, -0.1 if distribution pattern

**Output to console:**
========== INSIDER RANKINGS ==========

Nancy Pelosi (Congress)
Score: 87.2/100
Trades: 45 | Win rate: 82% | Avg 90d return: 14.3%
Last trade: 45 days ago
Recent buys: NVDA (45d), AAPL (67d)

Jump Crypto (Whale)
Score: 76.4/100
Trades: 28 | Win rate: 71% | Avg 7d return: 22.1%
Recent accumulation: ETH, UNI

Tim Cook (CEO, AAPL)
Score: 42.1/100
Trades: 0 purchases in last 90 days
Signal: No insider confidence (sellers only)

text

**Output to JSON:** Save rankings to `rankings.json` with full data

**Output to Telegram (optional):** If TELEGRAM_BOT_TOKEN set, send top 5 to subscribers

## STEP 6: UPDATE init_db.py

Simplify schema to ONLY these tables:

```sql
congress_trades (
    id, politician, ticker, transaction_date, disclosure_date,
    amount_range, type, price_at_transaction,
    return_30d, return_60d, return_90d, return_180d
)

ceo_trades (
    id, name, ticker, filing_date, transaction_date,
    shares, price, cik, return_90d, return_180d
)

whale_tx (
    id, wallet, token, amount_usd, tx_hash, timestamp,
    direction, price_at_tx, return_7d, return_30d
)

rankings (
    id, source_type, source_name, score, rank_date, json_data
)
Remove all other tables.

STEP 7: UPDATE .env.example
text
# Required
QUIVER_API_KEY=your_key_here
WHALE_WALLETS=0xab5801...,0xf939e0...

# Optional (use one crypto API)
ARKHAM_API_KEY=your_key_here
ETHERSCAN_API_KEY=your_key_here

# Optional
TELEGRAM_BOT_TOKEN=your_token_here
STEP 8: UPDATE README.md
Rewrite to state clearly:

"This is an analysis tool for MANUAL trading, not automated trading"

List three working data sources and how to get API keys

Provide example of running python rank_insiders.py

Remove ALL references to backtesting, optimization, paper trading, options, Sharpe ratio

STEP 9: VERIFICATION REQUIREMENTS
After rewriting, the user must be able to run:

bash
python fetch_congress.py --test 10
python fetch_ceo.py --test 10
python fetch_whales.py --test 10
python rank_insiders.py
Each command must:

Complete without crashing

Output meaningful logs (no silent failures)

Insert data into the database

rank_insiders.py must produce a console table with real numbers

STEP 10: ADD DISCLAIMER
Add this comment at the top of rank_insiders.py:

python
# DISCLAIMER: This tool analyzes public data for educational purposes.
# Past performance does not guarantee future results.
# This is NOT financial advice. The user makes all trading decisions manually.
ACCEPTANCE CRITERIA
All deleted files removed from repository

No fake metrics (Sharpe, backtest win rate, weight optimization, options dummy data)

rank_insiders.py outputs meaningful rankings (not all zeros)

User can get a working system within 30 minutes of setting up API keys

Every script has --test flag for quick validation

No silent failures — all errors logged to console

FINAL NOTE
The user does NOT want automation. They want data. Build a tool that collects data and presents it clearly. Let the human be the decision-maker.

text

---

Once you paste the prompt above, Claude will rewrite your codebase. After it's done, run the verification commands and report back what `rank_insiders.py` outputs.

Then you manually validate one trade with Yahoo Finance. That's the moment you'll know if the system is honest.
```
