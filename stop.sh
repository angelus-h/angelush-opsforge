#!/usr/bin/env bash
# ==============================================================================
# AngelusH SRE Hub Stop Script (Linux / macOS)
# Finds and stops the running Streamlit dashboard.
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🛑 Searching for running AngelusH SRE Hub instances..."

# Find pids of 'streamlit run app.py'
PIDS=$(pgrep -f "streamlit run app.py" || true)

if [ -z "$PIDS" ]; then
    echo "✅ No AngelusH SRE Hub instances found running."
    exit 0
fi

echo "Found AngelusH SRE Hub processes with PIDs: $PIDS"

for pid in $PIDS; do
    echo "Stopping PID $pid..."
    kill -15 "$pid" 2>/dev/null || true
done

sleep 1

# Check if they are still running and force kill if necessary
PIDS_LEFT=$(pgrep -f "streamlit run app.py" || true)
if [ -n "$PIDS_LEFT" ]; then
    echo "⚠️ Some processes did not stop gracefully. Forcing termination..."
    for pid in $PIDS_LEFT; do
        kill -9 "$pid" 2>/dev/null || true
    done
fi

echo "✅ AngelusH SRE Hub stopped successfully."
