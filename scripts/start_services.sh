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
VENV_PATH="$(dirname "$BASE_DIR")/venv"

ARQ_WORKERS=${ARQ_WORKERS:-1}

# Log startup
cd "$BASE_DIR"
echo "Starting Dograh Services at $(date) in BASE_DIR: ${BASE_DIR}"

###############################################################################
### 1) Load environment variables
###############################################################################

# Load environment from a file if it exists
if [[ -f "$ENV_FILE" ]]; then
  set -a && . "$ENV_FILE" && set +a
fi

FASTAPI_PORT=${FASTAPI_PORT:-8000}
FASTAPI_WORKERS=${FASTAPI_WORKERS:-1}

###############################################################################
### 2) Define services
###############################################################################

# Map "service name" → "command to run"
# Using arrays for bash 3.2 compatibility
SERVICE_NAMES=(
  "ari_manager"
  "campaign_orchestrator"
  "uvicorn"
)

SERVICE_COMMANDS=(
  "python -m api.services.telephony.ari_manager"
  "python -m api.services.campaign.campaign_orchestrator"
  "uvicorn api.app:app --host 0.0.0.0 --port $FASTAPI_PORT --workers $FASTAPI_WORKERS"
)

# Add ARQ workers dynamically
for ((i=1; i<=ARQ_WORKERS; i++)); do
  SERVICE_NAMES+=("arq$i")
  SERVICE_COMMANDS+=("python -m arq api.tasks.arq.WorkerSettings --custom-log-dict api.tasks.arq.LOG_CONFIG")
done

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
for name in "${SERVICE_NAMES[@]}"; do
  pidfile="$RUN_DIR/$name.pid"

  if [[ -f $pidfile ]]; then
    oldpid=$(<"$pidfile")

    if kill -0 "$oldpid" 2>/dev/null; then
      echo "Stopping $name (PID $oldpid and its process group)…"

      # Kill the entire process group (negative PID)
      kill -TERM -"$oldpid" 2>/dev/null || kill -TERM "$oldpid" 2>/dev/null || true
      sleep 4

      if kill -0 "$oldpid" 2>/dev/null; then
        echo "⚠️  $name did not exit cleanly, forcing stop..."
        kill -KILL -"$oldpid" 2>/dev/null || kill -KILL "$oldpid" 2>/dev/null || true
        sleep 1
      fi
    fi

    rm -f "$pidfile"
  else
    echo "No PID file for $name, skipping stop."
  fi
done

# Clean up any port tracking files for uvicorn
rm -f "$RUN_DIR/uvicorn.port" "$RUN_DIR/uvicorn_new.port" "$RUN_DIR/uvicorn_old.pid"

###############################################################################
### 5) Run migrations
###############################################################################

alembic -c "$BASE_DIR/api/alembic.ini" upgrade head

###############################################################################
### 6) Prepare logs
###############################################################################

mkdir -p "$BASE_LOG_DIR" "$LOG_DIR"

# Remove old symlink and create a new one
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
    export LOG_FILE_PATH="$LOG_DIR/$name.log"
    exec $cmd >>"$LOG_DIR/$name.log" 2>&1
  ) &

  pid=$!
  echo $pid >"$RUN_DIR/$name.pid"
  echo "  Started with PID $pid"

  if [[ "$name" == "uvicorn" ]]; then
    echo "$FASTAPI_PORT" >"$RUN_DIR/uvicorn.port"
  fi
done

###############################################################################
### 8) Summary
###############################################################################

echo
echo "──────────────────────────────────────────────────"
for name in "${SERVICE_NAMES[@]}"; do
  pid=$(<"$RUN_DIR/$name.pid")
  echo "✓ $name (PID $pid) → $LOG_DIR/$name.log"
done
echo "  Rotation: ${LOG_ROTATION_SIZE:-100 MB}"
echo "  Retention: ${LOG_RETENTION:-7 days}"
echo "  Compression: ${LOG_COMPRESSION:-gz}"
echo "Logs: tail -f $LOG_DIR/*.log"
echo "Rotated logs: ls $LOG_DIR/*.log.*"
echo "To stop: run this script again or kill -TERM -<PID> for process groups"
echo "──────────────────────────────────────────────────"
