#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_PATH="$SCRIPT_DIR/lib/setup_common.sh"
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

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                    Dograh Local Setup                        ║"
echo "║       Local docker deployment, optional TURN server          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Ask whether to enable coturn (skip prompt if ENABLE_COTURN is already set)
if [[ -z "${ENABLE_COTURN:-}" ]]; then
    echo -e "${YELLOW}Enable coturn (TURN server) for WebRTC NAT traversal? [y/N]:${NC}"
    read -p "> " ENABLE_COTURN_INPUT
    if [[ "$ENABLE_COTURN_INPUT" =~ ^[Yy] ]]; then
        ENABLE_COTURN=true
    else
        ENABLE_COTURN=false
    fi
fi

if [[ "${ENABLE_COTURN:-false}" == "true" ]]; then
    # Pick a TURN_HOST that's reachable from BOTH the browser (running on the
    # host) and the API container (running in docker). 127.0.0.1 is tempting
    # but doesn't work for the api container — its own loopback isn't where
    # coturn lives, so aiortc can't allocate a relay and FORCE_TURN_RELAY
    # ends up with an empty answer SDP. The host's LAN IP works for both.
    detect_lan_ip() {
        local ip=""
        if command -v ipconfig >/dev/null 2>&1; then
            for iface in en0 en1 en2 en3 en4; do
                ip=$(ipconfig getifaddr "$iface" 2>/dev/null)
                [[ -n "$ip" ]] && { echo "$ip"; return; }
            done
        fi
        if command -v ip >/dev/null 2>&1; then
            ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
            [[ -n "$ip" ]] && { echo "$ip"; return; }
        fi
        if command -v hostname >/dev/null 2>&1; then
            ip=$(hostname -I 2>/dev/null | awk '{print $1}')
            [[ -n "$ip" ]] && { echo "$ip"; return; }
        fi
    }

    DEFAULT_TURN_HOST="$(detect_lan_ip)"
    DEFAULT_TURN_HOST="${DEFAULT_TURN_HOST:-127.0.0.1}"

    # Get the host browsers/peers will use to reach the TURN server
    if [[ -z "${TURN_HOST:-}" ]]; then
        echo -e "${YELLOW}Enter the host browsers AND the API container will use to reach TURN${NC}"
        echo -e "${YELLOW}(press Enter for ${DEFAULT_TURN_HOST}):${NC}"
        read -p "> " TURN_HOST
    fi
    TURN_HOST="${TURN_HOST:-$DEFAULT_TURN_HOST}"

    # Validate that TURN_HOST is either an IP or a hostname (basic check)
    if ! [[ "$TURN_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then
        echo -e "${RED}Error: TURN host must be an IP address or hostname${NC}"
        exit 1
    fi

    # Get the TURN secret (skip prompt if TURN_SECRET is already set)
    if [[ -z "${TURN_SECRET:-}" ]]; then
        echo -e "${YELLOW}Enter a shared secret for the TURN server (press Enter to generate a random one):${NC}"
        read -sp "> " TURN_SECRET
        echo ""
    fi

    if [[ -z "${TURN_SECRET:-}" ]]; then
        TURN_SECRET=$(openssl rand -hex 32)
        echo -e "${BLUE}Generated random TURN secret${NC}"
    fi
fi

# Telemetry opt-out (default: true)
ENABLE_TELEMETRY="${ENABLE_TELEMETRY:-true}"

# Container registry (defaults to the public OSS registry)
REGISTRY="${REGISTRY:-ghcr.io/dograh-hq}"

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Coturn:        ${BLUE}${ENABLE_COTURN:-false}${NC}"
if [[ "${ENABLE_COTURN:-false}" == "true" ]]; then
    echo -e "  TURN Host:     ${BLUE}$TURN_HOST${NC}"
    echo -e "  TURN Secret:   ${BLUE}********${NC}"
fi
echo -e "  Telemetry:     ${BLUE}$ENABLE_TELEMETRY${NC}"
echo -e "  Registry:      ${BLUE}$REGISTRY${NC}"
echo ""

# Download compose file (skip when DOGRAH_SKIP_DOWNLOAD=1 — e.g. local repo testing).
TOTAL_STEPS=2

if [[ "${DOGRAH_SKIP_DOWNLOAD:-}" != "1" ]]; then
    if [[ "${ENABLE_COTURN:-false}" == "true" ]]; then
        echo -e "${BLUE}[1/$TOTAL_STEPS] Downloading docker-compose.yaml and TURN helper bundle...${NC}"
    else
        echo -e "${BLUE}[1/$TOTAL_STEPS] Downloading docker-compose.yaml...${NC}"
    fi
    curl -sS -o docker-compose.yaml https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml
    if [[ "${ENABLE_COTURN:-false}" == "true" ]]; then
        dograh_download_init_support_bundle "$(pwd)" "main"
    fi
    echo -e "${GREEN}✓ Deployment files downloaded${NC}"
else
    echo -e "${BLUE}[1/$TOTAL_STEPS] Using docker-compose.yaml in current directory${NC}"
fi

if [[ "${ENABLE_COTURN:-false}" == "true" ]]; then
    [[ -f scripts/run_dograh_init.sh ]] || dograh_fail "scripts/run_dograh_init.sh not found. Re-run setup_local.sh without DOGRAH_SKIP_DOWNLOAD=1, or use a full repo checkout."
    [[ -f scripts/lib/setup_common.sh ]] || dograh_fail "scripts/lib/setup_common.sh not found. Re-run setup_local.sh without DOGRAH_SKIP_DOWNLOAD=1, or use a full repo checkout."
    [[ -f deploy/templates/turnserver.remote.conf.template ]] || dograh_fail "deploy/templates/turnserver.remote.conf.template not found. Re-run setup_local.sh without DOGRAH_SKIP_DOWNLOAD=1, or use a full repo checkout."
fi

# Generate .env
ENV_STEP=$TOTAL_STEPS
echo -e "${BLUE}[$ENV_STEP/$TOTAL_STEPS] Creating environment file...${NC}"
OSS_JWT_SECRET=$(openssl rand -hex 32)

cat > .env << ENV_EOF
# Container registry for Dograh images
REGISTRY=$REGISTRY

# JWT secret for OSS authentication
OSS_JWT_SECRET=$OSS_JWT_SECRET

# Telemetry (set to false to disable)
ENABLE_TELEMETRY=$ENABLE_TELEMETRY
ENV_EOF

if [[ "${ENABLE_COTURN:-false}" == "true" ]]; then
    cat >> .env << ENV_EOF

# TURN Server Configuration (time-limited credentials via TURN REST API)
TURN_HOST=$TURN_HOST
TURN_SECRET=$TURN_SECRET
ENV_EOF
fi
echo -e "${GREEN}✓ .env file created${NC}"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Setup Complete!                           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Files created in ${BLUE}$(pwd)${NC}:"
echo "  - docker-compose.yaml"
echo "  - .env"
if [[ "${ENABLE_COTURN:-false}" == "true" ]]; then
    echo "  - scripts/run_dograh_init.sh"
    echo "  - scripts/lib/setup_common.sh"
    echo "  - deploy/templates/"
fi
echo ""
if [[ "${ENABLE_COTURN:-false}" == "true" ]]; then
    echo -e "${YELLOW}To start Dograh with TURN, run:${NC}"
    echo ""
    echo -e "  ${BLUE}docker compose --profile local-turn up --pull always${NC}"
else
    echo -e "${YELLOW}To start Dograh, run:${NC}"
    echo ""
    echo -e "  ${BLUE}docker compose up --pull always${NC}"
fi
echo ""
echo -e "${YELLOW}Your application will be available at:${NC}"
echo ""
echo -e "  ${BLUE}http://localhost:3010${NC}"
echo ""
