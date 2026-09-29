#!/usr/bin/env bash
# ==============================================================================
# SRE-Hub Installation Script (Linux / macOS)
# Sets up Python virtual environment and installs dependencies.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "=================================================="
echo "🛠️  Setting up SRE-Hub in: ${SCRIPT_DIR}"
echo "=================================================="

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 is not installed or not in PATH."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment (.venv)..."
    python3 -m venv .venv
else
    echo "✅ Virtual environment (.venv) already exists."
fi

# Activate and upgrade pip
echo "🔄 Activating .venv and installing requirements..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create state directories
echo "📁 Initializing local state directories..."
mkdir -p state/{maps,logs,digests,bundles}

echo "=================================================="
echo "🎉 Setup completed successfully!"
echo "👉 Run './start.sh' to launch SRE-Hub."
echo "=================================================="
