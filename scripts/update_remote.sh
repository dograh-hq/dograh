#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

REPO="dograh-hq/dograh"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                  Dograh Remote Update                        ║"
echo "║   Refresh host-side configs and pin api/ui image versions    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Refuse outside an install — nothing to update if these aren't here.
if [[ ! -f docker-compose.yaml ]]; then
    echo -e "${RED}Error: docker-compose.yaml not found in $(pwd)${NC}"
    echo -e "${RED}Run this script from your Dograh install directory${NC}"
    echo -e "${RED}(the 'dograh/' folder created by setup_remote.sh).${NC}"
    exit 1
fi

if [[ ! -f .env ]]; then
    echo -e "${RED}Error: .env not found in $(pwd)${NC}"
    echo -e "${RED}This script updates an existing install — there is nothing here to update.${NC}"
    echo -e "${RED}For a fresh install, see https://docs.dograh.com/deployment/docker${NC}"
    exit 1
fi

# Build-mode installs update via git, not via this script. The presence of an
# override file is the definitive marker (created by setup_remote.sh in build
# mode and not in prebuilt mode).
if [[ -f docker-compose.override.yaml ]]; then
    echo -e "${YELLOW}Build-mode install detected (docker-compose.override.yaml present).${NC}"
    echo ""
    echo -e "${YELLOW}This script is for prebuilt installs only. For build mode, update via git:${NC}"
    echo ""
    echo -e "  ${BLUE}git fetch${NC}"
    echo -e "  ${BLUE}git checkout <tag>      # or: git pull${NC}"
    echo -e "  ${BLUE}git submodule update --init --recursive${NC}"
    echo -e "  ${BLUE}sudo docker compose --profile remote build${NC}"
    echo -e "  ${BLUE}sudo docker compose --profile remote up -d${NC}"
    echo ""
    echo -e "${YELLOW}See https://docs.dograh.com/deployment/update#updating-a-source-build${NC}"
    exit 1
fi

###############################################################################
### Discover existing config from .env
###############################################################################

# Save anything the caller exported before we overwrite from .env.
_caller_FASTAPI_WORKERS="$FASTAPI_WORKERS"
_caller_TARGET_VERSION="$TARGET_VERSION"

set -a
# shellcheck disable=SC1091
. ./.env
set +a

# SERVER_IP isn't a literal key in .env — derive it from BACKEND_API_ENDPOINT.
if [[ -z "$SERVER_IP" ]]; then
    if [[ -n "$BACKEND_API_ENDPOINT" ]]; then
        SERVER_IP="${BACKEND_API_ENDPOINT#https://}"
        SERVER_IP="${SERVER_IP#http://}"
    fi
fi

if [[ -z "$SERVER_IP" ]]; then
    echo -e "${RED}Error: could not determine SERVER_IP from .env${NC}"
    echo -e "${RED}Expected BACKEND_API_ENDPOINT=https://<ip> in .env${NC}"
    exit 1
fi

if [[ -z "$TURN_SECRET" ]]; then
    echo -e "${RED}Error: TURN_SECRET not found in .env${NC}"
    exit 1
fi

# Reapply caller overrides on top of sourced .env so e.g. FASTAPI_WORKERS=8 ./update_remote.sh works.
[[ -n "$_caller_FASTAPI_WORKERS" ]] && FASTAPI_WORKERS="$_caller_FASTAPI_WORKERS"
[[ -n "$_caller_TARGET_VERSION" ]] && TARGET_VERSION="$_caller_TARGET_VERSION"

###############################################################################
### Determine target version
###############################################################################

if [[ -z "$TARGET_VERSION" ]]; then
    echo -e "${BLUE}Fetching latest release tag from GitHub...${NC}"
    LATEST_TAG=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | grep -E '"tag_name":' | head -1 \
        | sed -E 's/.*"tag_name":[[:space:]]*"([^"]+)".*/\1/' || true)

    if [[ -z "$LATEST_TAG" ]]; then
        echo -e "${YELLOW}Could not auto-discover latest tag — defaulting to 'main'.${NC}"
        LATEST_TAG="main"
    fi

    if [[ -t 0 ]]; then
        echo ""
        echo -e "${YELLOW}Target version. Accepted forms: bare semver (1.28.0), v-prefixed (v1.28.0),${NC}"
        echo -e "${YELLOW}full git tag (dograh-v1.28.0), or 'main' for bleeding edge.${NC}"
        read -p "[$LATEST_TAG]: " TARGET_VERSION
        TARGET_VERSION="${TARGET_VERSION:-$LATEST_TAG}"
    else
        TARGET_VERSION="$LATEST_TAG"
    fi
fi

# "latest" isn't a real ref on GitHub — treat it as "latest release".
if [[ "$TARGET_VERSION" == "latest" ]]; then
    TARGET_VERSION=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | grep -E '"tag_name":' | head -1 \
        | sed -E 's/.*"tag_name":[[:space:]]*"([^"]+)".*/\1/' || true)
    if [[ -z "$TARGET_VERSION" ]]; then
        echo -e "${RED}Error: could not resolve 'latest' to a release tag${NC}"
        exit 1
    fi
fi


# GitHub release tags use a 'dograh-v' prefix (e.g. dograh-v1.28.0); Docker
# image tags on Docker Hub drop both the prefix and the 'v' (e.g. ':1.28.0').
# Users commonly type shortcuts like '1.28.0' or 'v1.28.0' — try all reasonable
# variants so the script accepts any of those forms.
TRY_TAGS=("$TARGET_VERSION")
case "$TARGET_VERSION" in
    main|HEAD)
        ;;  # branch refs — leave as-is
    dograh-*)
        ;;  # already in the full tag form
    v*)
        TRY_TAGS+=("dograh-$TARGET_VERSION")
        ;;
    *)
        TRY_TAGS+=("dograh-v$TARGET_VERSION" "v$TARGET_VERSION" "dograh-$TARGET_VERSION")
        ;;
esac

echo -e "${BLUE}Validating target version: $TARGET_VERSION...${NC}"
RESOLVED_TAG=""
for tag in "${TRY_TAGS[@]}"; do
    if curl -fsI "https://raw.githubusercontent.com/$REPO/$tag/docker-compose.yaml" >/dev/null 2>&1; then
        RESOLVED_TAG="$tag"
        break
    fi
done

if [[ -z "$RESOLVED_TAG" ]]; then
    echo -e "${RED}Error: could not find a git tag matching '$TARGET_VERSION'${NC}"
    echo -e "${RED}Tried: ${TRY_TAGS[*]}${NC}"
    echo -e "${RED}See available releases at: https://github.com/$REPO/releases${NC}"
    exit 1
fi

if [[ "$RESOLVED_TAG" != "$TARGET_VERSION" ]]; then
    echo -e "${GREEN}✓ Resolved '$TARGET_VERSION' to git tag '$RESOLVED_TAG'${NC}"
fi
TARGET_VERSION="$RESOLVED_TAG"
RAW_BASE="https://raw.githubusercontent.com/$REPO/$TARGET_VERSION"

# Derive the Docker image tag from the git tag. Tags on Docker Hub use bare
# semver — strip the 'dograh-' prefix and the leading 'v'.
IMAGE_TAG=""
case "$TARGET_VERSION" in
    dograh-v*) IMAGE_TAG="${TARGET_VERSION#dograh-v}" ;;
    v*)        IMAGE_TAG="${TARGET_VERSION#v}" ;;
    main|HEAD) IMAGE_TAG="" ;;
    *)         [[ "$TARGET_VERSION" =~ ^[0-9] ]] && IMAGE_TAG="$TARGET_VERSION" ;;
esac

# Verify the image tag actually exists on Docker Hub. If not (e.g. CI hasn't
# published yet), fall back to ':latest' rather than pinning to a missing tag.
if [[ -n "$IMAGE_TAG" ]]; then
    if curl -fsI "https://hub.docker.com/v2/repositories/dograhai/dograh-api/tags/$IMAGE_TAG/" >/dev/null 2>&1; then
        echo -e "${GREEN}✓ Image tag :$IMAGE_TAG found on Docker Hub${NC}"
    else
        echo -e "${YELLOW}Warning: image tag :$IMAGE_TAG not found on Docker Hub — leaving images at :latest${NC}"
        IMAGE_TAG=""
    fi
fi

###############################################################################
### Reconcile required keys that may be missing on older installs
###############################################################################

if [[ -z "$FASTAPI_WORKERS" ]]; then
    if [[ -t 0 ]]; then
        echo ""
        echo -e "${YELLOW}FASTAPI_WORKERS not set in .env. Number of uvicorn workers nginx will load-balance:${NC}"
        read -p "[4]: " FASTAPI_WORKERS
        FASTAPI_WORKERS="${FASTAPI_WORKERS:-4}"
    else
        FASTAPI_WORKERS="4"
    fi
fi

if ! [[ "$FASTAPI_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
    echo -e "${RED}Error: FASTAPI_WORKERS must be a positive integer (got: $FASTAPI_WORKERS)${NC}"
    exit 1
fi

###############################################################################
### Summary + confirmation
###############################################################################

echo ""
echo -e "${GREEN}Update plan:${NC}"
echo -e "  Server IP:        ${BLUE}$SERVER_IP${NC}"
echo -e "  Target version:   ${BLUE}$TARGET_VERSION${NC}"
echo -e "  FastAPI workers:  ${BLUE}$FASTAPI_WORKERS${NC}  (ports 8000..$((8000 + FASTAPI_WORKERS - 1)))"
echo ""
echo -e "${YELLOW}Files that will be replaced (backups saved with suffix .bak.$TIMESTAMP):${NC}"
echo "  - docker-compose.yaml   (pulled from GitHub at $TARGET_VERSION)"
echo "  - nginx.conf            (regenerated from this script's template)"
echo "  - turnserver.conf       (regenerated from this script's template)"
echo "  - .env                  (existing values preserved; missing keys appended)"
echo ""
echo -e "${YELLOW}Any local customizations to these files will be overwritten — check the backup${NC}"
echo -e "${YELLOW}files if you need to re-apply edits afterwards.${NC}"
echo ""

if [[ -t 0 && "$DOGRAH_UPDATE_YES" != "1" ]]; then
    read -p "Proceed? [y/N]: " confirm
    if ! [[ "$confirm" =~ ^[Yy] ]]; then
        echo -e "${RED}Aborted.${NC}"
        exit 1
    fi
fi

###############################################################################
### Step 1 — backups
###############################################################################

echo ""
echo -e "${BLUE}[1/5] Backing up existing files...${NC}"
for f in docker-compose.yaml nginx.conf turnserver.conf .env; do
    if [[ -f "$f" ]]; then
        cp -p "$f" "$f.bak.$TIMESTAMP"
        echo -e "  ${GREEN}✓ $f → $f.bak.$TIMESTAMP${NC}"
    fi
done

###############################################################################
### Step 2 — docker-compose.yaml (download + pin image tags)
###############################################################################

echo -e "${BLUE}[2/5] Downloading docker-compose.yaml at $TARGET_VERSION...${NC}"
curl -fsSL -o docker-compose.yaml "$RAW_BASE/docker-compose.yaml"

# Pin api/ui image tags when we resolved one. For branch refs (main) IMAGE_TAG
# is empty, so the images stay at ':latest' and `up --pull always` grabs the
# newest build of that branch.
if [[ -n "$IMAGE_TAG" ]]; then
    sed -i.tmp -E "s#(dograh-(api|ui)):latest#\1:$IMAGE_TAG#g" docker-compose.yaml
    rm -f docker-compose.yaml.tmp
    echo -e "${GREEN}✓ docker-compose.yaml updated; images pinned to :$IMAGE_TAG${NC}"
else
    echo -e "${GREEN}✓ docker-compose.yaml updated (image tags left at :latest)${NC}"
fi

###############################################################################
### Step 3 — nginx.conf (regenerate from embedded template)
###############################################################################

echo -e "${BLUE}[3/5] Regenerating nginx.conf...${NC}"
{
    echo "# Backend API workers — one uvicorn process per port, balanced by least_conn."
    echo "# Generated by update_remote.sh; regenerate to change worker count."
    echo "upstream dograh_api {"
    echo "    least_conn;"
    for ((i=0; i<FASTAPI_WORKERS; i++)); do
        port=$((8000 + i))
        echo "    server api:$port max_fails=3 fail_timeout=10s;"
    done
    echo "    keepalive 32;"
    echo "}"
    echo ""
    cat << 'NGINX_EOF'
server {
    listen 80;
    server_name SERVER_IP_PLACEHOLDER;

    # Redirect all HTTP to HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name SERVER_IP_PLACEHOLDER;

    ssl_certificate     /etc/nginx/certs/local.crt;
    ssl_certificate_key /etc/nginx/certs/local.key;

    # Basic TLS settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;

    # Backend API and WebSockets - bypass the UI, go straight to the
    # api workers via the least_conn upstream defined above.
    location /api/v1/ {
        proxy_pass http://dograh_api;
        proxy_http_version 1.1;

        # Retry on a dead/restarting worker
        proxy_next_upstream error timeout http_502 http_503 http_504;

        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Long-lived WebSockets (audio streaming, signaling)
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        # Don't buffer streamed responses
        proxy_buffering off;
        client_max_body_size 100M;
    }

    location / {
        proxy_pass         http://ui:3010;
        proxy_http_version 1.1;

        # Important for WebSockets / hot reload etc.
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Rewrite localhost MinIO URLs in API responses to use current domain
        sub_filter 'http://localhost:9000/voice-audio/' 'https://$host/voice-audio/';
        sub_filter_once off;
        sub_filter_types application/json text/html;
    }

    location /voice-audio/ {
        proxy_pass http://minio:9000/voice-audio/;

        proxy_http_version 1.1;

        # Headers for file downloads from MinIO
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Allow large file downloads
        proxy_buffering off;
        client_max_body_size 100M;
    }
}
NGINX_EOF
} > nginx.conf

sed -i.tmp "s/SERVER_IP_PLACEHOLDER/$SERVER_IP/g" nginx.conf && rm -f nginx.conf.tmp
echo -e "${GREEN}✓ nginx.conf regenerated${NC}"

###############################################################################
### Step 4 — turnserver.conf (regenerate from embedded template)
###############################################################################

echo -e "${BLUE}[4/5] Regenerating turnserver.conf...${NC}"
cat > turnserver.conf << TURN_EOF
# Coturn TURN Server - Docker Configuration
# Auto-generated by update_remote.sh

# Listener ports
listening-port=3478
tls-listening-port=5349

# Relay port range
min-port=49152
max-port=49200

# Network - external IP for NAT traversal
external-ip=$SERVER_IP

# Realm
realm=dograh.com

# Authentication (TURN REST API with time-limited credentials)
use-auth-secret
static-auth-secret=$TURN_SECRET

# Security
fingerprint
no-cli
no-multicast-peers

# Logging
log-file=stdout
TURN_EOF
echo -e "${GREEN}✓ turnserver.conf regenerated${NC}"

###############################################################################
### Step 5 — reconcile .env (append missing keys; never overwrite existing)
###############################################################################

echo -e "${BLUE}[5/5] Reconciling .env...${NC}"
if ! grep -q "^FASTAPI_WORKERS=" .env; then
    {
        echo ""
        echo "# Number of uvicorn worker processes; nginx load-balances across them"
        echo "# (ports 8000..$((8000 + FASTAPI_WORKERS - 1))) with least_conn."
        echo "FASTAPI_WORKERS=$FASTAPI_WORKERS"
    } >> .env
    echo -e "${GREEN}✓ Added FASTAPI_WORKERS=$FASTAPI_WORKERS to .env${NC}"
else
    echo -e "${GREEN}✓ .env already has FASTAPI_WORKERS — left unchanged${NC}"
fi

###############################################################################
### Done — print restart + rollback instructions
###############################################################################

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                   Update Prepared!                           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Backups: ${BLUE}*.bak.$TIMESTAMP${NC}"
echo ""
echo -e "${YELLOW}To apply, recreate the stack:${NC}"
echo ""
echo -e "  ${BLUE}sudo docker compose --profile remote down${NC}"
echo -e "  ${BLUE}sudo docker compose --profile remote up -d --pull always${NC}"
echo ""
echo -e "${YELLOW}To roll back, restore the backups and recreate:${NC}"
echo ""
echo -e "  ${BLUE}for f in docker-compose.yaml nginx.conf turnserver.conf .env; do${NC}"
echo -e "  ${BLUE}    [[ -f \"\$f.bak.$TIMESTAMP\" ]] && cp \"\$f.bak.$TIMESTAMP\" \"\$f\"${NC}"
echo -e "  ${BLUE}done${NC}"
echo -e "  ${BLUE}sudo docker compose --profile remote down && sudo docker compose --profile remote up -d${NC}"
echo ""
