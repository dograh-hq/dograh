#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
ENV_FILE="$BASE_DIR/api/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a && . "$ENV_FILE" && set +a
fi

PORT="${WEB_PORT:-8000}"

# --proxy-headers makes uvicorn honor X-Forwarded-Proto / X-Forwarded-For from
# an upstream proxy (nginx, Traefik, ALB) so `request.url.scheme` reads back as
# `https` inside the app — required for provider webhook signature checks that
# hash the incoming URL.
#
# FORWARDED_ALLOW_IPS controls which peer IPs those headers are trusted from.
# Default "*" trusts all peers, which is safe when the app is only reachable
# via the proxy (the standard docker-compose and helm layouts). If uvicorn is
# also directly reachable from an untrusted network, narrow this to the proxy
# CIDR (e.g. FORWARDED_ALLOW_IPS="10.42.0.0/16") to prevent header spoofing.
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"

cd "$BASE_DIR"
exec uvicorn api.app:app --host 0.0.0.0 --port "$PORT" --workers 1 \
  --proxy-headers --forwarded-allow-ips="$FORWARDED_ALLOW_IPS"
