#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_PATH="$SCRIPT_DIR/scripts/lib/setup_common.sh"
BOOTSTRAP_LIB=""

if [[ ! -f "$LIB_PATH" ]]; then
    BOOTSTRAP_LIB="$(mktemp)"
    curl -fsSL -o "$BOOTSTRAP_LIB" "https://raw.githubusercontent.com/dograh-hq/dograh/main/scripts/lib/setup_common.sh"
    LIB_PATH="$BOOTSTRAP_LIB"
fi

cleanup() {
    if [[ -n "$BOOTSTRAP_LIB" ]]; then
        rm -f "$BOOTSTRAP_LIB"
    fi
}
trap cleanup EXIT

# shellcheck disable=SC1090
. "$LIB_PATH"

DOGRAH_DEPLOY_PROJECT_DIR="$SCRIPT_DIR"

VALIDATE_ONLY=0
MODE="pull"
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)
            MODE="build"
            ;;
        --preflight-only|--validate-only)
            VALIDATE_ONLY=1
            ;;
        --)
            shift
            EXTRA_ARGS=("$@")
            break
            ;;
        *)
            EXTRA_ARGS+=("$1")
            ;;
    esac
    shift
done

cd "$SCRIPT_DIR"

dograh_info "Running Dograh remote preflight..."
dograh_prepare_remote_install "$SCRIPT_DIR"
docker compose config -q
dograh_success "✓ dograh-init preflight validated"

if [[ "$VALIDATE_ONLY" == "1" ]]; then
    exit 0
fi

if [[ $EUID -eq 0 ]] || ! command -v sudo >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
else
    COMPOSE_CMD=(sudo docker compose)
fi

# Reconcile the Postgres role password with .env before starting the API.
# POSTGRES_PASSWORD only applies on first volume init, so an existing volume can
# hold a stale password the API would fail to authenticate against. Idempotent.
dograh_sync_postgres_password "$SCRIPT_DIR" "${COMPOSE_CMD[@]}"

# When SERVER_IP (sourced from .env above) is a private/reserved address the host
# has no public IP, so start the cloudflared service (tunnel profile) to make
# webhooks reachable. The backend resolves the tunnel's public URL at runtime using
# the same private-IP classification (api/utils/common.py:is_local_or_private_url),
# so the two stay in sync. A public-IP install runs nginx only.
PROFILE_ARGS=(--profile remote)
if dograh_is_local_ipv4 "${SERVER_IP:-}"; then
    PROFILE_ARGS+=(--profile tunnel)
fi

if [[ "$MODE" == "build" ]]; then
    CMD=("${COMPOSE_CMD[@]}" "${PROFILE_ARGS[@]}" up -d --build --force-recreate)
else
    CMD=("${COMPOSE_CMD[@]}" "${PROFILE_ARGS[@]}" up -d --pull always --force-recreate)
fi

# Bash 3.2 on macOS treats "${empty_array[@]}" as unbound under `set -u`.
if (( ${#EXTRA_ARGS[@]} )); then
    CMD+=("${EXTRA_ARGS[@]}")
fi

exec "${CMD[@]}"
