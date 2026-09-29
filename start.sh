#!/usr/bin/env bash
# ==============================================================================
# AngelusH SRE Hub Launcher Script (Linux / macOS)
# Activates .venv and starts Streamlit dashboard.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if [ ! -f ".venv/bin/activate" ]; then
    echo "⚠️  Virtual environment not found. Running './install.sh' first..."
    ./install.sh
fi

echo "🚀 Starting AngelusH SRE Hub Cockpit..."
source .venv/bin/activate
exec streamlit run app.py "$@"
