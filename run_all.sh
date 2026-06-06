#!/bin/bash
set -e
cd "/Users/samarth/Downloads/Official/PROJECTS/CODES/Python/Projects/sn9trader"
PY="/Users/samarth/Downloads/Official/PROJECTS/CODES/Python/Projects/sn9trader/venv/bin/python3"
echo "=== sn9trader run_all ==="
$PY init_db.py
$PY congress_tracker.py
$PY ceo_tracker.py
$PY whale_tracker.py
$PY options_tracker.py
$PY signal_combiner.py
$PY paper_trader.py
echo "=== done ==="
