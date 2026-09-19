#!/usr/bin/env bash
# Production-oriented remote deployment for this Dograh fork.
#
# Builds api and ui from THIS checkout (./api, ./pipecat, ./ui) and never
# pulls dograhai/dograh-api or dograhai/dograh-ui -- same local-build
# mechanism as start_docker_local.sh (a REGISTRY value docker never resolves
# against a real registry, so compose finds the image already built locally
# and never attempts a pull).
#
# Starts only: redis, dograh-init, coturn, api, ui, nginx.
# Never starts: postgres, minio, elasticsearch, cloudflared.
#   - Postgres is external (see REMOTE_PG_* below); api's DATABASE_URL and
#     depends_on are patched by docker-compose.blackbolt-remote.yaml.
#   - Storage is AWS S3 (ENABLE_AWS_S3=true), preferably via this EC2
#     instance's IAM role (leave AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY unset
#     in .env so boto3's default credential chain picks up the role).
#
# Run from the repo root, on the target host. Reuses scripts/lib/setup_common.sh
# and the dograh-init/nginx/coturn rendering machinery scripts/setup_remote.sh
# and remote_up.sh use for the mainline OSS remote deployment; see
# docker-compose.blackbolt-remote.yaml's header for exactly what's overridden
# and why.
#
# Flags:
#   --build-only       Build api/ui images, then stop (no .env mutation beyond
#                       what preflight needs, nothing started).
#   --preflight-only    Validate .env and compose config, then stop (no build,
#                       nothing started).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/setup_common.sh"
DOGRAH_DEPLOY_PROJECT_DIR="$REPO_ROOT"

# --- Fixed facts for this deployment (edit here to point at a different
# target; these are architecture decisions, not per-operator secrets) --------
SERVER_IP_DEFAULT="13.202.93.55"
REMOTE_PG_HOST="172.31.5.196"
REMOTE_PG_PORT="5432"
REMOTE_PG_DB="dograh_dev"
REMOTE_PG_USER="dograh"
S3_BUCKET_DEFAULT="blackbolt-dograh-dev-2026"
S3_REGION_DEFAULT="ap-south-1"

OVERRIDE_COMPOSE="docker-compose.blackbolt-remote.yaml"
COMPOSE=(docker compose -f docker-compose.yaml -f "$OVERRIDE_COMPOSE")
PROFILE_ARGS=(--profile remote)

export REGISTRY="${REGISTRY:-blackbolt-fork}"
API_IMAGE="$REGISTRY/dograh-api:latest"
UI_IMAGE="$REGISTRY/dograh-ui:latest"

BUILD_ONLY=0
PREFLIGHT_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --build-only) BUILD_ONLY=1 ;;
        --preflight-only) PREFLIGHT_ONLY=1 ;;
        *) dograh_fail "Unknown argument: $arg (supported: --build-only, --preflight-only)" ;;
    esac
done

# --- Layout checks -----------------------------------------------------------
[[ -f docker-compose.yaml ]] || dograh_fail "docker-compose.yaml not found. Run this from the repo root."
[[ -f "$OVERRIDE_COMPOSE" ]] || dograh_fail "$OVERRIDE_COMPOSE not found. Run this from the repo root."
[[ -f api/Dockerfile ]] || dograh_fail "api/Dockerfile not found. Run this from the repo root."
[[ -f ui/Dockerfile ]] || dograh_fail "ui/Dockerfile not found. Run this from the repo root."
[[ -f .env ]] || dograh_fail ".env not found. Create one with at least DOGRAH_DB_PASSWORD and OSS_JWT_SECRET set, then re-run."

dograh_require_init_compose_layout "$REPO_ROOT"

# --- Validate / fill in .env -------------------------------------------------
dograh_info "==> Checking .env"
dograh_load_env_file .env

[[ -n "${DOGRAH_DB_PASSWORD:-}" ]] || dograh_fail "DOGRAH_DB_PASSWORD is not set in .env (password for the '$REMOTE_PG_USER' role on the external Postgres at $REMOTE_PG_HOST). Set it and re-run."
[[ -n "${OSS_JWT_SECRET:-}" ]] || dograh_fail "OSS_JWT_SECRET is not set in .env."

# set_env_default KEY VALUE -- writes KEY only if currently unset/empty, so
# re-running never clobbers a value already present in .env (nothing here
# regenerates or overwrites an existing key).
set_env_default() {
    local key=$1 value=$2
    if [[ -z "${!key:-}" ]]; then
        dograh_set_env_key .env "$key" "$value"
        dograh_info "    set $key=$value"
    fi
}

set_env_default ENVIRONMENT "production"
set_env_default SERVER_IP "$SERVER_IP_DEFAULT"
set_env_default PUBLIC_HOST "$SERVER_IP_DEFAULT"
# deploy/blackbolt-remote/templates/nginx.remote.conf.template always redirects
# :80 -> https and terminates TLS at :443, so PUBLIC_BASE_URL must be https
# for the deployed stack to actually work as configured.
set_env_default PUBLIC_BASE_URL "https://$SERVER_IP_DEFAULT"
set_env_default ENABLE_COTURN "true"
set_env_default TURN_HOST "$SERVER_IP_DEFAULT"
set_env_default FORCE_TURN_RELAY "false"
set_env_default FASTAPI_WORKERS "1"
set_env_default ENABLE_SIGNUP "true"
set_env_default ENABLE_ARI_MANAGER "true"
set_env_default ENABLE_CAMPAIGN_ORCHESTRATOR "true"
set_env_default ENABLE_TELEMETRY "true"
set_env_default ENABLE_AWS_S3 "true"
set_env_default S3_BUCKET "$S3_BUCKET_DEFAULT"
set_env_default S3_REGION "$S3_REGION_DEFAULT"

if [[ -z "${TURN_SECRET:-}" ]]; then
    dograh_set_env_key .env TURN_SECRET "$(openssl rand -hex 32)"
    dograh_info "    generated TURN_SECRET"
fi
if [[ -z "${REDIS_PASSWORD:-}" ]]; then
    dograh_set_env_key .env REDIS_PASSWORD "$(openssl rand -hex 32)"
    dograh_info "    generated REDIS_PASSWORD"
fi

# Reload so the rest of this script (and the compose config check below) sees
# whatever was just written above.
dograh_load_env_file .env

if [[ -n "${AWS_ACCESS_KEY_ID:-}" || -n "${AWS_SECRET_ACCESS_KEY:-}" ]]; then
    dograh_warn "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY are set in .env -- boto3 will use them instead of this EC2 instance's IAM role. Unset both in .env to use the IAM role as intended."
fi

dograh_is_ipv4 "$SERVER_IP" || dograh_fail "SERVER_IP must be an IPv4 address (got: $SERVER_IP)"
dograh_validate_remote_runtime_env
dograh_success "✓ .env has everything required"

# --- TLS certs ----------------------------------------------------------------
# dograh-init (production branch) refuses to render nginx/coturn config
# without certs/local.{crt,key} present. Generate a self-signed pair if
# missing -- an existing cert (e.g. a real one an operator installed by hand)
# is left untouched.
mkdir -p certs
if [[ ! -f certs/local.crt || ! -f certs/local.key ]]; then
    dograh_info "==> Generating a self-signed TLS cert for CN=$PUBLIC_HOST (certs/local.crt, certs/local.key)"
    openssl req -x509 -nodes -newkey rsa:2048 \
        -keyout certs/local.key \
        -out certs/local.crt \
        -days 365 \
        -subj "/CN=$PUBLIC_HOST" \
        || dograh_fail "openssl failed to generate certs/local.{crt,key} (see error above)."
    dograh_warn "Self-signed cert -- browsers will show a warning until it's replaced with a real one."
else
    dograh_info "==> certs/local.crt and certs/local.key already present, leaving them as-is"
fi

# --- Validate compose config before doing anything expensive -----------------
dograh_info "==> Validating docker compose config"
"${COMPOSE[@]}" "${PROFILE_ARGS[@]}" config -q
dograh_success "✓ docker compose config is valid"

if [[ "$PREFLIGHT_ONLY" == "1" ]]; then
    dograh_success "Preflight OK (.env + certs + compose config). Nothing built or started (--preflight-only)."
    exit 0
fi

# --- Build api/ui from this checkout ------------------------------------------
dograh_info "==> Building $API_IMAGE from local ./api and ./pipecat"
docker build -f api/Dockerfile -t "$API_IMAGE" .

dograh_info "==> Building $UI_IMAGE from local ./ui"
docker build -f ui/Dockerfile -t "$UI_IMAGE" .

dograh_success "✓ Images built locally. REGISTRY=$REGISTRY makes compose use them; dograhai/dograh-api and dograhai/dograh-ui are never pulled."

if [[ "$BUILD_ONLY" == "1" ]]; then
    dograh_success "Build complete (--build-only). Nothing started."
    exit 0
fi

echo ""
echo "This will start, in order: redis -> dograh-init -> coturn -> api -> ui -> nginx"
echo "  postgres and minio are never started (external Postgres at $REMOTE_PG_HOST; storage is S3 bucket $S3_BUCKET/$S3_REGION)"
echo ""

if [[ -t 0 ]]; then
    read -r -p "Start Dograh now? [Y/n]: " answer
    case "$answer" in
        [Nn]*)
            echo "Images built, not started. Re-run this script when you're ready."
            exit 0
            ;;
    esac
fi

# --- External Postgres connectivity -------------------------------------------
dograh_info "==> Testing external Postgres connectivity to $REMOTE_PG_HOST:$REMOTE_PG_PORT"
if docker run --rm pgvector/pgvector:pg17 \
    pg_isready -h "$REMOTE_PG_HOST" -p "$REMOTE_PG_PORT" -U "$REMOTE_PG_USER" -d "$REMOTE_PG_DB"; then
    dograh_success "✓ External Postgres is reachable and accepting connections"
else
    dograh_fail "External Postgres at $REMOTE_PG_HOST:$REMOTE_PG_PORT is not reachable. Check the EC2 security group / network path, then re-run."
fi

# --- redis ---------------------------------------------------------------------
dograh_info "==> Starting redis"
"${COMPOSE[@]}" "${PROFILE_ARGS[@]}" up -d redis

redis_cid="$("${COMPOSE[@]}" "${PROFILE_ARGS[@]}" ps -q redis)"
redis_health=""
for _ in $(seq 1 30); do
    redis_health="$(docker inspect --format='{{.State.Health.Status}}' "$redis_cid" 2>/dev/null || true)"
    [[ "$redis_health" == "healthy" ]] && break
    sleep 2
done
[[ "$redis_health" == "healthy" ]] || dograh_fail "redis did not become healthy. Check: docker compose logs redis"

redis_pong="$("${COMPOSE[@]}" "${PROFILE_ARGS[@]}" exec -T redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping 2>/dev/null || true)"
[[ "$redis_pong" == "PONG" ]] || dograh_fail "redis did not respond PONG (got: '$redis_pong')"
dograh_success "✓ redis is healthy and answered PONG"

# --- dograh-init (renders nginx + coturn config, then exits) -------------------
dograh_info "==> Running dograh-init"
"${COMPOSE[@]}" "${PROFILE_ARGS[@]}" up --exit-code-from dograh-init dograh-init
dograh_success "✓ dograh-init completed"

# --- coturn ----------------------------------------------------------------------
dograh_info "==> Starting coturn"
"${COMPOSE[@]}" "${PROFILE_ARGS[@]}" up -d coturn

# --- api ---------------------------------------------------------------------
dograh_info "==> Starting api"
"${COMPOSE[@]}" "${PROFILE_ARGS[@]}" up -d api

dograh_info "==> Waiting for API health at http://127.0.0.1:8000/api/v1/health"
api_ready=0
for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:8000/api/v1/health"; then
        api_ready=1
        break
    fi
    sleep 3
done
[[ "$api_ready" == "1" ]] || dograh_fail "API did not become healthy in time. Check: docker compose logs api"
dograh_success "✓ API is healthy"

# --- ui ---------------------------------------------------------------------
dograh_info "==> Starting ui"
"${COMPOSE[@]}" "${PROFILE_ARGS[@]}" up -d ui

# --- nginx ---------------------------------------------------------------------
dograh_info "==> Starting nginx"
"${COMPOSE[@]}" "${PROFILE_ARGS[@]}" up -d nginx

echo ""
dograh_info "==> Final service status"
"${COMPOSE[@]}" "${PROFILE_ARGS[@]}" ps redis dograh-init coturn api ui nginx

echo ""
dograh_success "Dograh is running at: https://$PUBLIC_HOST"
dograh_warn "Self-signed certificate unless certs/local.{crt,key} were replaced -- your browser will warn once, that's expected."
echo ""
echo "Useful commands:"
echo "  docker compose -f docker-compose.yaml -f $OVERRIDE_COMPOSE logs -f api"
echo "  docker compose -f docker-compose.yaml -f $OVERRIDE_COMPOSE logs -f ui"
echo "  docker compose -f docker-compose.yaml -f $OVERRIDE_COMPOSE logs -f nginx"
echo "  docker compose -f docker-compose.yaml -f $OVERRIDE_COMPOSE logs -f coturn"
echo "  docker compose -f docker-compose.yaml -f $OVERRIDE_COMPOSE ps"
echo ""
echo "Re-run this script any time: it rebuilds api/ui from the current checkout and"
echo "recreates only the containers whose image or config actually changed."
