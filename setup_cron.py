#!/usr/bin/env python3
"""Generate crontab entries and run_all.sh."""

import os
import shutil
import stat
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable

CRON_LINES = [
    f"0 2 * * * {PYTHON} {BASE_DIR}/congress_tracker.py >> {BASE_DIR}/logs/congress.log 2>&1",
    f"0 */6 * * * {PYTHON} {BASE_DIR}/ceo_tracker.py >> {BASE_DIR}/logs/ceo.log 2>&1",
    f"*/5 * * * * {PYTHON} {BASE_DIR}/whale_tracker.py >> {BASE_DIR}/logs/whale.log 2>&1",
    f"*/15 * * * * {PYTHON} {BASE_DIR}/options_tracker.py >> {BASE_DIR}/logs/options.log 2>&1",
    f"0 */6 * * * {PYTHON} {BASE_DIR}/signal_combiner.py >> {BASE_DIR}/logs/signals.log 2>&1",
    f"0 22 * * * {PYTHON} {BASE_DIR}/paper_trader.py >> {BASE_DIR}/logs/paper.log 2>&1",
]

RUN_ALL = f"""#!/bin/bash
set -e
cd "{BASE_DIR}"
PY="{PYTHON}"
echo "=== sn9trader run_all ==="
$PY init_db.py
$PY congress_tracker.py
$PY ceo_tracker.py
$PY whale_tracker.py
$PY options_tracker.py
$PY signal_combiner.py
$PY paper_trader.py
echo "=== done ==="
"""


def make_executable(path):
    mode = os.stat(path).st_mode
    os.chmod(path, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def main():
    os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
    cron_file = os.path.join(BASE_DIR, "sn9trader.cron")
    with open(cron_file, "w", encoding="utf-8") as f:
        f.write("\n".join(CRON_LINES) + "\n")
    print(f"Wrote {cron_file}")
    print("Install with: crontab sn9trader.cron")

    run_all = os.path.join(BASE_DIR, "run_all.sh")
    with open(run_all, "w", encoding="utf-8") as f:
        f.write(RUN_ALL)
    make_executable(run_all)
    print(f"Wrote executable {run_all}")

    for script in ("congress_tracker.py", "ceo_tracker.py", "whale_tracker.py",
                   "options_tracker.py", "signal_combiner.py", "paper_trader.py",
                   "telegram_send.py", "setup_cron.py", "init_db.py"):
        path = os.path.join(BASE_DIR, script)
        if os.path.exists(path):
            make_executable(path)

    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = existing.stdout if existing.returncode == 0 else ""
        marker = "# sn9trader"
        if marker not in current:
            new_cron = current.rstrip() + f"\n{marker}\n" + "\n".join(CRON_LINES) + "\n"
            proc = subprocess.run(["crontab", "-"], input=new_cron, text=True, capture_output=True)
            if proc.returncode == 0:
                print("Crontab updated")
            else:
                print(f"Crontab install skipped: {proc.stderr.strip()}")
    except FileNotFoundError:
        print("crontab not found; use sn9trader.cron manually")


if __name__ == "__main__":
    main()
