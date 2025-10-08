#!/usr/bin/env bash
# rolling_update_uvicorn.sh — Zero-downtime rolling update for uvicorn workers

set -e  # Exit on error

### CONFIGURATION #############################################################

# Determine BASE_DIR as parent of the scripts directory
BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"

ENV_FILE="$BASE_DIR/api/.env"
RUN_DIR="$BASE_DIR/run"
BASE_LOG_DIR="$BASE_DIR/logs"  # Base logs directory (same as start_services.sh)
LATEST_LINK="$BASE_LOG_DIR/latest"        # Symlink to latest logs (same as start_services.sh)
VENV_PATH="$BASE_DIR/venv"
HEALTH_CHECK_ENDPOINT="/api/v1/health"  # Adjust as needed
MAX_WAIT_SECONDS=310  # Max wait for graceful shutdown (5 minutes + 10 seconds grace)

# Load environment
set -a && . "$ENV_FILE" && set +a

cd "$BASE_DIR"

### FUNCTIONS ##################################################################

log_info() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] INFO: $*"
}

log_error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

log_warning() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: $*"
}

check_port_availability() {
    local port=$1
    if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        return 1  # Port is in use
    fi
    return 0  # Port is available
}

wait_for_health_check() {
    local port=$1
    local max_attempts=30
    local attempt=0
    
    log_info "Waiting for new uvicorn workers to be healthy on port $port..."
    
    while [ $attempt -lt $max_attempts ]; do
        if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${port}${HEALTH_CHECK_ENDPOINT}" | grep -q "200"; then
            log_info "Health check passed on port $port"
            return 0
        fi
        attempt=$((attempt + 1))
        log_info "Health check attempt $attempt/$max_attempts..."
        sleep 1
    done
    
    log_error "Health check failed after $max_attempts attempts"
    return 1
}

get_old_uvicorn_pids() {
    local pidfile="$RUN_DIR/uvicorn.pid"
    local pids=""
    
    if [[ -f "$pidfile" ]]; then
        # Read the main PID
        local main_pid=$(<"$pidfile")
        if kill -0 "$main_pid" 2>/dev/null; then
            # Get all PIDs in the process group
            pids=$(ps -o pid= -g $(ps -o pgid= -p "$main_pid" | tr -d ' ') 2>/dev/null || echo "$main_pid")
        fi
    fi
    
    echo "$pids"
}

graceful_shutdown_old_workers() {
    local old_pids="$1"
    
    if [[ -z "$old_pids" ]]; then
        log_warning "No old uvicorn workers found to shut down"
        return 0
    fi
    
    log_info "Starting graceful shutdown of old uvicorn workers (PIDs: $(echo $old_pids | tr '\n' ' '))"
    
    # Send SIGTERM to trigger graceful shutdown
    for pid in $old_pids; do
        if kill -0 "$pid" 2>/dev/null; then
            log_info "Sending SIGTERM to PID $pid"
            kill -TERM "$pid" 2>/dev/null || true
        fi
    done
    
    # Wait for processes to exit gracefully
    local start_time=$(date +%s)
    local all_dead=false
    
    while [[ $(($(date +%s) - start_time)) -lt $MAX_WAIT_SECONDS ]]; do
        all_dead=true
        for pid in $old_pids; do
            if kill -0 "$pid" 2>/dev/null; then
                all_dead=false
                break
            fi
        done
        
        if $all_dead; then
            log_info "All old workers shut down gracefully"
            return 0
        fi
        
        log_info "Waiting for workers to complete active requests... ($(( $(date +%s) - start_time ))s elapsed)"
        sleep 5
    done
    
    # Force kill if still running after timeout
    log_warning "Timeout reached, force killing remaining workers"
    for pid in $old_pids; do
        if kill -0 "$pid" 2>/dev/null; then
            log_warning "Force killing PID $pid"
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    
    sleep 1
    return 0
}

start_new_uvicorn_workers() {
    local new_port=$1
    
    log_info "Starting new uvicorn workers on port $new_port..."
    
    # Get configuration from environment
    set -a && . "$ENV_FILE" && set +a
    
    if [[ -z "${FASTAPI_WORKERS:-}" ]]; then
        log_error "FASTAPI_WORKERS environment variable is not set"
        return 1
    fi
    
    # Activate virtual environment
    source ${VENV_PATH}/bin/activate

    # Use the latest log directory created by start_services.sh
    local log_dir=""

    # First, check if the symlink exists and points to a valid directory
    if [[ -L "$LATEST_LINK" ]] && [[ -d "$LATEST_LINK" ]]; then
        # Follow the symlink to get the actual directory
        log_dir="$BASE_LOG_DIR/$(readlink "$LATEST_LINK")"
        log_info "Using existing log directory: $log_dir"
    else
        log_error "No log directory found. Run start_services.sh first to create logs directory."
        log_error "Expected symlink at: $LATEST_LINK"
        return 1
    fi

    # Create unique log filename using timestamp and script PID to avoid conflicts
    local script_pid=$$  # PID of this rolling_update script (for uniqueness)
    local timestamp=$(date '+%H%M%S')
    export LOG_FILE_PATH="$log_dir/uvicorn-rollover-${timestamp}-${script_pid}.log"
    
    log_info "Starting uvicorn with $FASTAPI_WORKERS workers on port $new_port"
    log_info "Logs: $LOG_FILE_PATH"

    # Start in background (same pattern as start_services.sh)
    (
        cd "$BASE_DIR"
        export LOG_FILE_PATH="$log_dir/uvicorn-rollover-${timestamp}-${script_pid}.log"
        exec uvicorn api.app:app --host 0.0.0.0 --port $new_port --workers $FASTAPI_WORKERS >>"$LOG_FILE_PATH" 2>&1
    ) &

    local new_pid=$!
    echo "$new_pid" > "$RUN_DIR/uvicorn_new.pid"
    
    # Save port information
    echo "$new_port" > "$RUN_DIR/uvicorn_new.port"
    
    log_info "New uvicorn started with PID $new_pid"
    
    # Wait a bit for startup
    sleep 5
    
    # Check if process is still running
    if ! kill -0 "$new_pid" 2>/dev/null; then
        log_error "New uvicorn process died immediately"
        return 1
    fi
    
    return 0
}

finalize_rollover() {
    log_info "Finalizing rollover..."
    
    # Move new PID file to main PID file
    if [[ -f "$RUN_DIR/uvicorn_new.pid" ]]; then
        mv "$RUN_DIR/uvicorn_new.pid" "$RUN_DIR/uvicorn.pid"
    fi
    
    # Store the new port for reference
    if [[ -f "$RUN_DIR/uvicorn_new.port" ]]; then
        mv "$RUN_DIR/uvicorn_new.port" "$RUN_DIR/uvicorn.port"
    fi
    
    # Clean up old PID file if it exists
    rm -f "$RUN_DIR/uvicorn_old.pid"
    
    log_info "Rollover completed successfully"
}

rollback() {
    local old_port=$1
    local new_pid=$2

    log_error "Rolling back due to failure..."

    # Kill new workers if they exist
    if [[ -n "$new_pid" ]] && kill -0 "$new_pid" 2>/dev/null; then
        log_info "Killing new uvicorn workers (PID: $new_pid)"
        kill -KILL -"$new_pid" 2>/dev/null || kill -KILL "$new_pid" 2>/dev/null || true
    fi

    # Clean up temporary files
    rm -f "$RUN_DIR/uvicorn_new.pid" "$RUN_DIR/uvicorn_new.port"

    log_error "Rollback completed"
}

### MAIN LOGIC ################################################################

# Check arguments
if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <NEW_PORT>"
    echo "Example: $0 8001"
    exit 1
fi

NEW_PORT=$1

# Validate port number
if ! [[ "$NEW_PORT" =~ ^[0-9]+$ ]] || [ "$NEW_PORT" -lt 1 ] || [ "$NEW_PORT" -gt 65535 ]; then
    log_error "Invalid port number: $NEW_PORT"
    exit 1
fi

# Check if port is available
if ! check_port_availability "$NEW_PORT"; then
    log_error "Port $NEW_PORT is already in use"
    exit 1
fi

# Get old port from file or environment
OLD_PORT=""
if [[ -f "$RUN_DIR/uvicorn.port" ]]; then
    OLD_PORT=$(<"$RUN_DIR/uvicorn.port")
elif [[ -f "$ENV_FILE" ]]; then
    set -a && . "$ENV_FILE" && set +a
    OLD_PORT="${FASTAPI_PORT:-}"
fi

if [[ "$NEW_PORT" == "$OLD_PORT" ]]; then
    log_error "New port is the same as old port ($NEW_PORT)"
    exit 1
fi

log_info "Starting rolling update from port ${OLD_PORT:-unknown} to port $NEW_PORT"

# Create run directory if it doesn't exist
mkdir -p "$RUN_DIR"

# Get old uvicorn PIDs before starting new ones
OLD_PIDS=$(get_old_uvicorn_pids)
if [[ -n "$OLD_PIDS" ]]; then
    # Save old PIDs for potential rollback
    echo "$OLD_PIDS" > "$RUN_DIR/uvicorn_old.pid"
    log_info "Found old uvicorn workers: $(echo $OLD_PIDS | tr '\n' ' ')"
else
    log_warning "No existing uvicorn workers found"
fi

# Start new uvicorn workers
if ! start_new_uvicorn_workers "$NEW_PORT"; then
    log_error "Failed to start new uvicorn workers"
    exit 1
fi

NEW_PID=$(<"$RUN_DIR/uvicorn_new.pid")

# Wait for new workers to be healthy
if ! wait_for_health_check "$NEW_PORT"; then
    log_error "New workers failed health check"
    rollback "$OLD_PORT" "$NEW_PID"
    exit 1
fi

# Give the system some time to stabilize before shutting down old workers
log_info "Waiting for system to stabilize..."
sleep 5

# Gracefully shutdown old workers
if [[ -n "$OLD_PIDS" ]]; then
    graceful_shutdown_old_workers "$OLD_PIDS"
fi

# Finalize the rollover
finalize_rollover

# Summary
echo "──────────────────────────────────────────────────"
echo "✓ Rolling update completed successfully"
echo "  Old port: ${OLD_PORT:-none}"
echo "  New port: $NEW_PORT"
echo "  New PID: $NEW_PID"
echo "  Logs: $BASE_LOG_DIR/$LATEST_LINK/"
echo "──────────────────────────────────────────────────"