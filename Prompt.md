## PROMPT: Diagnostic & Remediation

Copy this entire prompt to Claude/Copilot:

````markdown
# CONTEXT

We built sn9trader per Prompt.md, but congressional data source (housestockwatcher.com) is DEAD. S3 mirror 403s. Kadao fallback works but is slow and unvalidated. Need to fix ALL blockers.

# CURRENT STATE (from user's README and issue list)

1. Congress_tracker: 0 rows inserted. API dead. Kadao fallback exists but run was interrupted.
2. SP500 ticker fetch: failed first run (pandas read_html missing lxml, yfinance.sp500() doesn't exist). Fixed in code but unverified.
3. System Python: externally managed (macOS). venv created but not in .gitignore.
4. Forward returns: 2-second sleep per ticker causes 10-30min runtime. Needs optimization.
5. Variable naming mismatch: ETHERSCAN_API_KEY vs ETHERSCAN_KEY (handled with fallback, but messy).
6. "Kelly" filter: matches multiple politicians (Mark Kelly, Mike Kelly, Kelly Morrison).
7. Requirements.txt missing lxml.
8. .gitignore missing venv/.

# REQUIRED FIXES (prioritized)

## FIX 1: Replace dead congressional API with working source (HIGHEST PRIORITY)

Options (choose based on what user has access to):

- A) Financial Modeling Prep API (has congressional trading endpoint) - $19/mo basic
- B) Quiver Quantitative API (has free tier, 100 calls/day) - https://api.quiverquant.com
- C) Continue with Kadao GitHub fallback but OPTIMIZE it (pre-load all JSON, batch process)

**My recommendation:** Quiver Quantitative free tier. It's alive, documented, and has congressional trading endpoint:
`https://api.quiverquant.com/beta/live/congresstrading?politician=Pelosi`

Implement as:

```python
# Add to congress_tracker.py
QUIVER_BASE = "https://api.quiverquant.com/beta/live/congresstrading"
# Fetch for each politician individually, no API key required for basic tier
```
````

## FIX 2: Optimize forward return calculation

Problem: 2-second sleep between yfinance calls for hundreds of trades = 30min run.

Solution:

- Batch fetch using `yfinance.download(tickers, start, end, group_by='ticker')`
- One API call per 100 tickers, not per trade
- Add `--quick` flag for first run: skip forward returns, just insert trades. Run returns separately.

## FIX 3: Fix SP500 ticker list permanently

Replace current fragile cascade with:

```python
def get_sp500_tickers():
    # Primary: local cached CSV (create once)
    cache_file = Path("logs/sp500_tickers.txt")
    if cache_file.exists():
        return [l.strip() for l in cache_file.open() if l.strip()]

    # Secondary: direct CSV from GitHub (reliable)
    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    df = pd.read_csv(url)
    tickers = df['Symbol'].tolist()

    # Cache it
    cache_file.parent.mkdir(exist_ok=True)
    cache_file.write_text("\n".join(tickers))
    return tickers
```

Remove Wikipedia and yfinance.sp500() entirely.

## FIX 4: Clean up variable naming

In init_db.py and all trackers, standardize to:

- `ETHERSCAN_API_KEY` (remove fallback confusion)
- `TELEGRAM_BOT_TOKEN` (remove TELEGRAM_TOKEN fallback)

Update .env.example to match exactly.

## FIX 5: Fix "Kelly" ambiguity

Replace string matching with exact name matching:

```python
TARGET_POLITICIANS = [
    "Nancy Pelosi",
    "Mitch McConnell",
    "Adam Schiff",
    "Zoe Lofgren",
    "Pete Sessions",
    "Tommy Tuberville",
    "Mark Kelly"  # Explicitly Mark Kelly (Senate)
]
```

Do NOT use partial matching.

## FIX 6: Add lxml to requirements.txt

Also add: `openpyxl` (pandas Excel fallback), `tqdm` (progress bars for long runs)

## FIX 7: Add .gitignore

Create with:

```
venv/
*.db
.env
legal_trail/*.json
paper_portfolio/
logs/*.txt
__pycache__/
*.pyc
```

## FIX 8: Add progress bar to long-running trackers

Modify congress_tracker.py to show progress when processing many trades:

```python
from tqdm import tqdm
for trade in tqdm(trades, desc="Processing congressional trades"):
    # process
```

## FIX 9: Add resume capability for first run

If congress_tracker.py is interrupted, it should NOT re-process already-inserted trades. Add this check at start:

```python
existing = pd.read_sql("SELECT DISTINCT transaction_date, ticker, politician FROM congress_trades", conn)
# Skip trades already in DB
```

## FIX 10: Create test mode

Add `--test` flag to all trackers:

- `--test 10` = process only 10 records
- Use for quick validation without waiting 30min

# DELIVERABLES

After implementing fixes above, output:

1. Updated `congress_tracker.py` (with Quiver API or optimized Kadao)
2. Updated `requirements.txt`
3. New `fix_all.sh` script that:
   - Deletes old DB (backup first)
   - Runs init_db.py fresh
   - Runs each tracker in test mode (--test 10)
   - Runs full congress_tracker.py with progress bar
4. Updated `.env.example` with correct variable names
5. New `.gitignore`
6. Verification commands to run AFTER fixes:
   ```bash
   sqlite3 trading_signals.db "SELECT COUNT(*) FROM congress_trades;" # Should be >100
   sqlite3 trading_signals.db "SELECT COUNT(*) FROM ceo_trades WHERE forward_return_12m IS NOT NULL;"
   ```

# VERIFICATION BEFORE ACCEPTING

Run these manual checks and report results:

1. `curl https://api.quiverquant.com/beta/live/congresstrading?politician=Pelosi` (if using Quiver) — returns data?
2. `python congress_tracker.py --test 5` — inserts 5 rows?
3. `python signal_combiner.py` — generates any signals >70 confidence?

If any fail, explain why and propose alternative.

# FINAL NOTE

The AI did NOT fail — the external API died. That's not the AI's fault. The code structure is solid. Now we pivot to working data sources and add optimizations.

Output complete fixed code. Do not explain — just code and verification commands.

```

---

## What You Need To Decide

The critical choice is **FIX 1** — congressional data source:

| Source | Cost | Reliability | Rate Limits |
|--------|------|-------------|-------------|
| Quiver Quantitative | Free tier (100 calls/day) | Good | 100/day |
| Financial Modeling Prep | $19/mo | Very good | 300/day |
| Kadao GitHub (current fallback) | Free | Moderate (manual updates) | None |
| Senate Stock Watcher (if alive) | Free | Unknown | None |

**My recommendation:** Use **Quiver free tier** as primary, Kadao as fallback. I'll adjust the prompt above accordingly.

---

## Your Next Step

1. **Decide which congressional data source you want** (Quiver free is easiest)
2. **Feed the prompt above to Claude/Copilot** exactly as written
3. **Run the verification commands**
4. **Report back** with:
   - Which source you chose
   - The output of verification commands
   - Any remaining errors

I will then give you the **next phase** prompts for:
- Backtesting validation
- Optimization of confidence weights using historical data
- Deployment to Debian VM with proper process management (systemd vs cron)

You're 80% there. The API death is a setback, not a failure. Keep going.
```
