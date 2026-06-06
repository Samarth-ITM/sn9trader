#!/bin/bash
# Debian VM Deployment Script for Sn9Trader v2
# Support systemd service + timers.
# Run: ./deploy.sh [--dry-run]

set -e

DRY_RUN=false
if [ "$1" == "--dry-run" ]; then
    DRY_RUN=true
    echo "=== RUNNING IN DRY RUN MODE ==="
fi

BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$BASE_DIR"

echo "1. Checking Python 3.10+ version..."
PYTHON_CMD="python3"
if ! command -v python3 &> /dev/null; then
    if command -v python &> /dev/null; then
        PYTHON_CMD="python"
    else
        echo "Error: Python is not installed."
        exit 1
    fi
fi

PY_VERSION=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$($PYTHON_CMD -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$($PYTHON_CMD -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo "Error: Python version is $PY_VERSION. Python 3.10+ is required."
    exit 1
fi
echo "Python version $PY_VERSION is OK."

echo "2. Setting up Virtual Environment (venv)..."
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON_CMD -m venv venv
else
    echo "Venv already exists."
fi

echo "3. Installing / Updating dependencies..."
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

echo "4. Verifying .env configuration..."
if [ ! -f ".env" ]; then
    echo "Warning: .env file is missing."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "Created .env from .env.example. Please fill in details!"
    else
        echo "Error: .env.example is also missing."
        exit 1
    fi
else
    echo ".env file verified."
fi

echo "5. Running Database Initialization..."
venv/bin/python init_db.py

echo "6. Running trackers in test mode..."
venv/bin/python congress_tracker.py --test 10 --quick
venv/bin/python ceo_tracker.py --test 2
# Skip whale and options tracker in test run if keys are not ready, but try running them
venv/bin/python whale_tracker.py --test 1 || echo "Whale tracker run skipped/failed (expected if Etherscan key empty)"
venv/bin/python options_tracker.py --test 5 || echo "Options tracker run skipped/failed"

echo "7. Generating systemd Service & Timer files..."
SERVICES=(congress ceo whale signal paper)
ON_CALENDARS=(
    "*-*-* 02:00:00"                # congress: daily 2 AM
    "*-*-* 00,06,12,18:00:00"       # ceo: every 6 hours
    "*:0/5"                         # whale: every 5 minutes
    "*-*-* 00,06,12,18:00:00"       # signal: every 6 hours
    "*-*-* 22:00:00"                # paper: daily 10 PM
)
DESCRIPTIONS=(
    "Sn9Trader Congressional Trades Tracker"
    "Sn9Trader CEO Insider Purchases Tracker"
    "Sn9Trader Whale Transaction Tracker"
    "Sn9Trader Signal Combiner"
    "Sn9Trader Paper Portfolio Tracker"
)
SCRIPTS=(
    "congress_tracker.py"
    "ceo_tracker.py"
    "whale_tracker.py"
    "signal_combiner.py"
    "paper_trader.py"
)

SYS_DIR="/etc/systemd/system"
TEMP_SYS_DIR="logs/systemd_temp"
mkdir -p "$TEMP_SYS_DIR"

for i in "${!SERVICES[@]}"; do
    NAME="${SERVICES[$i]}"
    DESC="${DESCRIPTIONS[$i]}"
    CAL="${ON_CALENDARS[$i]}"
    SCRIPT="${SCRIPTS[$i]}"
    
    # Service File
    SERVICE_FILE="$TEMP_SYS_DIR/sn9trader-$NAME.service"
    cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=$DESC
After=network.target

[Service]
Type=oneshot
WorkingDirectory=$BASE_DIR
ExecStart=/bin/bash -c '$BASE_DIR/venv/bin/python $BASE_DIR/$SCRIPT >> $BASE_DIR/logs/$NAME.log 2>&1'
User=$USER
EOF

    # Timer File
    TIMER_FILE="$TEMP_SYS_DIR/sn9trader-$NAME.timer"
    cat <<EOF > "$TIMER_FILE"
[Unit]
Description=$DESC Timer

[Timer]
OnCalendar=$CAL
Persistent=true

[Install]
WantedBy=timers.target
EOF

    echo "Generated service and timer files for: $NAME"
    if [ "$DRY_RUN" == "true" ]; then
        echo "----------------------------------------"
        echo "File: sn9trader-$NAME.service"
        cat "$SERVICE_FILE"
        echo "File: sn9trader-$NAME.timer"
        cat "$TIMER_FILE"
        echo "----------------------------------------"
    fi
done

if [ "$DRY_RUN" == "true" ]; then
    echo "Dry run complete. Skipping systemd installation."
    exit 0
fi

# Real Installation
echo "8. Installing to systemd directory..."
if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo to install systemd services:"
    echo "  sudo ./deploy.sh"
    exit 1
fi

for NAME in "${SERVICES[@]}"; do
    cp "$TEMP_SYS_DIR/sn9trader-$NAME.service" "$SYS_DIR/"
    cp "$TEMP_SYS_DIR/sn9trader-$NAME.timer" "$SYS_DIR/"
    
    systemctl daemon-reload
    systemctl enable "sn9trader-$NAME.timer"
    systemctl start "sn9trader-$NAME.timer"
    echo "Enabled and started sn9trader-$NAME.timer"
done

echo "=== deployment complete! ==="
