"""
Confidence Weight Optimizer.
Find optimal weights for each source based on historical backtest performance.
Run: python optimize_weights.py
"""

import json
import logging
import os
import sqlite3
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
OUTPUT_PATH = os.path.join(BASE_DIR, "optimal_weights.json")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def calculate_sharpe(returns, risk_free=0.05):
    if len(returns) < 2:
        return 0.0
    std = returns.std()
    if std == 0:
        return 0.0
    return (returns.mean() - risk_free / 252) / std * np.sqrt(252)


def main():
    log.info("Starting weight optimizer...")
    if not os.path.exists(DB_PATH):
        log.error("Database not found. Run backtest_engine.py first.")
        return

    # Load simulated trades from backtest
    with sqlite3.connect(DB_PATH) as conn:
        try:
            trades_df = pd.read_sql("SELECT * FROM backtest_results", conn)
        except Exception:
            log.error("backtest_results table not found. Please run backtest_engine.py first.")
            return

    if trades_df.empty:
        log.warning("No backtest results to optimize on. Run backtest_engine.py first.")
        return

    # Calculate historical win rates per name to act as "confidence source scores"
    win_rates = trades_df.groupby("name")["trade_return"].apply(lambda x: (x > 0).mean()).to_dict()

    # Define weight candidate values
    congress_opts = [0.2, 0.3, 0.4, 0.5]
    ceo_opts = [0.2, 0.3, 0.4]
    whale_opts = [0.1, 0.2, 0.3]

    combinations = []
    # Generate combinations that sum to 1.0
    for w_cong in congress_opts:
        for w_ceo in ceo_opts:
            for w_whale in whale_opts:
                if abs(w_cong + w_ceo + w_whale - 1.0) < 1e-9:
                    combinations.append((w_cong, w_ceo, w_whale))

    # Also add other steps of 0.05 if no combinations exist, but we have 5 exact ones
    log.info("Evaluating %d weight combinations...", len(combinations))

    results = []
    
    # Run simulation for each combination
    for w_cong, w_ceo, w_whale in combinations:
        # Simulate signal scores for each trade
        simulated_returns = []
        for idx, row in trades_df.iterrows():
            name = row["name"]
            wr = win_rates.get(name, 0.5)
            
            # Score calculation
            if row["source"] == "congress":
                score = wr * w_cong
            else:  # ceo
                score = wr * w_ceo

            # If score is positive, we scale trade return by score (weighted trade performance)
            simulated_returns.append(row["trade_return"] * score)

        portfolio_returns = pd.Series(simulated_returns)
        sharpe = calculate_sharpe(portfolio_returns)
        avg_ret = portfolio_returns.mean()

        results.append({
            "congress": w_cong,
            "ceo": w_ceo,
            "whale": w_whale,
            "options": 0.0,
            "sharpe": sharpe,
            "avg_return": avg_ret
        })

    # Sort results by Sharpe ratio
    results_df = pd.DataFrame(results).sort_values("sharpe", ascending=False)

    print("\n=== WEIGHT OPTIMIZATION SENSITIVITY ANALYSIS ===")
    print(results_df.to_string(index=False, formatters={
        "congress": "{:.2f}".format,
        "ceo": "{:.2f}".format,
        "whale": "{:.2f}".format,
        "options": "{:.2f}".format,
        "sharpe": "{:.4f}".format,
        "avg_return": "{:.2%}".format
    }))

    best = results_df.iloc[0]
    best_weights = {
        "congress": float(best["congress"]),
        "ceo": float(best["ceo"]),
        "whale": float(best["whale"]),
        "options": 0.0
    }

    log.info("Optimal Weights found: Congress=%.2f, CEO=%.2f, Whale=%.2f (Sharpe: %.4f)",
             best_weights["congress"], best_weights["ceo"], best_weights["whale"], best["sharpe"])

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(best_weights, f, indent=2)
    log.info("Saved optimal weights to %s", OUTPUT_PATH)


if __name__ == "__main__":
    main()
