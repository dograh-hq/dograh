#!/usr/bin/env bash
# rolling_update.sh — Zero-downtime rolling update using dual-band port strategy
#
# Usage:
#   ./scripts/rolling_update.sh
#   DRAIN_TIMEOUT=600 ./scripts/rolling_update.sh
#
# Old workers drain active calls (WebSocket/WebRTC) before shutting down.
# Nginx switches to new workers only after every one passes health checks.
# On failure at any phase, the script rolls back: kills new workers, leaves
# old workers and nginx untouched.

set -euo pipefail

###############################################################################
### CONFIGURATION
###############################################################################

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"

ENV_FILE="$BASE_DIR/api/.env"
RUN_DIR="$BASE_DIR/run"
BASE_LOG_DIR="$BASE_DIR/logs"
LATEST_LINK="$BASE_LOG_DIR/latest"
VENV_PATH="$BASE_DIR/venv"

NGINX_UPSTREAM_TEMPLATE="$BASE_DIR/nginx/dograh_upstream.conf.template"
NGINX_UPSTREAM_CONF="/etc/nginx/conf.d/dograh_upstream.conf"

HEALTH_CHECK_ENDPOINT="/api/v1/health"
ACTIVE_CALLS_ENDPOINT="/api/v1/health/active-calls"
DOGRAH_DEVOPS_SECRET_HEADER="X-Dograh-Devops-Secret"

# Load environment
if [[ -f "$ENV_FILE" ]]; then
  set -a && . "$ENV_FILE" && set +a
fi

UVICORN_BASE_PORT=${UVICORN_BASE_PORT:-8000}
CPU_CORES=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)
FASTAPI_WORKERS=${FASTAPI_WORKERS:-$CPU_CORES}
ARQ_WORKERS=${ARQ_WORKERS:-1}

# Tuning knobs (override via environment)
DRAIN_TIMEOUT=${DRAIN_TIMEOUT:-300}          # seconds to wait for active calls to finish
DRAIN_INTERVAL=${DRAIN_INTERVAL:-5}          # seconds between active-call drain polls
STOP_TIMEOUT=${STOP_TIMEOUT:-30}             # seconds to wait for drained workers to exit after SIGTERM
HEALTH_MAX_ATTEMPTS=${HEALTH_MAX_ATTEMPTS:-30}  # per-worker health-check retries
HEALTH_INTERVAL=${HEALTH_INTERVAL:-2}        # seconds between health-check retries

cd "$BASE_DIR"

###############################################################################
### HELPERS
###############################################################################

log_info()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO:  $*"; }
log_warn()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN:  $*"; }
log_error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }

if [[ -z "${DOGRAH_DEVOPS_SECRET:-}" ]]; then
  log_error "DOGRAH_DEVOPS_SECRET is not set. Add it to $ENV_FILE before running rolling_update.sh."
  exit 1
fi
if [[ "$DOGRAH_DEVOPS_SECRET" == "change-me-dograh-devops-secret" ]]; then
  log_error "DOGRAH_DEVOPS_SECRET still has the example placeholder value. Replace it in $ENV_FILE."
  exit 1
fi

# Band port calculation: band A = base, band B = base + 100
band_base_port() {
  local band=$1
  if [[ "$band" == "A" ]]; then
    echo "$UVICORN_BASE_PORT"
  else
    echo $((UVICORN_BASE_PORT + 100))
  fi
}

opposite_band() {
  if [[ "$1" == "A" ]]; then echo "B"; else echo "A"; fi
}

# Get all descendant PIDs of a process
get_descendants() {
  local parent_pid=$1
  local descendants=""
  local children
  children=$(pgrep -P "$parent_pid" 2>/dev/null || true)
  for child in $children; do
    descendants="$descendants $child $(get_descendants "$child")"
  done
  echo "$descendants"
}

# Kill a process and all its descendants
kill_process_tree() {
  local pid=$1
  local signal=$2
  local descendants
  descendants=$(get_descendants "$pid")
  for desc_pid in $descendants; do
    if kill -0 "$desc_pid" 2>/dev/null; then
      kill "$signal" "$desc_pid" 2>/dev/null || true
    fi
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill "$signal" "$pid" 2>/dev/null || true
  fi
}

# Active in-progress call count for a single worker, via its health endpoint.
# A worker that is unreachable (already exited) reports 0, so it never blocks the
# drain. Non-200 responses or malformed bodies are hard failures: otherwise an
# auth/configuration error could be mistaken for a fully drained worker.
count_active_calls_on_port() {
  local port=$1
  local response http_code body n
  response=$(curl -sS --max-time 3 \
    -H "${DOGRAH_DEVOPS_SECRET_HEADER}: ${DOGRAH_DEVOPS_SECRET}" \
    -w $'\n%{http_code}' \
    "http://127.0.0.1:${port}${ACTIVE_CALLS_ENDPOINT}" 2>/dev/null || true)
  http_code="${response##*$'\n'}"
  body="${response%$'\n'*}"

  if [[ "$http_code" == "000" ]]; then
    printf '0'
    return 0
  fi

  if [[ "$http_code" != "200" ]]; then
    log_error "uvicorn_${port} active-calls endpoint returned HTTP ${http_code}. Check DOGRAH_DEVOPS_SECRET in $ENV_FILE."
    return 1
  fi

  n=$(printf '%s' "$body" \
    | grep -o '"active_calls"[[:space:]]*:[[:space:]]*[0-9]\+' \
    | grep -o '[0-9]\+$' || true)
  if [[ -z "$n" ]]; then
    log_error "uvicorn_${port} active-calls endpoint returned an invalid response body."
    return 1
  fi

  printf '%s' "$n"
}

###############################################################################
### ROLLBACK
###############################################################################

# Kill all new-band workers and leave old workers + nginx untouched
rollback_new_workers() {
  local new_band=$1
  local new_base
  new_base=$(band_base_port "$new_band")

  log_error "ROLLING BACK — killing new band $new_band workers"

  for ((w = 0; w < FASTAPI_WORKERS; w++)); do
    local port=$((new_base + w))
    local pidfile="$RUN_DIR/uvicorn_${port}.pid"
    if [[ -f "$pidfile" ]]; then
      local pid
      pid=$(<"$pidfile")
      if kill -0 "$pid" 2>/dev/null; then
        kill_process_tree "$pid" "-KILL"
        log_info "  Killed uvicorn_${port} (PID $pid)"
      fi
      rm -f "$pidfile"
    fi
  done

  log_error "Rollback complete. Old workers and nginx are untouched."
}

###############################################################################
### PHASE 0: PRE-FLIGHT CHECKS
###############################################################################

log_info "=== Phase 0: Pre-flight checks ==="

# Determine current and new band
if [[ -f "$RUN_DIR/active_band" ]]; then
  OLD_BAND=$(<"$RUN_DIR/active_band")
else
  log_error "No active_band file found in $RUN_DIR. Run start_services.sh first."
  exit 1
fi

NEW_BAND=$(opposite_band "$OLD_BAND")
OLD_BASE=$(band_base_port "$OLD_BAND")
NEW_BASE=$(band_base_port "$NEW_BAND")

log_info "Current band: $OLD_BAND (ports ${OLD_BASE}–$((OLD_BASE + FASTAPI_WORKERS - 1)))"
log_info "New band:     $NEW_BAND (ports ${NEW_BASE}–$((NEW_BASE + FASTAPI_WORKERS - 1)))"

# Verify at least one old worker is running
old_running=0
for ((w = 0; w < FASTAPI_WORKERS; w++)); do
  port=$((OLD_BASE + w))
  pidfile="$RUN_DIR/uvicorn_${port}.pid"
  if [[ -f "$pidfile" ]]; then
    pid=$(<"$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      old_running=$((old_running + 1))
    fi
  fi
done

if [[ $old_running -eq 0 ]]; then
  log_error "No old workers are running. Use start_services.sh for a cold start."
  exit 1
fi
log_info "Found $old_running running old worker(s)"

# Verify new ports are free
for ((w = 0; w < FASTAPI_WORKERS; w++)); do
  port=$((NEW_BASE + w))
  if ss -tln "sport = :$port" | grep -q LISTEN; then
    log_error "Port $port is already in use. Cannot start new band."
    exit 1
  fi
done
log_info "All new-band ports are free"

# Verify nginx is running
if ! pgrep -x nginx >/dev/null 2>&1; then
  log_error "nginx is not running."
  exit 1
fi
log_info "nginx is running"

# Verify Node >= 22.6 (required by api/mcp_server/ts_validator)
if ! command -v node >/dev/null 2>&1; then
  log_error "node is not installed. api/mcp_server/ts_validator requires Node >= 22.6."
  log_error "Install via: curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs"
  exit 1
fi
NODE_VERSION=$(node -v | sed 's/^v//')
NODE_MAJOR=${NODE_VERSION%%.*}
NODE_MINOR=$(echo "$NODE_VERSION" | cut -d. -f2)
if [[ $NODE_MAJOR -lt 22 ]] || { [[ $NODE_MAJOR -eq 22 ]] && [[ $NODE_MINOR -lt 6 ]]; }; then
  log_error "Node $NODE_VERSION is too old. api/mcp_server/ts_validator requires Node >= 22.6."
  log_error "Upgrade via: curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs"
  exit 1
fi
log_info "node $NODE_VERSION is new enough"

###############################################################################
### PHASE 1: RUN MIGRATIONS
###############################################################################

log_info "=== Phase 1: Running Alembic migrations ==="

# Activate virtual environment
if [[ -d "$VENV_PATH" && -f "$VENV_PATH/bin/activate" ]]; then
  source "$VENV_PATH/bin/activate"
else
  log_warn "No virtual environment at $VENV_PATH, continuing without"
fi

if ! alembic -c "$BASE_DIR/api/alembic.ini" upgrade head; then
  log_error "Alembic migration failed. Aborting — nothing has been touched."
  exit 1
fi
log_info "Migrations complete"

TS_VALIDATOR_DIR="$BASE_DIR/api/mcp_server/ts_validator"
if [[ -f "$TS_VALIDATOR_DIR/package.json" ]]; then
  log_info "Installing ts_validator npm dependencies"
  if ! (cd "$TS_VALIDATOR_DIR" && npm install); then
    log_error "npm install for ts_validator failed. Aborting — nothing has been touched."
    exit 1
  fi
fi

###############################################################################
### PHASE 2: START NEW WORKERS
###############################################################################

log_info "=== Phase 2: Starting new workers on band $NEW_BAND ==="

# Resolve log directory
if [[ -L "$LATEST_LINK" && -d "$LATEST_LINK" ]]; then
  LOG_DIR="$BASE_LOG_DIR/$(readlink "$LATEST_LINK")"
else
  # Create a new timestamped log dir for this deploy
  TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
  LOG_DIR="$BASE_LOG_DIR/$TIMESTAMP"
  mkdir -p "$LOG_DIR"
  rm -f "$LATEST_LINK"
  ln -s "$TIMESTAMP" "$LATEST_LINK"
fi

mkdir -p "$RUN_DIR"

for ((w = 0; w < FASTAPI_WORKERS; w++)); do
  port=$((NEW_BASE + w))
  name="uvicorn_${port}"
  log_info "  Starting $name on port $port"

  (
    cd "$BASE_DIR"
    export LOG_FILE_PATH="$LOG_DIR/${name}.log"
    exec uvicorn api.app:app --host 127.0.0.1 --port "$port" \
      >>"$LOG_DIR/${name}.log" 2>&1
  ) &

  pid=$!
  echo "$pid" > "$RUN_DIR/${name}.pid"
  log_info "    PID $pid"
done

# Brief pause to let workers bind
sleep 3

# Quick sanity: make sure they haven't crashed immediately
for ((w = 0; w < FASTAPI_WORKERS; w++)); do
  port=$((NEW_BASE + w))
  pid=$(<"$RUN_DIR/uvicorn_${port}.pid")
  if ! kill -0 "$pid" 2>/dev/null; then
    log_error "Worker uvicorn_${port} (PID $pid) died immediately"
    rollback_new_workers "$NEW_BAND"
    exit 1
  fi
done

log_info "All $FASTAPI_WORKERS new workers started"

###############################################################################
### PHASE 3: HEALTH-CHECK EVERY NEW WORKER
###############################################################################

log_info "=== Phase 3: Health-checking new workers ==="

all_healthy=true
for ((w = 0; w < FASTAPI_WORKERS; w++)); do
  port=$((NEW_BASE + w))
  healthy=false

  for ((attempt = 1; attempt <= HEALTH_MAX_ATTEMPTS; attempt++)); do
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
      "http://127.0.0.1:${port}${HEALTH_CHECK_ENDPOINT}" 2>/dev/null || echo "000")

    if [[ "$http_code" == "200" ]]; then
      log_info "  uvicorn_${port} healthy (attempt $attempt)"
      healthy=true
      break
    fi
    sleep "$HEALTH_INTERVAL"
  done

  if ! $healthy; then
    log_error "  uvicorn_${port} FAILED health check after $HEALTH_MAX_ATTEMPTS attempts"
    all_healthy=false
    break
  fi
done

if ! $all_healthy; then
  rollback_new_workers "$NEW_BAND"
  exit 1
fi

log_info "All new workers are healthy"

###############################################################################
### PHASE 4: SWITCH NGINX TO NEW BAND
###############################################################################

log_info "=== Phase 4: Switching nginx to band $NEW_BAND ==="

if [[ ! -f "$NGINX_UPSTREAM_TEMPLATE" ]]; then
  log_error "Nginx upstream template not found at $NGINX_UPSTREAM_TEMPLATE"
  rollback_new_workers "$NEW_BAND"
  exit 1
fi

# Build upstream server list from new-band ports
UPSTREAM_SERVERS=""
for ((w = 0; w < FASTAPI_WORKERS; w++)); do
  port=$((NEW_BASE + w))
  UPSTREAM_SERVERS="${UPSTREAM_SERVERS}    server 127.0.0.1:${port};\n"
done

# Generate upstream config
sed -e "s|{{UVICORN_UPSTREAM_SERVERS}}|${UPSTREAM_SERVERS}|" \
    "$NGINX_UPSTREAM_TEMPLATE" | sudo tee "$NGINX_UPSTREAM_CONF" > /dev/null

log_info "Generated nginx upstream config with $FASTAPI_WORKERS workers (ports ${NEW_BASE}–$((NEW_BASE + FASTAPI_WORKERS - 1)))"

# Validate config
if ! sudo nginx -t 2>/dev/null; then
  log_error "nginx config validation failed!"
  sudo nginx -t 2>&1 || true
  # Restore old upstream config
  OLD_UPSTREAM=""
  for ((w = 0; w < FASTAPI_WORKERS; w++)); do
    port=$((OLD_BASE + w))
    OLD_UPSTREAM="${OLD_UPSTREAM}    server 127.0.0.1:${port};\n"
  done
  sed -e "s|{{UVICORN_UPSTREAM_SERVERS}}|${OLD_UPSTREAM}|" \
      "$NGINX_UPSTREAM_TEMPLATE" | sudo tee "$NGINX_UPSTREAM_CONF" > /dev/null

  rollback_new_workers "$NEW_BAND"
  exit 1
fi

# Reload nginx (graceful — finishes in-flight requests to old upstream)
sudo systemctl reload nginx
log_info "nginx reloaded — traffic now routed to band $NEW_BAND"

###############################################################################
### PHASE 5: DRAIN OLD WORKERS
###############################################################################

# nginx (Phase 4) already routes new calls to the new band, so the old band only
# holds calls still in progress. Wait for those to finish BEFORE signalling the
# workers: SIGTERM makes uvicorn force-close live call WebSockets (close code
# 1012), cutting calls mid-conversation. So we poll each old worker's in-flight
# call count and only stop once it reaches zero (or DRAIN_TIMEOUT elapses).

log_info "=== Phase 5a: Draining active calls from band $OLD_BAND (timeout ${DRAIN_TIMEOUT}s) ==="

drain_start=$(date +%s)
while true; do
  active=0
  for ((w = 0; w < FASTAPI_WORKERS; w++)); do
    port=$((OLD_BASE + w))
    # Only poll workers still alive; an exited worker holds no calls.
    pidfile="$RUN_DIR/uvicorn_${port}.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then
      if ! call_count=$(count_active_calls_on_port "$port"); then
        exit 1
      fi
      active=$((active + call_count))
    fi
  done

  if [[ $active -eq 0 ]]; then
    log_info "Band $OLD_BAND fully drained — no active calls"
    break
  fi

  elapsed=$(( $(date +%s) - drain_start ))
  if [[ $elapsed -ge $DRAIN_TIMEOUT ]]; then
    log_warn "Drain timeout reached (${DRAIN_TIMEOUT}s) with $active active call(s) still running — stopping anyway."
    break
  fi

  log_info "  Waiting for $active active call(s) to finish... (${elapsed}s / ${DRAIN_TIMEOUT}s)"
  sleep "$DRAIN_INTERVAL"
done

log_info "=== Phase 5b: Stopping old workers (band $OLD_BAND, timeout ${STOP_TIMEOUT}s) ==="

# Calls are drained — now signal the workers and reap them. A drained worker
# exits within a second or two of SIGTERM; STOP_TIMEOUT bounds stragglers (e.g.
# a call that outlived DRAIN_TIMEOUT) before we force-kill.
OLD_PIDS=()
for ((w = 0; w < FASTAPI_WORKERS; w++)); do
  port=$((OLD_BASE + w))
  pidfile="$RUN_DIR/uvicorn_${port}.pid"
  if [[ -f "$pidfile" ]]; then
    pid=$(<"$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      OLD_PIDS+=("$pid")
      log_info "  Sending SIGTERM to uvicorn_${port} (PID $pid)"
      kill_process_tree "$pid" "-TERM"
    fi
    rm -f "$pidfile"
  fi
done

if [[ ${#OLD_PIDS[@]} -gt 0 ]]; then
  stop_start=$(date +%s)

  while true; do
    all_dead=true
    for pid in "${OLD_PIDS[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        all_dead=false
        break
      fi
    done

    if $all_dead; then
      log_info "All old workers exited"
      break
    fi

    elapsed=$(( $(date +%s) - stop_start ))
    if [[ $elapsed -ge $STOP_TIMEOUT ]]; then
      log_warn "Stop timeout reached (${STOP_TIMEOUT}s). Force-killing remaining old workers."
      for pid in "${OLD_PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
          kill_process_tree "$pid" "-KILL"
          log_warn "  Force-killed PID $pid"
        fi
      done
      sleep 1
      break
    fi

    log_info "  Waiting for old workers to exit... (${elapsed}s / ${STOP_TIMEOUT}s)"
    sleep 2
  done
else
  log_warn "No old worker PIDs to stop"
fi

###############################################################################
### PHASE 6: RESTART NON-HTTP SERVICES
###############################################################################

log_info "=== Phase 6: Restarting non-HTTP services ==="

# Services to restart (same as start_services.sh)
RESTART_NAMES=(
  "ari_manager"
  "campaign_orchestrator"
)
RESTART_COMMANDS=(
  "python -m api.services.telephony.ari_manager"
  "python -m api.services.campaign.campaign_orchestrator"
)

# Add ARQ workers
for ((i = 1; i <= ARQ_WORKERS; i++)); do
  RESTART_NAMES+=("arq$i")
  RESTART_COMMANDS+=("python -m arq api.tasks.arq.WorkerSettings --custom-log-dict api.tasks.arq.LOG_CONFIG")
done

for i in "${!RESTART_NAMES[@]}"; do
  name="${RESTART_NAMES[$i]}"
  cmd="${RESTART_COMMANDS[$i]}"
  pidfile="$RUN_DIR/${name}.pid"

  # Stop old instance
  if [[ -f "$pidfile" ]]; then
    oldpid=$(<"$pidfile")
    if kill -0 "$oldpid" 2>/dev/null; then
      log_info "  Stopping $name (PID $oldpid)"
      kill_process_tree "$oldpid" "-TERM"
      sleep 2
      if kill -0 "$oldpid" 2>/dev/null; then
        kill_process_tree "$oldpid" "-KILL"
        sleep 1
      fi
    fi
    rm -f "$pidfile"
  fi

  # Start new instance
  log_info "  Starting $name"
  (
    cd "$BASE_DIR"
    export LOG_FILE_PATH="$LOG_DIR/${name}.log"
    exec $cmd >>"$LOG_DIR/${name}.log" 2>&1
  ) &

  pid=$!
  echo "$pid" > "$RUN_DIR/${name}.pid"
  log_info "    PID $pid"
done

###############################################################################
### PHASE 7: FINALIZE
###############################################################################

log_info "=== Phase 7: Finalize ==="

echo "$NEW_BAND" > "$RUN_DIR/active_band"
log_info "active_band set to $NEW_BAND"

echo
echo "══════════════════════════════════════════════════"
echo "  Rolling update completed successfully"
echo ""
echo "  Band:      $OLD_BAND → $NEW_BAND"
echo "  Workers:   $FASTAPI_WORKERS (ports ${NEW_BASE}–$((NEW_BASE + FASTAPI_WORKERS - 1)))"
echo "  Services:  ${RESTART_NAMES[*]}"
echo "  Logs:      $LOG_DIR"
echo "══════════════════════════════════════════════════"
