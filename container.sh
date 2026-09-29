#!/usr/bin/env bash
# ==============================================================================
# AngelusH SRE Hub Container Builder & Runner
# Helper script to build and run the Docker container.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

COMMAND=$1

case "$COMMAND" in
    build)
        echo "🐳 Building AngelusH SRE Hub Docker image..."
        if command -v podman &> /dev/null; then
            podman build -t sre-hub:latest .
        else
            docker build -t sre-hub:latest .
        fi
        echo "✅ Build complete!"
        ;;
    up)
        echo "🚀 Starting AngelusH SRE Hub container in the background..."
        if command -v podman-compose &> /dev/null; then
            podman-compose up -d
        else
            docker-compose up -d
        fi
        echo "🌐 AngelusH SRE Hub is available at http://localhost:8501"
        ;;
    down)
        echo "🛑 Stopping AngelusH SRE Hub container..."
        if command -v podman-compose &> /dev/null; then
            podman-compose down
        else
            docker-compose down
        fi
        echo "✅ Container stopped."
        ;;
    *)
        echo "Usage: ./container.sh {build|up|down}"
        echo ""
        echo "  build : Build the container image"
        echo "  up    : Start the container using docker-compose (background)"
        echo "  down  : Stop the container"
        exit 1
        ;;
esac
