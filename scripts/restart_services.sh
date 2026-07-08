#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
cd "$BASE_DIR"

echo "🔄 Restarting all Dograh services..."
echo ""

###############################################################################
# Stop services
###############################################################################
echo "⏹️  Stopping services..."
"$BASE_DIR/scripts/stop_services.sh" 2>/dev/null || true

# In case PID files are stale, kill any remaining uvicorn/arq/ari processes
pkill -f "uvicorn api.app:app" 2>/dev/null || true
pkill -f "python -m arq" 2>/dev/null || true
pkill -f "ari_manager" 2>/dev/null || true
pkill -f "campaign_orchestrator" 2>/dev/null || true
sleep 2

###############################################################################
# Start services
###############################################################################
echo "▶️  Starting services..."
exec "$BASE_DIR/scripts/start_services_dev.sh"
