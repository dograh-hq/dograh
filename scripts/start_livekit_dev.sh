#!/usr/bin/env bash
set -e

###############################################################################
### Start LiveKit stack + dograh-livekit worker (DEV MODE)
###
### This script:
###   1. Starts LiveKit OSS via docker compose (~/dev/livekit/)
###   2. Starts the dograh-livekit AgentServer worker
###   3. Waits for LiveKit health check
###
### Prerequisites:
###   - ~/dev/livekit/docker-compose.yml
###   - dograh-livekit/.venv (built by setup)
###   - Dograh API running with DOGRAH_INTERNAL_TOKEN set
###############################################################################

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"

LIVEKIT_DIR="${LIVEKIT_DIR:-$HOME/dev/livekit}"
LIVEKIT_COMPOSE_FILE="$LIVEKIT_DIR/docker-compose.yml"

DOGRAH_LIVEKIT_DIR="$BASE_DIR/dograh-livekit"
VENV_PATH="$DOGRAH_LIVEKIT_DIR/.venv"

RUN_DIR="$BASE_DIR/run"
LOG_DIR="$BASE_DIR/logs"

LIVEKIT_HEALTH_URL="${LIVEKIT_HEALTH_URL:-http://localhost:7880}"
LIVEKIT_HEALTH_MAX_ATTEMPTS=${LIVEKIT_HEALTH_MAX_ATTEMPTS:-30}
LIVEKIT_HEALTH_INTERVAL=${LIVEKIT_HEALTH_INTERVAL:-2}

###############################################################################
### 1) Load environment
###############################################################################

if [[ -f "$DOGRAH_LIVEKIT_DIR/.env" ]]; then
  set -a && . "$DOGRAH_LIVEKIT_DIR/.env" && set +a
  echo "Loaded dograh-livekit/.env"
else
  echo "Warning: dograh-livekit/.env not found. Copy .env.example and configure."
fi

# Also load Dograh's env for shared vars (DOGRAH_INTERNAL_TOKEN)
if [[ -f "$BASE_DIR/api/.env" ]]; then
  set -a && . "$BASE_DIR/api/.env" && set +a
fi

###############################################################################
### 2) Start LiveKit stack
###############################################################################

echo ""
echo "━━━ LiveKit Stack ━━━"

if [[ ! -f "$LIVEKIT_COMPOSE_FILE" ]]; then
  echo "Error: LiveKit compose file not found at $LIVEKIT_COMPOSE_FILE"
  exit 1
fi

cd "$LIVEKIT_DIR"
echo "Starting LiveKit (docker compose)..."
docker compose up -d --wait 2>&1 | sed 's/^/  /'

echo "LiveKit stack started."

###############################################################################
### 3) Start dograh-livekit worker
###############################################################################

echo ""
echo "━━━ dograh-livekit worker ━━━"

if [[ ! -d "$VENV_PATH" ]]; then
  echo "Error: venv not found at $VENV_PATH. Run: cd dograh-livekit && python -m venv .venv && source .venv/bin/activate && pip install -e \".[dev]\""
  exit 1
fi

mkdir -p "$RUN_DIR" "$LOG_DIR"

# Stop old worker
PIDFILE="$RUN_DIR/dograh_livekit.pid"
if [[ -f "$PIDFILE" ]]; then
  OLDPID=$(<"$PIDFILE")
  if kill -0 "$OLDPID" 2>/dev/null; then
    echo "Stopping old dograh-livekit worker (PID $OLDPID)..."
    kill "$OLDPID" 2>/dev/null || true
    sleep 2
    if kill -0 "$OLDPID" 2>/dev/null; then
      kill -9 "$OLDPID" 2>/dev/null || true
    fi
  fi
  rm -f "$PIDFILE"
fi

# Start worker
cd "$BASE_DIR"
source "$VENV_PATH/bin/activate"

echo "Starting dograh-livekit worker..."
python -m app.main &
PID=$!
echo $PID > "$PIDFILE"
echo "  dograh-livekit worker started (PID $PID)"

###############################################################################
### 4) Health check
###############################################################################

echo ""
echo "Waiting for LiveKit health check at $LIVEKIT_HEALTH_URL ..."

healthy=false
for ((attempt = 1; attempt <= LIVEKIT_HEALTH_MAX_ATTEMPTS; attempt++)); do
  if curl -s -o /dev/null -w "%{http_code}" "$LIVEKIT_HEALTH_URL" 2>/dev/null | grep -q "200"; then
    echo "✓ LiveKit healthy (attempt $attempt)"
    healthy=true
    break
  fi
  sleep "$LIVEKIT_HEALTH_INTERVAL"
done

if ! $healthy; then
  echo "⚠ LiveKit health check timed out. Worker is running but LiveKit may not be ready."
  echo "  Check: docker compose -f $LIVEKIT_COMPOSE_FILE ps"
fi

###############################################################################
### 5) Summary
###############################################################################

echo ""
echo "──────────────────────────────────────────────────"
echo "LiveKit + dograh-livekit stack running"
echo ""
echo "  LiveKit server:  $LIVEKIT_HEALTH_URL"
echo "  LiveKit WS:      ws://localhost:7880"
echo "  dograh-livekit:   PID $PID"
echo ""
echo "To stop:"
echo "  kill \$(cat $PIDFILE)           # Stop worker"
echo "  docker compose -f $LIVEKIT_COMPOSE_FILE down  # Stop LiveKit"
echo ""
echo "Logs: docker compose -f $LIVEKIT_COMPOSE_FILE logs -f"
echo "──────────────────────────────────────────────────"
