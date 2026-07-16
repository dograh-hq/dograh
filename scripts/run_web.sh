#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
ENV_FILE="$BASE_DIR/api/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a && . "$ENV_FILE" && set +a
fi

PORT="${WEB_PORT:-8000}"

# uvicorn trusts X-Forwarded-Proto / X-Forwarded-For only from peers listed
# in the FORWARDED_ALLOW_IPS env var (default 127.0.0.1). Behind a reverse
# proxy it must be set (compose: api service env, helm: web.forwardedAllowIps)
# or request.url stays http:// and URL-signed webhook validation fails.
cd "$BASE_DIR"
exec uvicorn api.app:app --host 0.0.0.0 --port "$PORT" --workers 1
