"""
Historical Backtesting Engine.
Validate that following historical signals would have beaten the market.
Run: python backtest_engine.py --years 2
"""

import argparse
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from tqdm import tqdm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
SECTOR_CACHE_PATH = os.path.join(BASE_DIR, "logs", "ticker_sectors.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def load_sectors():
    if os.path.exists(SECTOR_CACHE_PATH):
        try:
            with open(SECTOR_CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_sectors(sectors):
    os.makedirs(os.path.dirname(SECTOR_CACHE_PATH), exist_ok=True)
    try:
        with open(SECTOR_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(sectors, f, indent=2)
    except Exception as exc:
        log.warning("Failed to save sector cache: %s", exc)


def get_sector(ticker, cache):
    t = ticker.replace(".", "-")
    if t in cache:
        return cache[t]
    try:
        log.info("Fetching sector for %s", t)
        info = yf.Ticker(t).info
        sector = info.get("sector", "Unknown")
        cache[t] = sector
        save_sectors(cache)
        time.sleep(0.2)
        return sector
    except Exception:
        cache[t] = "Unknown"
        return "Unknown"


def parse_date(date_str):
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str[:10], fmt)
        except ValueError:
            continue
    return None


def get_price_from_df(df, ticker, target_date):
    if ticker not in df:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            close = df[ticker]["Close"].dropna()
        else:
            close = df["Close"].dropna()
        if close.empty:
            return None
        idx = close.index
        on_or_after = close[idx >= pd.Timestamp(target_date)]
        if not on_or_after.empty:
            closest_date = on_or_after.index[0]
            if (closest_date - pd.Timestamp(target_date)).days <= 7:
                return float(on_or_after.iloc[0])
        before = close[idx <= pd.Timestamp(target_date)]
        if not before.empty:
            closest_date = before.index[-1]
            if (pd.Timestamp(target_date) - closest_date).days <= 7:
                return float(before.iloc[-1])
    except Exception as exc:
        log.warning("Price lookup failed for %s: %s", ticker, exc)
    return None


def calculate_sharpe(returns, risk_free=0.05):
    # Returns is a series of trade returns.
    if len(returns) < 2:
        return 0.0
    mean = returns.mean()
    std = returns.std()
    if std == 0:
        return 0.0
    return (mean - risk_free / 252) / std * np.sqrt(252)  # annualized assuming standard holding


def calculate_max_drawdown(returns):
    if len(returns) == 0:
        return 0.0
    # Sort returns chronologically to simulate equity curve
    equity = (1 + returns).cumprod()
    cummax = equity.cummax()
    drawdown = (equity - cummax) / cummax
    return float(drawdown.min())


def run_backtest(years):
    log.info("Starting historical backtest for past %d years...", years)
    if not os.path.exists(DB_PATH):
        log.error("Database not found. Run init_db.py first.")
        return None

    cutoff_date = (datetime.utcnow() - timedelta(days=years * 365)).strftime("%Y-%m-%d")
    
    with sqlite3.connect(DB_PATH) as conn:
        # 1. Fetch trades
        congress_query = f"""
            SELECT 'congress' as source, name, ticker, disclosure_date as sig_date, 90 as hold_days 
            FROM congress_trades 
            WHERE disclosure_date >= '{cutoff_date}'
        """
        ceo_query = f"""
            SELECT 'ceo' as source, name, ticker, filing_date as sig_date, 180 as hold_days 
            FROM ceo_trades 
            WHERE filing_date >= '{cutoff_date}'
        """
        
        trades_df = pd.concat([
            pd.read_sql(congress_query, conn),
            pd.read_sql(ceo_query, conn)
        ], ignore_index=True)

    if trades_df.empty:
        log.warning("No historical trades found in database.")
        return None

    log.info("Found %d historical trades to simulate", len(trades_df))

    # 2. Get unique tickers and date ranges
    tickers = set(trades_df["ticker"].unique())
    tickers.add("SPY")
    
    # Calculate dates
    trades_df["sig_dt"] = trades_df["sig_date"].apply(parse_date)
    trades_df = trades_df.dropna(subset=["sig_dt"])
    trades_df["buy_dt"] = trades_df["sig_dt"] + timedelta(days=2)
    trades_df["exit_dt"] = trades_df.apply(lambda r: r["buy_dt"] + timedelta(days=r["hold_days"]), axis=1)

    min_start = trades_df["buy_dt"].min() - timedelta(days=10)
    max_end = trades_df["exit_dt"].max() + timedelta(days=10)
    today = datetime.now()
    if max_end > today + timedelta(days=1):
        max_end = today + timedelta(days=1)

    log.info("Downloading historical prices for %d tickers from %s to %s",
             len(tickers), min_start.date(), max_end.date())
             
    try:
        prices_df = yf.download(
            list(tickers),
            start=min_start.strftime("%Y-%m-%d"),
            end=max_end.strftime("%Y-%m-%d"),
            group_by="ticker",
            progress=False,
            auto_adjust=True
        )
    except Exception as exc:
        log.error("Failed to download historical prices: %s", exc)
        return None

    # Load sectors cache
    sectors_cache = load_sectors()

    # Create backtest results table
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS backtest_results")
        conn.execute("""
            CREATE TABLE backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                name TEXT,
                ticker TEXT,
                buy_date TEXT,
                buy_price REAL,
                exit_date TEXT,
                exit_price REAL,
                trade_return REAL,
                spy_return REAL,
                alpha REAL,
                hold_days INTEGER,
                sector TEXT,
                year INTEGER
            )
        """)
        conn.commit()

    simulated_trades = []

    # 3. Simulate trades
    for idx, row in tqdm(trades_df.iterrows(), total=len(trades_df), desc="Simulating trades"):
        ticker = row["ticker"]
        buy_date = row["buy_dt"]
        exit_date = row["exit_dt"]
        
        # Check if exit is in future
        if exit_date > datetime.now():
            continue

        buy_price = get_price_from_df(prices_df, ticker, buy_date)
        exit_price = get_price_from_df(prices_df, ticker, exit_date)
        
        spy_buy = get_price_from_df(prices_df, "SPY", buy_date)
        spy_exit = get_price_from_df(prices_df, "SPY", exit_date)

        if not buy_price or not exit_price or not spy_buy or not spy_exit:
            continue

        ret = (exit_price - buy_price) / buy_price
        spy_ret = (spy_exit - spy_buy) / spy_buy
        alpha = ret - spy_ret
        sector = get_sector(ticker, sectors_cache)
        year = buy_date.year

        simulated_trades.append({
            "source": row["source"],
            "name": row["name"],
            "ticker": ticker,
            "buy_date": buy_date.strftime("%Y-%m-%d"),
            "buy_price": buy_price,
            "exit_date": exit_date.strftime("%Y-%m-%d"),
            "exit_price": exit_price,
            "trade_return": ret,
            "spy_return": spy_ret,
            "alpha": alpha,
            "hold_days": int(row["hold_days"]),
            "sector": sector,
            "year": year
        })

    if not simulated_trades:
        log.warning("No trades were successfully simulated.")
        return None

    # Write to database
    with sqlite3.connect(DB_PATH) as conn:
        for t in simulated_trades:
            conn.execute("""
                INSERT INTO backtest_results 
                (source, name, ticker, buy_date, buy_price, exit_date, exit_price, trade_return, spy_return, alpha, hold_days, sector, year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (t["source"], t["name"], t["ticker"], t["buy_date"], t["buy_price"], t["exit_date"], t["exit_price"], t["trade_return"], t["spy_return"], t["alpha"], t["hold_days"], t["sector"], t["year"]))
        conn.commit()

    log.info("Simulated %d trades successfully and saved to backtest_results", len(simulated_trades))
    return pd.DataFrame(simulated_trades)


def generate_report(df):
    if df is None or df.empty:
        return

    # Aggregate performance
    summary = df.groupby("source").agg(
        trades=("trade_return", "count"),
        win_rate=("trade_return", lambda x: (x > 0).mean()),
        avg_return=("trade_return", "mean"),
        avg_alpha=("alpha", "mean"),
        sharpe=("trade_return", calculate_sharpe),
        max_drawdown=("trade_return", calculate_max_drawdown)
    ).reset_index()

    # Standard overall metrics
    overall_win_rate = (df["trade_return"] > 0).mean()
    overall_return = df["trade_return"].mean()
    overall_alpha = df["alpha"].mean()
    overall_sharpe = calculate_sharpe(df["trade_return"])
    overall_drawdown = calculate_max_drawdown(df["trade_return"])

    overall_row = pd.DataFrame([{
        "source": "OVERALL",
        "trades": len(df),
        "win_rate": overall_win_rate,
        "avg_return": overall_return,
        "avg_alpha": overall_alpha,
        "sharpe": overall_sharpe,
        "max_drawdown": overall_drawdown
    }])
    summary = pd.concat([summary, overall_row], ignore_index=True)

    print("\n=== BACKTEST RESULTS SUMMARY ===")
    print(summary.to_string(index=False, formatters={
        "win_rate": "{:.1%}".format,
        "avg_return": "{:.1%}".format,
        "avg_alpha": "{:.1%}".format,
        "sharpe": "{:.2f}".format,
        "max_drawdown": "{:.1%}".format
    }))

    # Detail breakdowns
    politician_breakdown = df.groupby("name").agg(
        trades=("trade_return", "count"),
        win_rate=("trade_return", lambda x: (x > 0).mean()),
        avg_return=("trade_return", "mean"),
        avg_alpha=("alpha", "mean")
    ).sort_values("avg_return", ascending=False).reset_index()

    sector_breakdown = df.groupby("sector").agg(
        trades=("trade_return", "count"),
        win_rate=("trade_return", lambda x: (x > 0).mean()),
        avg_return=("trade_return", "mean"),
        avg_alpha=("alpha", "mean")
    ).sort_values("avg_return", ascending=False).reset_index()

    # Cumulative equity curve
    df_sorted = df.sort_values("buy_date").copy()
    df_sorted["cum_portfolio"] = (1 + df_sorted["trade_return"]).cumprod() - 1
    df_sorted["cum_spy"] = (1 + df_sorted["spy_return"]).cumprod() - 1

    # Plotly HTML generation
    chart_dates = list(df_sorted["buy_date"])
    chart_port = list(df_sorted["cum_portfolio"] * 100)
    chart_spy = list(df_sorted["cum_spy"] * 100)

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <title>Sn9Trader v2 Backtest Report</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        body {{
            font-family: 'Outfit', sans-serif;
            background-color: #0d1117;
            color: #c9d1d9;
            margin: 0;
            padding: 40px;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            color: #58a6ff;
            text-align: center;
        }}
        .card {{
            background: rgba(22, 27, 34, 0.8);
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #30363d;
        }}
        th {{
            background-color: #161b22;
            color: #58a6ff;
        }}
        tr:hover {{
            background-color: #1f242c;
        }}
        .win {{ color: #3fb950; }}
        .loss {{ color: #f85149; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Sn9Trader v2 Backtest Report</h1>
        
        <div class="card">
            <h2>Overall Performance</h2>
            <table>
                <tr>
                    <th>Source</th>
                    <th>Total Trades</th>
                    <th>Win Rate</th>
                    <th>Avg Return</th>
                    <th>Avg Alpha vs SPY</th>
                    <th>Sharpe Ratio</th>
                    <th>Max Drawdown</th>
                </tr>
                {"".join(f'''
                <tr>
                    <td><b>{row['source'].upper()}</b></td>
                    <td>{row['trades']}</td>
                    <td class="{"win" if row['win_rate'] >= 0.5 else "loss"}">{row['win_rate']:.1%}</td>
                    <td class="{"win" if row['avg_return'] >= 0 else "loss"}">{row['avg_return']:.1%}</td>
                    <td class="{"win" if row['avg_alpha'] >= 0 else "loss"}">{row['avg_alpha']:.1%}</td>
                    <td>{row['sharpe']:.2f}</td>
                    <td class="loss">{row['max_drawdown']:.1%}</td>
                </tr>
                ''' for _, row in summary.iterrows())}
            </table>
        </div>

        <div class="card">
            <h2>Equity Curve (Cumulative Return %)</h2>
            <div id="equity_chart" style="width:100%;height:500px;"></div>
        </div>

        <div class="card">
            <h2>Performance by Politician / Insider</h2>
            <table>
                <tr>
                    <th>Name</th>
                    <th>Trades</th>
                    <th>Win Rate</th>
                    <th>Avg Return</th>
                    <th>Avg Alpha</th>
                </tr>
                {"".join(f'''
                <tr>
                    <td>{row['name']}</td>
                    <td>{row['trades']}</td>
                    <td class="{"win" if row['win_rate'] >= 0.5 else "loss"}">{row['win_rate']:.1%}</td>
                    <td class="{"win" if row['avg_return'] >= 0 else "loss"}">{row['avg_return']:.1%}</td>
                    <td class="{"win" if row['avg_alpha'] >= 0 else "loss"}">{row['avg_alpha']:.1%}</td>
                </tr>
                ''' for _, row in politician_breakdown.iterrows())}
            </table>
        </div>

        <div class="card">
            <h2>Performance by Sector</h2>
            <table>
                <tr>
                    <th>Sector</th>
                    <th>Trades</th>
                    <th>Win Rate</th>
                    <th>Avg Return</th>
                    <th>Avg Alpha</th>
                </tr>
                {"".join(f'''
                <tr>
                    <td>{row['sector']}</td>
                    <td>{row['trades']}</td>
                    <td class="{"win" if row['win_rate'] >= 0.5 else "loss"}">{row['win_rate']:.1%}</td>
                    <td class="{"win" if row['avg_return'] >= 0 else "loss"}">{row['avg_return']:.1%}</td>
                    <td class="{"win" if row['avg_alpha'] >= 0 else "loss"}">{row['avg_alpha']:.1%}</td>
                </tr>
                ''' for _, row in sector_breakdown.iterrows())}
            </table>
        </div>
    </div>

    <script>
        var tracePortfolio = {{
            x: {json.dumps(chart_dates)},
            y: {json.dumps(chart_port)},
            mode: 'lines',
            name: 'Sn9Trader Composite Portfolio',
            line: {{color: '#58a6ff', width: 3}}
        }};

        var traceSPY = {{
            x: {json.dumps(chart_dates)},
            y: {json.dumps(chart_spy)},
            mode: 'lines',
            name: 'SPY Benchmark',
            line: {{color: '#8b949e', width: 2, dash: 'dash'}}
        }};

        var data = [tracePortfolio, traceSPY];

        var layout = {{
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: {{color: '#c9d1d9'}},
            xaxis: {{
                gridcolor: '#30363d',
                title: 'Date'
            }},
            yaxis: {{
                gridcolor: '#30363d',
                title: 'Cumulative Return (%)'
            }},
            margin: {{t: 20, b: 40, l: 60, r: 20}}
        }};

        Plotly.newPlot('equity_chart', data, layout);
    </script>
</body>
</html>
"""
    with open(os.path.join(BASE_DIR, "backtest_report.html"), "w", encoding="utf-8") as f:
        f.write(html_content)
    log.info("Backtest HTML report generated: backtest_report.html")


def main():
    parser = argparse.ArgumentParser(description="Historical Backtesting Engine")
    parser.add_argument("--years", type=int, default=2, help="Number of historical years to backtest")
    args = parser.parse_args()

    df = run_backtest(args.years)
    generate_report(df)


if __name__ == "__main__":
    main()
