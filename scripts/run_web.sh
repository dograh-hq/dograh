#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
ENV_FILE="$BASE_DIR/api/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a && . "$ENV_FILE" && set +a
fi

PORT="${WEB_PORT:-8000}"

# uvicorn enables proxy-header handling by default but only trusts
# X-Forwarded-Proto / X-Forwarded-For from peers listed in the
# FORWARDED_ALLOW_IPS env var (its built-in fallback when the
# --forwarded-allow-ips flag is absent; defaults to 127.0.0.1). Behind a
# reverse proxy that env var MUST be set, or request.url keeps the http
# scheme and providers that sign their webhook URL (Vobiz, Twilio, Plivo)
# fail signature validation. Each deployment declares it where its config
# lives: docker-compose in the api service environment, helm via
# web.forwardedAllowIps in values.yaml.
cd "$BASE_DIR"
exec uvicorn api.app:app --host 0.0.0.0 --port "$PORT" --workers 1
