#!/usr/bin/env bash
set -e  # Exit on error

###############################################################################
### CONFIGURATION
###############################################################################

# Determine BASE_DIR as parent of the scripts directory
BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"

ENV_FILE="$BASE_DIR/api/.env"
RUN_DIR="$BASE_DIR/run"                 # Where we keep *.pid
BASE_LOG_DIR="$BASE_DIR/logs"           # Base logs directory

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_DIR="$BASE_LOG_DIR/$TIMESTAMP"      # Timestamped log directory
LATEST_LINK="$BASE_LOG_DIR/latest"      # Symlink to latest logs
VENV_PATH="$BASE_DIR/venv"

LOG_TO_FILE=${LOG_TO_FILE:-true}

HEALTH_CHECK_ENDPOINT="/api/v1/health"
HEALTH_MAX_ATTEMPTS=${HEALTH_MAX_ATTEMPTS:-30}
HEALTH_INTERVAL=${HEALTH_INTERVAL:-2}

cd "$BASE_DIR"
echo "Starting Dograh Services (DEV MODE) at $(date) in BASE_DIR: ${BASE_DIR}"
echo "Auto-reload enabled for api/ directory changes"

###############################################################################
### 1) Load environment variables
###############################################################################

if [[ -f "$ENV_FILE" ]]; then
  set -a && . "$ENV_FILE" && set +a
fi

UVICORN_BASE_PORT=${UVICORN_BASE_PORT:-8000}

###############################################################################
### 2) Define services
###############################################################################

SERVICE_NAMES=(
  "ari_manager"
  "campaign_orchestrator"
  "uvicorn"
  "arq"
)

SERVICE_COMMANDS=(
  "python -m api.services.telephony.ari_manager"
  "python -m api.services.campaign.campaign_orchestrator"
  "uvicorn api.app:app --host 0.0.0.0 --port $UVICORN_BASE_PORT --reload --reload-dir api"
  "python -m arq api.tasks.arq.WorkerSettings --custom-log-dict api.tasks.arq.LOG_CONFIG"
)

###############################################################################
### 3) Activate virtual environment
###############################################################################

if [[ -d "$VENV_PATH" && -f "$VENV_PATH/bin/activate" ]]; then
  source "$VENV_PATH/bin/activate"
  echo "Virtual environment activated: $VENV_PATH"
else
  echo "Warning: Virtual environment not found at $VENV_PATH"
  echo "Continuing without virtual environment activation..."
fi

###############################################################################
### 4) Stop old services
###############################################################################

mkdir -p "$RUN_DIR"

# Function to get all descendant PIDs of a process (children, grandchildren, etc.)
get_descendants() {
  local parent_pid=$1
  local descendants=""
  local children

  # Get direct children
  children=$(pgrep -P "$parent_pid" 2>/dev/null || true)

  for child in $children; do
    # Recursively get descendants of each child
    descendants="$descendants $child $(get_descendants "$child")"
  done

  echo "$descendants"
}

# Function to kill a process and all its descendants
kill_process_tree() {
  local pid=$1
  local signal=$2
  local descendants

  descendants=$(get_descendants "$pid")

  # Kill children first (bottom-up), then parent
  for desc_pid in $descendants; do
    if kill -0 "$desc_pid" 2>/dev/null; then
      kill "$signal" "$desc_pid" 2>/dev/null || true
    fi
  done

  # Kill the parent
  if kill -0 "$pid" 2>/dev/null; then
    kill "$signal" "$pid" 2>/dev/null || true
  fi
}

for name in "${SERVICE_NAMES[@]}"; do
  pidfile="$RUN_DIR/$name.pid"

  if [[ -f $pidfile ]]; then
    oldpid=$(<"$pidfile")

    if kill -0 "$oldpid" 2>/dev/null; then
      echo "Stopping $name (PID $oldpid and all descendants)…"

      kill_process_tree "$oldpid" "-TERM"
      sleep 4

      still_alive=false
      if kill -0 "$oldpid" 2>/dev/null; then
        still_alive=true
      else
        for desc_pid in $(get_descendants "$oldpid"); do
          if kill -0 "$desc_pid" 2>/dev/null; then
            still_alive=true
            break
          fi
        done
      fi

      if $still_alive; then
        echo "⚠️  $name did not exit cleanly, forcing stop..."
        kill_process_tree "$oldpid" "-KILL"
        sleep 1
      fi
    fi

    rm -f "$pidfile"
  else
    echo "No PID file for $name, skipping stop."
  fi
done

# Clean up legacy port tracking files
rm -f "$RUN_DIR/uvicorn.port" "$RUN_DIR/uvicorn_new.port" "$RUN_DIR/uvicorn_old.pid" "$RUN_DIR/active_band"

###############################################################################
### 5) Run migrations
###############################################################################

alembic -c "$BASE_DIR/api/alembic.ini" upgrade head

###############################################################################
### 6) Prepare logs
###############################################################################

mkdir -p "$BASE_LOG_DIR" "$LOG_DIR"

if [[ -L "$LATEST_LINK" ]]; then
  rm "$LATEST_LINK"
fi
ln -s "$TIMESTAMP" "$LATEST_LINK"

echo "Log directory: $LOG_DIR"
echo "Latest symlink: $LATEST_LINK -> $TIMESTAMP"

###############################################################################
### 7) Start services
###############################################################################

for i in "${!SERVICE_NAMES[@]}"; do
  name="${SERVICE_NAMES[$i]}"
  cmd="${SERVICE_COMMANDS[$i]}"
  echo "→ Starting $name"

  (
    cd "$BASE_DIR"
    if [[ "$LOG_TO_FILE" == "true" ]]; then
      export LOG_FILE_PATH="$LOG_DIR/$name.log"
      exec $cmd >>"$LOG_DIR/$name.log" 2>&1
    else
      exec $cmd
    fi
  ) &

  pid=$!
  echo $pid >"$RUN_DIR/$name.pid"
  echo "  Started with PID $pid"

done

###############################################################################
### 8) Wait for uvicorn health check
###############################################################################

echo "Waiting for uvicorn health check at http://127.0.0.1:${UVICORN_BASE_PORT}${HEALTH_CHECK_ENDPOINT} ..."

healthy=false
for ((attempt = 1; attempt <= HEALTH_MAX_ATTEMPTS; attempt++)); do
  http_code=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://127.0.0.1:${UVICORN_BASE_PORT}${HEALTH_CHECK_ENDPOINT}" 2>/dev/null || echo "000")

  if [[ "$http_code" == "200" ]]; then
    echo "✓ uvicorn healthy (attempt $attempt)"
    healthy=true
    break
  fi
  sleep "$HEALTH_INTERVAL"
done

if ! $healthy; then
  echo "✗ uvicorn FAILED health check after $HEALTH_MAX_ATTEMPTS attempts."
  echo "  Check logs: tail -f $LOG_DIR/uvicorn.log"
  exit 1
fi

###############################################################################
### 9) Summary
###############################################################################

echo
echo "──────────────────────────────────────────────────"
echo "Mode: DEVELOPMENT (auto-reload enabled)"
echo ""
for name in "${SERVICE_NAMES[@]}"; do
  pid=$(<"$RUN_DIR/$name.pid")
  echo "✓ $name (PID $pid) → $LOG_DIR/$name.log"
done
echo ""
echo "  Rotation: ${LOG_ROTATION_SIZE:-100 MB}"
echo "  Retention: ${LOG_RETENTION:-7 days}"
echo "  Compression: ${LOG_COMPRESSION:-gz}"
echo "Logs: tail -f $LOG_DIR/*.log"
echo "Rotated logs: ls $LOG_DIR/*.log.*"
echo "To stop: ./scripts/stop_services.sh"
echo "──────────────────────────────────────────────────"
