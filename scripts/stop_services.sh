#!/usr/bin/env bash
set -e  # Exit on error

###############################################################################
### CONFIGURATION
###############################################################################

# Determine BASE_DIR as parent of the scripts directory
BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"

RUN_DIR="$BASE_DIR/run"                 # Where we keep *.pid

cd "$BASE_DIR"
echo "Stopping Dograh Services at $(date) in BASE_DIR: ${BASE_DIR}"

###############################################################################
### HELPER FUNCTIONS
###############################################################################

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

###############################################################################
### STOP SERVICES
###############################################################################

if [[ ! -d "$RUN_DIR" ]]; then
  echo "No run directory found at $RUN_DIR"
  echo "No services appear to be running."
  exit 0
fi

# Find all PID files in the run directory
pid_files=("$RUN_DIR"/*.pid)

# Check if any PID files exist
if [[ ! -e "${pid_files[0]}" ]]; then
  echo "No PID files found in $RUN_DIR"
  echo "No services appear to be running."
  exit 0
fi

stopped_count=0
failed_count=0

for pidfile in "${pid_files[@]}"; do
  # Extract service name from pidfile path
  name=$(basename "$pidfile" .pid)

  if [[ -f "$pidfile" ]]; then
    oldpid=$(<"$pidfile")

    if kill -0 "$oldpid" 2>/dev/null; then
      echo "Stopping $name (PID $oldpid and all descendants)..."

      # Kill the entire process tree (parent + all descendants)
      kill_process_tree "$oldpid" "-TERM"
      sleep 4

      # Check if parent or any descendants are still alive
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
        echo "  Warning: $name did not exit cleanly, forcing stop..."
        kill_process_tree "$oldpid" "-KILL"
        sleep 1

        # Final check
        if kill -0 "$oldpid" 2>/dev/null; then
          echo "  Error: Failed to stop $name (PID $oldpid)"
          ((failed_count++))
        else
          echo "  Stopped $name (forced)"
          ((stopped_count++))
        fi
      else
        echo "  Stopped $name"
        ((stopped_count++))
      fi
    else
      echo "Service $name (PID $oldpid) is not running"
    fi

    rm -f "$pidfile"
  fi
done

# Clean up any port tracking files for uvicorn
rm -f "$RUN_DIR/uvicorn.port" "$RUN_DIR/uvicorn_new.port" "$RUN_DIR/uvicorn_old.pid"

###############################################################################
### SUMMARY
###############################################################################

echo
echo "──────────────────────────────────────────────────"
echo "Stopped $stopped_count service(s)"
if [[ $failed_count -gt 0 ]]; then
  echo "Failed to stop $failed_count service(s)"
fi
echo "──────────────────────────────────────────────────"
