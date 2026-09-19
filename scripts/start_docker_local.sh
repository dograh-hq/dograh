#!/usr/bin/env bash
# Build the api and ui images from local source (this checkout's ./api,
# ./pipecat and ./ui) and start the OSS Docker stack from those images
# instead of pulling the published ones. docker-compose.yaml only has custom
# images for api and ui -- everything else (postgres/redis/minio/nginx/
# coturn/cloudflared) is a stock public image, so those still pull normally
# regardless of REGISTRY. Use this to try out local changes in the same
# docker-compose.yaml stack start_docker.sh runs -- for the published-image
# quickstart, use start_docker.sh instead.
set -e

REGISTRY="${REGISTRY:-daken.in}"
ENABLE_TELEMETRY="${ENABLE_TELEMETRY:-true}"
API_IMAGE="$REGISTRY/dograh-api:latest"
UI_IMAGE="$REGISTRY/dograh-ui:latest"

fail() {
    echo "Error: $*" >&2
    exit 1
}

[[ -f docker-compose.yaml ]] || fail "docker-compose.yaml not found. Run this from the repo root."
[[ -f api/Dockerfile ]] || fail "api/Dockerfile not found. Run this from the repo root."
[[ -f ui/Dockerfile ]] || fail "ui/Dockerfile not found. Run this from the repo root."
[[ -f .env ]] || fail ".env not found. Run scripts/start_docker.sh once first to generate it, then re-run this script."

echo "Building $API_IMAGE from local ./api and ./pipecat ..."
docker build -f api/Dockerfile -t "$API_IMAGE" .

echo "Building $UI_IMAGE from local ./ui ..."
docker build -f ui/Dockerfile -t "$UI_IMAGE" .

echo ""
echo "This will run:"
echo "  REGISTRY=$REGISTRY ENABLE_TELEMETRY=$ENABLE_TELEMETRY docker compose --profile tunnel up -d"
echo ""
echo "(Same full stack as start_docker.sh -- postgres/redis/minio/nginx/coturn/cloudflared"
echo " start normally. api and ui use the images just built above instead of being pulled;"
echo " compose recreates them automatically since the images changed.)"
echo ""

if [[ -t 0 ]]; then
    read -r -p "Start Dograh now? [Y/n]: " answer
    case "$answer" in
        [Nn]*)
            echo "Image built, not started. Run the command above when you're ready."
            exit 0
            ;;
    esac
fi

REGISTRY="$REGISTRY" ENABLE_TELEMETRY="$ENABLE_TELEMETRY" \
    docker compose --profile tunnel up -d

echo ""
echo "Started. Tail logs with: docker compose logs -f api"
echo "                     or: docker compose logs -f ui"
