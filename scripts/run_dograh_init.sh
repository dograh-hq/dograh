#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="${DOGRAH_INIT_WORKSPACE_DIR:-/workspace}"
OUTPUT_ROOT="${DOGRAH_INIT_OUTPUT_ROOT:-/generated}"
NGINX_OUTPUT_DIR="$OUTPUT_ROOT/nginx"
COTURN_OUTPUT_DIR="$OUTPUT_ROOT/coturn"
CERTS_DIR="${DOGRAH_INIT_CERTS_DIR:-/certs}"

# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/setup_common.sh"

DOGRAH_DEPLOY_PROJECT_DIR="$WORKSPACE_DIR"

mkdir -p "$NGINX_OUTPUT_DIR" "$COTURN_OUTPUT_DIR"

if [[ "${ENVIRONMENT:-local}" == "production" ]]; then
    sativoice_validate_remote_runtime_env
    [[ -f "$CERTS_DIR/local.crt" ]] || sativoice_fail "certs/local.crt not found"
    [[ -f "$CERTS_DIR/local.key" ]] || sativoice_fail "certs/local.key not found"

    export TURN_EXTERNAL_IP="$SERVER_IP"
    sativoice_render_remote_nginx_conf "$WORKSPACE_DIR" "$NGINX_OUTPUT_DIR/default.conf"
    sativoice_render_remote_turn_conf "$WORKSPACE_DIR" "$COTURN_OUTPUT_DIR/turnserver.conf"
    sativoice_success "✓ sativoice-init rendered remote nginx and coturn config"
    exit 0
fi

if [[ -n "${TURN_SECRET:-}" && -n "${TURN_HOST:-}" ]]; then
    export TURN_EXTERNAL_IP="$TURN_HOST"
    sativoice_render_remote_turn_conf "$WORKSPACE_DIR" "$COTURN_OUTPUT_DIR/turnserver.conf"
    sativoice_success "✓ sativoice-init rendered local TURN config"
    exit 0
fi

sativoice_success "✓ sativoice-init no-op for current profile"
