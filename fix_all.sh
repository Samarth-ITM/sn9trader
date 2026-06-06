#!/bin/bash
set -e

BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$BASE_DIR"

echo "=== Backing up old DB ==="
if [ -f trading_signals.db ]; then
    cp trading_signals.db trading_signals.db.bak
    echo "Backed up trading_signals.db to trading_signals.db.bak"
    rm trading_signals.db
fi

echo "=== Running init_db.py fresh ==="
venv/bin/python init_db.py

echo "=== Running trackers in test mode (--test 10) ==="
venv/bin/python congress_tracker.py --test 10
venv/bin/python ceo_tracker.py --test 10
venv/bin/python whale_tracker.py --test 10
venv/bin/python options_tracker.py --test 10

echo "=== Running full congress_tracker.py ==="
venv/bin/python congress_tracker.py

echo "=== Running signal_combiner.py ==="
venv/bin/python signal_combiner.py

echo "=== sn9trader fix_all complete ==="
