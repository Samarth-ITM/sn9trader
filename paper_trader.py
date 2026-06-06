"""
Paper trading engine.
Supports live forward paper trading and historical signal backtest.
Run:
  python paper_trader.py --live (default)
  python paper_trader.py --historical
"""

import argparse
import logging
import os
import sqlite3
from datetime import datetime, timedelta

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from tqdm import tqdm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
SLIP_BUY = 1.001
SLIP_SELL = 0.999
RISK_FREE = 0.05

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def get_env_sizing():
    sizing = os.getenv("POSITION_SIZING", "fixed").lower()
    return sizing


def get_env_stop_loss():
    try:
        return float(os.getenv("STOP_LOSS_PCT", "0.0"))
    except (TypeError, ValueError):
        return 0.0


def get_price(ticker):
    t = ticker.replace(".", "-")
    try:
        data = yf.Ticker(t).history(period="1d")
        if data.empty:
            return None
        return float(data["Close"].iloc[-1])
    except Exception:
        return None


def get_position_size(confidence):
    sizing = get_env_sizing()
    base = 10_000
    if sizing == "confidence" and confidence:
        return base * (confidence / 100.0)
    return base


def check_live_stop_loss(conn, stop_loss_pct):
    if not stop_loss_pct or stop_loss_pct <= 0:
        return 0
    
    rows = conn.execute(
        """SELECT pp.signal_id, pp.entry_price, pp.entry_date, pp.shares, s.ticker
           FROM paper_portfolio pp
           JOIN signals s ON s.id = pp.signal_id
           WHERE pp.exit_date IS NULL AND s.paper_status='open'"""
    ).fetchall()
    
    closed = 0
    for sid, entry, entry_date, shares, ticker in rows:
        try:
            entry_dt = datetime.fromisoformat(entry_date)
            # Download recent daily history from entry to today
            t = ticker.replace(".", "-")
            hist = yf.download(t, start=entry_dt.strftime("%Y-%m-%d"), progress=False, auto_adjust=True)
            if hist.empty:
                continue
            
            trigger_p = entry * (1 - stop_loss_pct)
            lows = hist["Low"] if "Low" in hist else hist["Close"]
            below_trigger = lows[lows < trigger_p]
            if not below_trigger.empty:
                trigger_date = below_trigger.index[0]
                exit_p = trigger_p * SLIP_SELL
                pnl = (exit_p - entry) * shares
                conn.execute(
                    """UPDATE paper_portfolio SET exit_price=?, exit_date=?, pnl=?
                       WHERE signal_id=? AND exit_date IS NULL""",
                    (exit_p, trigger_date.strftime("%Y-%m-%d"), pnl, sid),
                )
                conn.execute("UPDATE signals SET paper_status='stop_loss' WHERE id=?", (sid,))
                closed += 1
                log.info("STOP LOSS TRIGGERED: %s closed @ %.2f on %s", ticker, exit_p, trigger_date.date())
        except Exception as exc:
            log.warning("Stop loss check failed for %s: %s", ticker, exc)
            
    conn.commit()
    return closed


def open_positions(conn):
    rows = conn.execute(
        """SELECT s.id, s.ticker, s.suggested_hold, s.created_at, s.confidence
           FROM signals s
           WHERE s.paper_status IS NULL"""
    ).fetchall()
    opened = 0
    for sid, ticker, hold, created, confidence in rows:
        price = get_price(ticker)
        if not price:
            continue
        entry = price * SLIP_BUY
        size = get_position_size(confidence)
        shares = size / entry
        conn.execute(
            """INSERT INTO paper_portfolio
               (signal_id,entry_price,entry_date,shares) VALUES (?,?,?,?)""",
            (sid, entry, datetime.utcnow().isoformat(), shares),
        )
        conn.execute("UPDATE signals SET paper_status='open' WHERE id=?", (sid,))
        opened += 1
        log.info("Opened %s @ %.2f (size: $%.0f)", ticker, entry, size)
    conn.commit()
    return opened


def close_positions(conn):
    rows = conn.execute(
        """SELECT pp.signal_id, pp.entry_price, pp.entry_date, pp.shares, s.ticker, s.suggested_hold
           FROM paper_portfolio pp
           JOIN signals s ON s.id = pp.signal_id
           WHERE pp.exit_date IS NULL AND s.paper_status='open'"""
    ).fetchall()
    closed = 0
    for sid, entry, entry_date, shares, ticker, hold in rows:
        try:
            entry_dt = datetime.fromisoformat(entry_date)
        except ValueError:
            continue
        if datetime.utcnow() < entry_dt + timedelta(days=hold or 90):
            continue
        price = get_price(ticker)
        if not price:
            continue
        exit_p = price * SLIP_SELL
        pnl = (exit_p - entry) * shares
        conn.execute(
            """UPDATE paper_portfolio SET exit_price=?, exit_date=?, pnl=?
               WHERE signal_id=? AND exit_date IS NULL""",
            (exit_p, datetime.utcnow().isoformat(), pnl, sid),
        )
        conn.execute("UPDATE signals SET paper_status='closed' WHERE id=?", (sid,))
        closed += 1
        log.info("Closed %s pnl=%.2f", ticker, pnl)
    conn.commit()
    return closed


def plot_equity_curve(df_trades, filename="paper_portfolio/equity_curve.png"):
    if df_trades.empty:
        return
    df_sorted = df_trades.sort_values("exit_date").copy()
    initial_equity = 100_000
    df_sorted["cum_pnl"] = df_sorted["pnl"].cumsum()
    df_sorted["equity"] = initial_equity + df_sorted["cum_pnl"]
    
    plt.style.use('dark_background')
    plt.figure(figsize=(10, 5))
    plt.plot(df_sorted["exit_date"], df_sorted["equity"], color="#58a6ff", linewidth=2)
    plt.title("Paper Portfolio Cumulative Equity Curve", color="#c9d1d9")
    plt.xlabel("Exit Date", color="#c9d1d9")
    plt.ylabel("Equity ($)", color="#c9d1d9")
    plt.grid(True, linestyle="--", alpha=0.3)
    plt.gcf().autofmt_xdate()
    
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    plt.savefig(filename, bbox_inches='tight')
    plt.close()
    log.info("Saved equity curve to %s", filename)


def weekly_report(conn):
    if datetime.utcnow().weekday() != 0:  # Monday
        return None
    
    # Generate Equity Curve
    trades = pd.read_sql("SELECT pnl, entry_date, exit_date FROM paper_portfolio WHERE pnl IS NOT NULL", conn)
    if trades.empty:
        return None
    
    plot_equity_curve(trades)

    pnls = list(trades["pnl"])
    total_return = sum(pnls) / 100_000 * 100  # Return based on 100k starting equity
    rets = trades["pnl"] / 100_000
    sharpe = (rets.mean() - RISK_FREE / 52) / (rets.std() + 1e-9) * np.sqrt(52)
    cum = rets.cumsum()
    max_dd = float((cum - cum.cummax()).min()) * 100
    
    # Calculate win rates
    win_rate = (trades["pnl"] > 0).mean() * 100
    
    # Get upcoming exits
    upcoming_exits = conn.execute(
        """SELECT s.ticker, pp.entry_date, s.suggested_hold
           FROM paper_portfolio pp
           JOIN signals s ON s.id = pp.signal_id
           WHERE pp.exit_date IS NULL
           ORDER BY pp.entry_date ASC LIMIT 5"""
    ).fetchall()
    
    exits_text = ""
    for ticker, entry_d, hold in upcoming_exits:
        try:
            entry_dt = datetime.fromisoformat(entry_d)
            exit_dt = entry_dt + timedelta(days=hold)
            exits_text += f"\n  - {ticker} exits around {exit_dt.strftime('%Y-%m-%d')}"
        except Exception:
            pass
            
    try:
        spy = yf.download("SPY", period="3mo", progress=False, auto_adjust=True)
        spy_ret = (float(spy["Close"].iloc[-1]) / float(spy["Close"].iloc[0]) - 1) * 100
    except Exception:
        spy_ret = 0.0

    return {
        "total_return_pct": round(total_return, 2),
        "sharpe": round(float(sharpe), 2),
        "max_drawdown_pct": round(max_dd, 2),
        "vs_spy_pct": round(total_return - spy_ret, 2),
        "win_rate_pct": round(win_rate, 1),
        "trades": len(pnls),
        "exits": exits_text
    }


def run_historical_simulation():
    log.info("Running historical paper portfolio simulation...")
    if not os.path.exists(DB_PATH):
        log.error("Database not found. Run backtest_engine.py first.")
        return

    # Load all signals from the signals table
    with sqlite3.connect(DB_PATH) as conn:
        signals = pd.read_sql("SELECT * FROM signals ORDER BY created_at ASC", conn)
        
    if signals.empty:
        log.warning("No signals found in the signals table to backtest.")
        return

    log.info("Simulating paper trading over %d signals...", len(signals))
    
    sim_trades = []
    stop_loss_pct = get_env_stop_loss()
    log.info("Simulation config: stop_loss_pct=%.2f, sizing=%s", stop_loss_pct, get_env_sizing())

    tickers = set(signals["ticker"].unique())
    if not tickers:
        return

    # Find date range for price downloads
    signals["created_dt"] = pd.to_datetime(signals["created_at"])
    min_date = signals["created_dt"].min() - timedelta(days=5)
    max_date = signals["created_dt"].max() + timedelta(days=200)
    today = datetime.now()
    if max_date > today + timedelta(days=1):
        max_date = today + timedelta(days=1)

    log.info("Downloading historical daily data for %d tickers...", len(tickers))
    try:
        prices = yf.download(list(tickers), start=min_date.strftime("%Y-%m-%d"), end=max_date.strftime("%Y-%m-%d"), group_by="ticker", progress=False, auto_adjust=True)
    except Exception as exc:
        log.error("Yfinance download failed: %s", exc)
        return

    for idx, sig in tqdm(signals.iterrows(), total=len(signals), desc="Processing historical signals"):
        ticker = sig["ticker"]
        created_dt = sig["created_dt"]
        entry_dt = created_dt + timedelta(days=2)
        hold = int(sig["suggested_hold"] or 90)
        exit_dt = entry_dt + timedelta(days=hold)
        
        if exit_dt > datetime.now():
            continue

        # Helper to get price
        def get_local_price(t, date, price_type="Close"):
            if t not in prices:
                return None
            try:
                if isinstance(prices.columns, pd.MultiIndex):
                    sub = prices[t]
                else:
                    sub = prices
                
                col = sub[price_type].dropna()
                idx_matches = col.index[col.index >= pd.Timestamp(date)]
                if not idx_matches.empty:
                    return float(col.loc[idx_matches[0]])
            except Exception:
                pass
            return None

        entry_price = get_local_price(ticker, entry_dt, "Close")
        if not entry_price or entry_price <= 0:
            continue

        size = get_position_size(sig["confidence"])
        shares = size / (entry_price * SLIP_BUY)
        
        # Stop loss check
        exit_p = None
        exit_date_str = exit_dt.strftime("%Y-%m-%d")
        
        if stop_loss_pct > 0:
            trigger_p = entry_price * (1 - stop_loss_pct)
            # Fetch daily Low prices from entry_dt to exit_dt
            try:
                if ticker in prices:
                    sub = prices[ticker] if isinstance(prices.columns, pd.MultiIndex) else prices
                    lows = sub["Low"].dropna()
                    period_lows = lows[(lows.index >= pd.Timestamp(entry_dt)) & (lows.index <= pd.Timestamp(exit_dt))]
                    below = period_lows[period_lows < trigger_p]
                    if not below.empty:
                        exit_date_str = below.index[0].strftime("%Y-%m-%d")
                        exit_p = trigger_p * SLIP_SELL
            except Exception:
                pass

        if exit_p is None:
            exit_p = get_local_price(ticker, exit_dt, "Close")
            if exit_p:
                exit_p *= SLIP_SELL

        if not exit_p:
            continue

        pnl = (exit_p - (entry_price * SLIP_BUY)) * shares
        sim_trades.append({
            "ticker": ticker,
            "entry_price": entry_price * SLIP_BUY,
            "entry_date": entry_dt.strftime("%Y-%m-%d"),
            "exit_price": exit_p,
            "exit_date": exit_date_str,
            "shares": shares,
            "pnl": pnl
        })

    df_sim = pd.DataFrame(sim_trades)
    if df_sim.empty:
        log.warning("No trades executed in historical simulation.")
        return

    plot_equity_curve(df_sim)
    
    win_rate = (df_sim["pnl"] > 0).mean() * 100
    total_pnl = df_sim["pnl"].sum()
    log.info("Simulation Summary:")
    log.info("  Total Trades: %d", len(df_sim))
    log.info("  Win Rate: %.1f%%", win_rate)
    log.info("  Total Return P&L: $%.2f", total_pnl)


def main():
    parser = argparse.ArgumentParser(description="Paper Trading Engine")
    parser.add_argument("--historical", action="store_true", help="Run historical backtest simulation")
    parser.add_argument("--live", action="store_true", help="Run live paper trading engine")
    args = parser.parse_args()

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    
    if args.historical:
        run_historical_simulation()
        return

    # Default is Live mode
    stop_loss = get_env_stop_loss()
    with sqlite3.connect(DB_PATH) as conn:
        # Check stop loss first
        closed_sl = check_live_stop_loss(conn, stop_loss)
        opened = open_positions(conn)
        closed = close_positions(conn)
        report = weekly_report(conn)
        
    print(f"Paper trader (live): opened={opened} closed_exits={closed} closed_stoploss={closed_sl}")
    if report:
        print(f"Weekly report: {report}")
        try:
            from telegram_send import send_signal_sync
            send_signal_sync({
                "ticker": "PORTFOLIO_REPORT",
                "confidence": 0,
                "sources_list": [
                    f"Return: {report['total_return_pct']}%",
                    f"Sharpe: {report['sharpe']}",
                    f"Max Drawdown: {report['max_drawdown_pct']}%",
                    f"Win Rate: {report['win_rate_pct']}%",
                    f"Trades: {report['trades']}",
                    f"Exits: {report['exits']}"
                ],
                "hold_days": 0,
                "legal_trail_path": "paper_portfolio/equity_curve.png" if os.path.exists("paper_portfolio/equity_curve.png") else "",
            })
        except Exception as exc:
            log.warning("Report telegram skipped: %s", exc)


if __name__ == "__main__":
    main()
