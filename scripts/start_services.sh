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

ARQ_WORKERS=${ARQ_WORKERS:-1}
LOG_TO_FILE=${LOG_TO_FILE:-true}    # Set to false in Docker to use stdout

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

UVICORN_BASE_PORT=${UVICORN_BASE_PORT:-8000}
CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
FASTAPI_WORKERS=${FASTAPI_WORKERS:-$CPU_CORES}

###############################################################################
### 1b) Safety check — refuse to start over running services
###############################################################################

if [[ -d "$RUN_DIR" ]]; then
  live_count=0
  for pidfile in "$RUN_DIR"/*.pid; do
    [[ -e "$pidfile" ]] || continue
    pid=$(<"$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      live_count=$((live_count + 1))
    fi
  done

  if [[ $live_count -gt 0 ]]; then
    echo "ERROR: $live_count service(s) are still running."
    echo ""
    echo "  Stop first:                       ./scripts/stop_services.sh"
    echo "  For a zero-downtime deploy, use:  ./scripts/rolling_update.sh"
    echo ""
    exit 1
  fi
fi

###############################################################################
### 2) Define services
###############################################################################

# Map "service name" → "command to run"
# Using arrays for bash 3.2 compatibility
SERVICE_NAMES=(
  "ari_manager"
  "campaign_orchestrator"
)

SERVICE_COMMANDS=(
  "python -m api.services.telephony.ari_manager"
  "python -m api.services.campaign.campaign_orchestrator"
)

# Add uvicorn workers on separate ports (behind nginx least_conn)
for ((w=0; w<FASTAPI_WORKERS; w++)); do
  port=$((UVICORN_BASE_PORT + w))
  SERVICE_NAMES+=("uvicorn_$port")
  SERVICE_COMMANDS+=("uvicorn api.app:app --host 127.0.0.1 --port $port")
done

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

mkdir -p "$RUN_DIR"

NGINX_UPSTREAM_TEMPLATE="$BASE_DIR/nginx/dograh_upstream.conf.template"
NGINX_UPSTREAM_CONF="/etc/nginx/conf.d/dograh_upstream.conf"

###############################################################################
### 4) Run migrations
###############################################################################

alembic -c "$BASE_DIR/api/alembic.ini" upgrade head

###############################################################################
### 7) Prepare logs
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
### 8) Start services
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
      # Log to stdout/stderr for Docker
      exec $cmd
    fi
  ) &

  pid=$!
  echo $pid >"$RUN_DIR/$name.pid"
  echo "  Started with PID $pid"

done

# Cold start always uses band A (for rolling_update.sh dual-band strategy)
echo "A" > "$RUN_DIR/active_band"

###############################################################################
### 8) Generate nginx upstream config & reload
###############################################################################

if [[ -f "$NGINX_UPSTREAM_TEMPLATE" ]]; then
  # Build upstream server list from worker ports
  UPSTREAM_SERVERS=""
  for ((w=0; w<FASTAPI_WORKERS; w++)); do
    port=$((UVICORN_BASE_PORT + w))
    UPSTREAM_SERVERS="${UPSTREAM_SERVERS}    server 127.0.0.1:${port};\n"
  done

  # Generate upstream config from template
  sed -e "s|{{UVICORN_UPSTREAM_SERVERS}}|${UPSTREAM_SERVERS}|" \
      "$NGINX_UPSTREAM_TEMPLATE" | sudo tee "$NGINX_UPSTREAM_CONF" > /dev/null

  echo "Generated nginx upstream config with $FASTAPI_WORKERS workers (ports ${UVICORN_BASE_PORT}-$((UVICORN_BASE_PORT + FASTAPI_WORKERS - 1)))"

  # Test and reload nginx
  if sudo nginx -t 2>/dev/null; then
    sudo systemctl reload nginx
    echo "Nginx reloaded successfully"
  else
    echo "ERROR: nginx config test failed, not reloading"
    sudo nginx -t
    exit 1
  fi
fi

###############################################################################
### 9) Summary
###############################################################################

echo
echo "──────────────────────────────────────────────────"
echo "Mode: PRODUCTION"
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
