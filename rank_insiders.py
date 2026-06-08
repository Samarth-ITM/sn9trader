import datetime
import json
import logging
import os
import sqlite3

import pandas as pd
import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "trading_signals.db")
RANKINGS_PATH = os.path.join(BASE_DIR, "rankings.json")

logging.basicConfig(level=logging.INFO, format="%(message)s") # Cleaner format for console
log = logging.getLogger(__name__)

DISCLAIMER = """
=============================================================================
WARNING: This tool is for EDUCATIONAL PURPOSES ONLY.
The data provided does NOT constitute financial advice. Past performance is
not indicative of future results. Insider and Whale tracking has inherent lags.
Trade at your own risk.
=============================================================================
"""

def send_telegram_message(token, text):
    try:
        # Assuming there's a chat ID we could send to, but we don't have one in .env
        # If we had one, we'd use it here. We'll skip sending if chat_id isn't known.
        # This is a placeholder for actual Telegram logic if the user adds a chat_id.
        pass
    except Exception as e:
        log.warning(f"Telegram error: {e}")

def get_freshness(last_date_str):
    if not last_date_str:
        return 999
    try:
        last_date = datetime.datetime.strptime(last_date_str, "%Y-%m-%d").date()
        return (datetime.datetime.now().date() - last_date).days
    except ValueError:
        return 999

def rank_congress(conn):
    query = "SELECT name, transaction_date, forward_return_90d FROM congress_trades"
    df = pd.read_sql_query(query, conn)
    if df.empty:
        return []

    # Filter out trades where 90d return hasn't happened yet
    df_resolved = df.dropna(subset=['forward_return_90d']).copy()
    
    rankings = []
    for name, group in df.groupby('name'):
        resolved_group = df_resolved[df_resolved['name'] == name]
        total_trades = len(group)
        resolved_trades = len(resolved_group)
        
        win_count = len(resolved_group[resolved_group['forward_return_90d'] > 0])
        win_rate = win_count / resolved_trades if resolved_trades > 0 else 0
        
        avg_return = resolved_group['forward_return_90d'].mean() if resolved_trades > 0 else 0
        median_return = resolved_group['forward_return_90d'].median() if resolved_trades > 0 else 0
        std_dev = resolved_group['forward_return_90d'].std() if resolved_trades > 1 else 0
        
        last_trade_date = group['transaction_date'].max()
        freshness = get_freshness(last_trade_date)
        
        # Composite Score Formula (Arbitrary, rewards high win rate, high avg return, low std dev, high trade volume)
        # We cap std dev to avoid div by zero
        safe_std = max(std_dev, 0.01)
        composite_score = (win_rate * 50) + (avg_return * 100) - (safe_std * 10) + min(total_trades, 50)
        
        # Decay score based on freshness
        if freshness > 90:
             composite_score *= 0.8
        elif freshness > 180:
             composite_score *= 0.5
             
        if resolved_trades > 0: # Only rank if they have resolved trades
             rankings.append({
                 "type": "congress",
                 "name": name,
                 "total_trades": total_trades,
                 "resolved_trades": resolved_trades,
                 "win_rate": round(win_rate, 2),
                 "avg_return_90d": round(avg_return, 4),
                 "median_return_90d": round(median_return, 4),
                 "std_dev_return": round(std_dev, 4),
                 "freshness_days": freshness,
                 "composite_score": round(composite_score, 2)
             })
             
    return sorted(rankings, key=lambda x: x['composite_score'], reverse=True)

def rank_ceos(conn):
    query = "SELECT name, ticker, transaction_date, forward_return_90d FROM ceo_trades"
    df = pd.read_sql_query(query, conn)
    if df.empty:
        return []

    # Calculate cluster buys (multiple insiders buying same ticker in 7 days)
    # This is a bit complex in pandas, we'll do a simple check
    cluster_bonus_names = set()
    df['date'] = pd.to_datetime(df['transaction_date'], errors='coerce')
    valid_dates = df.dropna(subset=['date'])
    
    for ticker, t_group in valid_dates.groupby('ticker'):
        t_group = t_group.sort_values('date')
        for i in range(len(t_group)):
            current_date = t_group.iloc[i]['date']
            mask = (t_group['date'] >= current_date) & (t_group['date'] <= current_date + pd.Timedelta(days=7))
            recent_buyers = t_group[mask]['name'].nunique()
            if recent_buyers > 2:
                cluster_bonus_names.update(t_group[mask]['name'].unique())

    df_resolved = df.dropna(subset=['forward_return_90d']).copy()
    
    rankings = []
    for name, group in df.groupby('name'):
        resolved_group = df_resolved[df_resolved['name'] == name]
        total_trades = len(group)
        resolved_trades = len(resolved_group)
        
        win_count = len(resolved_group[resolved_group['forward_return_90d'] > 0])
        win_rate = win_count / resolved_trades if resolved_trades > 0 else 0
        
        avg_return = resolved_group['forward_return_90d'].mean() if resolved_trades > 0 else 0
        median_return = resolved_group['forward_return_90d'].median() if resolved_trades > 0 else 0
        std_dev = resolved_group['forward_return_90d'].std() if resolved_trades > 1 else 0
        
        last_trade_date = group['transaction_date'].max()
        freshness = get_freshness(last_trade_date)
        
        safe_std = max(std_dev, 0.01)
        composite_score = (win_rate * 50) + (avg_return * 100) - (safe_std * 10) + min(total_trades, 20)
        
        if name in cluster_bonus_names:
            composite_score *= 1.2
            
        if freshness > 180:
             composite_score *= 0.5
             
        if resolved_trades > 0:
             rankings.append({
                 "type": "ceo",
                 "name": name,
                 "total_trades": total_trades,
                 "resolved_trades": resolved_trades,
                 "win_rate": round(win_rate, 2),
                 "avg_return_90d": round(avg_return, 4),
                 "median_return_90d": round(median_return, 4),
                 "std_dev_return": round(std_dev, 4),
                 "freshness_days": freshness,
                 "composite_score": round(composite_score, 2),
                 "cluster_buy_bonus": name in cluster_bonus_names
             })
             
    return sorted(rankings, key=lambda x: x['composite_score'], reverse=True)

def rank_whales(conn):
    query = "SELECT wallet, amount_usd, direction, timestamp, forward_return_7d FROM whale_tx"
    df = pd.read_sql_query(query, conn)
    if df.empty:
        return []

    df_resolved = df.dropna(subset=['forward_return_7d']).copy()
    
    rankings = []
    for wallet, group in df.groupby('wallet'):
        resolved_group = df_resolved[df_resolved['wallet'] == wallet]
        total_txs = len(group)
        
        # Calculate win rate for 'OUT' directions (buying tokens, sending ETH out)
        # This is highly context dependent. Assuming OUT means buying an altcoin.
        # We will just look at all resolved trades for simplicity.
        
        win_count = len(resolved_group[resolved_group['forward_return_7d'] > 0])
        resolved_txs = len(resolved_group)
        win_rate = win_count / resolved_txs if resolved_txs > 0 else 0
        
        avg_position_usd = group['amount_usd'].mean()
        
        ins = len(group[group['direction'] == 'IN'])
        outs = len(group[group['direction'] == 'OUT'])
        exchange_flow_score = outs / (ins + outs) if (ins + outs) > 0 else 0
        
        composite_score = (win_rate * 100) + (exchange_flow_score * 50)
        
        rankings.append({
             "type": "whale",
             "wallet": wallet,
             "total_txs": total_txs,
             "win_rate_7d": round(win_rate, 2),
             "avg_position_size_usd": round(avg_position_usd, 2),
             "exchange_flow_score": round(exchange_flow_score, 2),
             "composite_score": round(composite_score, 2)
        })
        
    return sorted(rankings, key=lambda x: x['composite_score'], reverse=True)

def main():
    print(DISCLAIMER)
    
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    
    with sqlite3.connect(DB_PATH) as conn:
        congress_ranks = rank_congress(conn)
        ceo_ranks = rank_ceos(conn)
        whale_ranks = rank_whales(conn)
        
        # Save to DB
        cursor = conn.cursor()
        cursor.execute("DELETE FROM rankings") # Clear old
        now = datetime.datetime.now().isoformat()
        
        for r in congress_ranks:
             cursor.execute("INSERT INTO rankings (type, name_or_wallet, score_json, timestamp) VALUES (?, ?, ?, ?)",
                            (r['type'], r['name'], json.dumps(r), now))
        for r in ceo_ranks:
             cursor.execute("INSERT INTO rankings (type, name_or_wallet, score_json, timestamp) VALUES (?, ?, ?, ?)",
                            (r['type'], r['name'], json.dumps(r), now))
        for r in whale_ranks:
             cursor.execute("INSERT INTO rankings (type, name_or_wallet, score_json, timestamp) VALUES (?, ?, ?, ?)",
                            (r['type'], r['wallet'], json.dumps(r), now))
        conn.commit()

    # Console Output
    print("\\n=== TOP CONGRESS TRADERS ===")
    for r in congress_ranks[:5]:
         print(f"{r['name']}: Score {r['composite_score']} | WinRate: {r['win_rate']:.0%} | AvgReturn90d: {r['avg_return_90d']:.1%}")

    print("\\n=== TOP CEO INSIDERS ===")
    for r in ceo_ranks[:5]:
         bonus_str = "[CLUSTER BUY]" if r['cluster_buy_bonus'] else ""
         print(f"{r['name']}: Score {r['composite_score']} | WinRate: {r['win_rate']:.0%} | AvgReturn90d: {r['avg_return_90d']:.1%} {bonus_str}")

    print("\\n=== TOP WHALES ===")
    for r in whale_ranks[:5]:
         print(f"{r['wallet'][:8]}...: Score {r['composite_score']} | WinRate7d: {r['win_rate_7d']:.0%} | AvgPos: ${r['avg_position_size_usd']:,.0f}")

    # Save to JSON
    all_ranks = {
        "congress": congress_ranks,
        "ceos": ceo_ranks,
        "whales": whale_ranks
    }
    with open(RANKINGS_PATH, "w") as f:
        json.dump(all_ranks, f, indent=4)
        
    log.info(f"\\nRankings saved to {RANKINGS_PATH}")

if __name__ == "__main__":
    main()
