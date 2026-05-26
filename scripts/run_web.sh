#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
ENV_FILE="$BASE_DIR/api/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a && . "$ENV_FILE" && set +a
fi

PORT="${WEB_PORT:-8000}"

cd "$BASE_DIR"
exec uvicorn api.app:app --host 0.0.0.0 --port "$PORT" --workers 1
