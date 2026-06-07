# Sn9Trader Update Log

## Summary of Actions (v1)

- **Congressional Trades**: Swapped dead API to parallelized Kadao GitHub fallback. Implemented batch yfinance price fetch (slashing runtime from 30min to <20s). Exact match checking resolved Kelly ambiguities. Added `--test`, `--quick` flags, resume logic, and `tqdm` progress bars.
- **CEO Tracker**: Updated User-Agent to resolve SEC 403 Forbidden blocks. Fixed XML path parsing bug for index page listings. Added `--test` flag.
- **Environment**: Cleaned up env variables, `.env.example`, `.gitignore`, and `requirements.txt`.
- **Pipeline**: Created `fix_all.sh` to automate backup, init, test and run.

---

## 7/6-1:50am
- **Weight Redistribution**: Disabled NSE options tracker (404 error) and redistributed weights in `signal_combiner.py` (Congress: 0.45, CEO: 0.35, Whale: 0.20) if options data is absent.
- **Backtest Engine**: Created `backtest_engine.py` to run historical simulations over past 2 years and generate Plotly `backtest_report.html` (Overall Win Rate: 61.4%, Sharpe: 1.20).
- **Weight Optimizer**: Created `optimize_weights.py` to find optimal weights: Congress=0.50, CEO=0.20, Whale=0.30, Options=0.00 (Sharpe: 1.3024). Saved to `optimal_weights.json`.
- **Paper Trader**: Enhanced `paper_trader.py` with `--live` and `--historical` modes, confidence-based sizing, daily Low stop-losses, and weekly reports with `paper_portfolio/equity_curve.png`.
- **Deployment**: Created `deploy.sh` script to automate system requirements check, database init, test run, and systemd service/timer file generation.
- **Health Check**: Created `health_check.py` to check log freshness, database size, disk space, and API connectivity.

### Brutal Persistent Issues:
1. **NSE Options Scanner is Dead (404)**: The NSE option chain API is completely blocked/dead. Options monitoring is disabled and its weights have been redistributed to other sources. Unless a private options API feed is purchased or a complex headless-browser session scraper is engineered, options data tracking is dead.
2. **Invalid Etherscan API Key**: The Etherscan API key in `.env` is invalid, causing Etherscan block rewards test and Whale Tracker transactions lookup to fail. Whale wallet transfers cannot be tracked until a valid free tier Etherscan API key is provided.
3. **No Whale Wallets Configured**: `WHALE_WALLETS` and `WHALE_WALLETS_CSV` variables are empty. The Whale Tracker runs but monitors 0 wallets, rendering it useless. You must populate these variables with target blockchain addresses.
4. **SEC Edgar Block Vulnerability**: The SEC Edgar API is highly sensitive. The user-agent is hardcoded as `sn9trader samarth@example.com`. If too many concurrent connections are opened or if the SEC flags the domain/email, the IP will be blacklisted. The email should be updated to a real personal/business email, and rate-limiting sleeps must be strictly observed.
5. **Telegram Alerts Bot Silenced**: No signals or health alerts will be delivered unless the daemon is running (`python telegram_send.py`) and at least one user subscribes by sending the `/start` command to the bot.

---

## 7/6-2:28am
- **Comprehensive System Audit**: Performed a full validation of data collections, backtesting metrics, and goal alignment.
- **Audit Findings Written to answer.md**: Created [answer.md](file:///Users/samarth/Downloads/Official/PROJECTS/CODES/Python/Projects/sn9trader/answer.md) detailing exact dates of congress trades (stale, 90+ days old), SEC EDGAR Form 4 filings (no purchases for tracked CEOs in 90 days), Whale Tracker configuration (zombie process), Options Flow US scanner (static placeholder), S&P 500 cache MD5 verification, and Sharpe ratio invalidity (computed on individual trade returns instead of daily series).
- **Goal Alignment Check**: Noted that the system does not output ranked profitability lists or structured daily newsletters, and lacks real-time crypto edge.

