#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# Dograh Log Monitor — tails all services in one terminal
# Usage: ./scripts/monitor_logs.sh [local|docker|all]
# ────────────────────────────────────────────────────────────────
set -euo pipefail
MODE="${1:-local}"
DOGRAH_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; MAGENTA='\033[0;35m'; BLUE='\033[0;34m'; NC='\033[0m'

log() { echo -e "${2:-$NC}[dograh-monitor]${NC} $1"; }

monitor_docker() {
    log "Docker containers:" "$CYAN"
    docker compose -f "$DOGRAH_DIR/docker-compose-local.yaml" ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

    log "Tailing Docker logs (Ctrl+C to stop)..." "$YELLOW"
    docker compose -f "$DOGRAH_DIR/docker-compose-local.yaml" \
        logs -f --tail=50 \
        postgres redis minio 2>&1 | while IFS= read -r line; do
        if [[ "$line" == *"postgres"* ]]; then echo -e "${MAGENTA}[postgres]${NC} $line"
        elif [[ "$line" == *"redis"* ]]; then echo -e "${RED}[redis]${NC} $line"
        elif [[ "$line" == *"minio"* ]]; then echo -e "${BLUE}[minio]${NC} $line"
        else echo -e "${CYAN}[docker]${NC} $line"; fi
    done
}

monitor_mps() {
    if docker compose -f "$DOGRAH_DIR/docker-compose-local.yaml" ps local-mps 2>/dev/null | grep -q 'Up'; then
        log "MPS Gateway (Docker) — tailing..." "$GREEN"
        docker compose -f "$DOGRAH_DIR/docker-compose-local.yaml" logs -f --tail=20 local-mps 2>&1 | \
            while IFS= read -r line; do echo -e "${GREEN}[mps-gateway]${NC} $line"; done
    else
        log "MPS Gateway — not running in Docker" "$YELLOW"
        log "  Start with: docker compose -f docker-compose-local.yaml --profile local-mps up -d local-mps" "$YELLOW"
    fi
}

monitor_api() {
    log "API logs — tailing api/app.log (if file logging configured)..." "$MAGENTA"
    if [ -f "$DOGRAH_DIR/api/app.log" ]; then
        tail -f "$DOGRAH_DIR/api/app.log" | while IFS= read -r line; do echo -e "${MAGENTA}[api]${NC} $line"; done
    else
        log "  No api/app.log found. API logs go to stdout." "$YELLOW"
        log "  Start API with: cd api && LOG_LEVEL=DEBUG uvicorn api.app:app --reload --port 8000" "$YELLOW"
    fi
}

monitor_ui() {
    log "UI logs — Next.js dev server..." "$CYAN"
    log "  The UI dev server logs to its own terminal. Start with: cd ui && npm run dev" "$CYAN"
}

# ── Main ──
echo -e "${YELLOW}╔══════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║   Dograh Log Monitor — mode: $MODE   ║${NC}"
echo -e "${YELLOW}╚══════════════════════════════════════════╝${NC}"

case "$MODE" in
    docker|d)
        monitor_docker
        ;;
    local|l)
        monitor_mps &
        monitor_api &
        wait
        ;;
    all|a)
        monitor_docker &
        monitor_mps &
        monitor_api &
        wait
        ;;
    *)
        echo "Usage: $0 [docker|local|all]"
        echo "  docker — Docker containers only (postgres, redis, minio, local-mps)"
        echo "  local  — API + MPS gateway logs"
        echo "  all    — Everything"
        ;;
esac
