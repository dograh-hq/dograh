#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEFAULT_POSTGRES_PORT=5432
DEFAULT_REDIS_PORT=6379
MAX_PORT_ATTEMPTS=200

resolve_port_candidate() {
  local service_path="$1"
  local fallback="$2"

  if ! command -v bunx >/dev/null 2>&1; then
    echo "$fallback"
    return
  fi

  local candidate
  candidate="$(bunx path-to-port "$service_path" 2>/dev/null | tail -n 1 | tr -d '[:space:]')"
  if [[ "$candidate" =~ ^[0-9]+$ ]]; then
    echo "$candidate"
    return
  fi

  echo "$fallback"
}

port_in_use() {
  local port="$1"

  if command -v ss >/dev/null 2>&1; then
    if ss -tln "sport = :$port" | grep -q LISTEN; then
      return 0
    fi
    return 1
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    raise SystemExit(1)
else:
    s.close()
    raise SystemExit(0)
PY
    return
  fi

  if command -v python >/dev/null 2>&1; then
    python - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.bind(("127.0.0.1", port))
except OSError:
    raise SystemExit(1)
else:
    s.close()
    raise SystemExit(0)
PY
    return
  fi

  # If no probe command exists, assume the port is free to avoid blocking startup.
  return 1
}

next_available_port() {
  local service_name="$1"
  local port="$2"
  local attempt=0

  if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    echo "$port"
    return
  fi

  while ((attempt < MAX_PORT_ATTEMPTS)); do
    if ! port_in_use "$port"; then
      echo "$port"
      return
    fi

    local previous_port=$port
    port=$((port + 1))
    attempt=$((attempt + 1))
    echo "⚠️  $service_name port $previous_port is in use, trying $port" >&2
  done

  echo "Unable to find a free $service_name port after $MAX_PORT_ATTEMPTS attempts starting at $port." >&2
  exit 1
}

resolve_port() {
  local env_name="$1"
  local service_path="$2"
  local fallback_port="$3"
  local requested_port="${!env_name:-}"
  local selected_port
  local default_port

  if [[ -n "$requested_port" ]]; then
    if [[ "$requested_port" =~ ^[0-9]+$ ]]; then
      selected_port="$(next_available_port "$env_name" "$requested_port")"
    else
      selected_port="$(next_available_port "$env_name" "$fallback_port")"
    fi
  else
    default_port="$(resolve_port_candidate "$service_path" "$fallback_port")"
    selected_port="$(next_available_port "$env_name" "$default_port")"
  fi

  printf -v "$env_name" '%s' "$selected_port"
}

resolve_port POSTGRES_PORT "$BASE_DIR/postgres" "$DEFAULT_POSTGRES_PORT"
resolve_port REDIS_PORT "$BASE_DIR/redis" "$DEFAULT_REDIS_PORT"

export POSTGRES_PORT REDIS_PORT

cat <<EOF
export POSTGRES_PORT="${POSTGRES_PORT}"
export REDIS_PORT="${REDIS_PORT}"
EOF
