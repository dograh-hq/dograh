#!/bin/bash
set -euo pipefail

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
echo "║                   Dograh Remote Setup                        ║"
echo "║      Automated HTTPS deployment with TURN server             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Get the public IP address (skip prompt if SERVER_IP is already set)
if [[ -z "${SERVER_IP:-}" ]]; then
    echo -e "${YELLOW}Enter your server's public IP address:${NC}"
    read -p "> " SERVER_IP
fi

if [[ -z "$SERVER_IP" ]]; then
    dograh_fail "IP address cannot be empty"
fi

if ! dograh_is_ipv4 "$SERVER_IP"; then
    dograh_fail "Invalid IP address format"
fi

# Get the TURN secret (skip prompt if TURN_SECRET is already set)
if [[ -z "${TURN_SECRET:-}" ]]; then
    echo -e "${YELLOW}Enter a shared secret for the TURN server (press Enter to generate a random one):${NC}"
    read -sp "> " TURN_SECRET
    echo ""
fi

if [[ -z "$TURN_SECRET" ]]; then
    TURN_SECRET=$(openssl rand -hex 32)
    echo -e "${BLUE}Generated random TURN secret${NC}"
fi

# Deployment mode. Skip prompt if DEPLOY_MODE is already set. Non-interactive
# callers without a TTY default to "prebuilt" to keep automation stable.
if [[ -z "${DEPLOY_MODE:-}" ]]; then
    if [[ -t 0 ]]; then
        echo ""
        echo -e "${YELLOW}Deployment mode:${NC}"
        echo "  1) prebuilt - pull official dograh images (recommended, fastest)"
        echo "  2) build    - build images from source (for forks or local customizations)"
        read -p "Choose [1]: " mode_choice
        mode_choice="${mode_choice:-1}"
        case "$mode_choice" in
            1|prebuilt) DEPLOY_MODE="prebuilt" ;;
            2|build) DEPLOY_MODE="build" ;;
            *) dograh_fail "invalid choice '$mode_choice'" ;;
        esac
    else
        DEPLOY_MODE="prebuilt"
    fi
fi

if [[ "$DEPLOY_MODE" == "build" ]]; then
    if [[ -z "${REPO_SOURCE:-}" ]]; then
        if [[ -d ".git" ]] && [[ -f "docker-compose.yaml" ]]; then
            if [[ -t 0 ]]; then
                echo ""
                echo -e "${YELLOW}Detected a git repo with docker-compose.yaml in $(pwd).${NC}"
                read -p "Build from this repo? [Y/n]: " use_existing
                use_existing="${use_existing:-Y}"
                if [[ "$use_existing" =~ ^[Yy] ]]; then
                    REPO_SOURCE="existing"
                else
                    REPO_SOURCE="clone"
                fi
            else
                REPO_SOURCE="existing"
            fi
        else
            REPO_SOURCE="clone"
        fi
    fi

    if [[ "$REPO_SOURCE" == "clone" ]]; then
        if [[ -z "${FORK_REPO:-}" ]]; then
            if [[ -t 0 ]]; then
                echo ""
                echo -e "${YELLOW}GitHub repo to clone (format: owner/name):${NC}"
                read -p "[dograh-hq/dograh]: " FORK_REPO
                FORK_REPO="${FORK_REPO:-dograh-hq/dograh}"
            else
                FORK_REPO="dograh-hq/dograh"
            fi
        fi

        if [[ -z "${BRANCH:-}" ]]; then
            if [[ -t 0 ]]; then
                echo -e "${YELLOW}Branch:${NC}"
                read -p "[main]: " BRANCH
                BRANCH="${BRANCH:-main}"
            else
                BRANCH="main"
            fi
        fi
    fi
fi

ENABLE_TELEMETRY="${ENABLE_TELEMETRY:-true}"
FASTAPI_WORKERS="${FASTAPI_WORKERS:-}"

if [[ -z "$FASTAPI_WORKERS" ]]; then
    if [[ -t 0 ]]; then
        echo ""
        echo -e "${YELLOW}Number of FastAPI workers (uvicorn processes nginx will load-balance):${NC}"
        read -p "[4]: " FASTAPI_WORKERS
        FASTAPI_WORKERS="${FASTAPI_WORKERS:-4}"
    else
        FASTAPI_WORKERS="4"
    fi
fi

[[ "$FASTAPI_WORKERS" =~ ^[1-9][0-9]*$ ]] || dograh_fail "FASTAPI_WORKERS must be a positive integer (got: $FASTAPI_WORKERS)"

if [[ "$DEPLOY_MODE" == "build" && "${REPO_SOURCE:-}" == "existing" ]]; then
    TARGET_DIR="."
else
    TARGET_DIR="dograh"
fi

if [[ "${DOGRAH_FORCE_OVERWRITE:-}" != "1" && "${DOGRAH_SKIP_DOWNLOAD:-}" != "1" ]]; then
    if [[ -f "$TARGET_DIR/.env" ]]; then
        if [[ "$TARGET_DIR" == "." ]]; then
            existing_path="$(pwd)/.env"
        else
            existing_path="$(pwd)/$TARGET_DIR/.env"
        fi
        echo ""
        echo -e "${YELLOW}Detected an existing Dograh install:${NC}"
        echo -e "  ${YELLOW}$existing_path${NC}"
        echo ""
        echo -e "${RED}Refusing to continue - re-running setup would:${NC}"
        echo -e "${RED}  - overwrite .env (invalidates sessions, breaks TURN auth)${NC}"
        echo -e "${RED}  - regenerate SSL certificates${NC}"
        echo -e "${RED}  - replace the validated remote deployment bundle${NC}"
        echo ""
        echo -e "${BLUE}To upgrade an existing install, follow:${NC}"
        echo -e "  ${BLUE}https://docs.dograh.com/deployment/update${NC}"
        echo ""
        echo -e "${BLUE}To wipe state and reinstall from scratch, re-run with:${NC}"
        echo -e "  ${BLUE}DOGRAH_FORCE_OVERWRITE=1 <same command>${NC}"
        echo ""
        exit 1
    fi
fi

if [[ "$DEPLOY_MODE" == "build" ]]; then
    TOTAL=6
else
    TOTAL=5
fi

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Server IP:        ${BLUE}$SERVER_IP${NC}"
echo -e "  TURN Secret:      ${BLUE}********${NC}"
echo -e "  Deploy mode:      ${BLUE}$DEPLOY_MODE${NC}"
echo -e "  FastAPI workers:  ${BLUE}$FASTAPI_WORKERS${NC}  (ports 8000..$((8000 + FASTAPI_WORKERS - 1)))"
if [[ "$DEPLOY_MODE" == "build" ]]; then
    if [[ "${REPO_SOURCE:-}" == "clone" ]]; then
        echo -e "  Source:           ${BLUE}clone $FORK_REPO@$BRANCH${NC}"
    else
        echo -e "  Source:           ${BLUE}existing repo at $(pwd)${NC}"
    fi
fi
echo ""

if [[ "$DEPLOY_MODE" == "build" ]]; then
    if [[ "${DOGRAH_SKIP_DOWNLOAD:-}" == "1" ]]; then
        echo -e "${BLUE}[1/$TOTAL] Using existing repo in current directory${NC}"
    elif [[ "${REPO_SOURCE:-}" == "clone" ]]; then
        if [[ -e "dograh" ]]; then
            dograh_fail "'dograh' directory already exists. Remove it or re-run with REPO_SOURCE=existing from inside it."
        fi
        echo -e "${BLUE}[1/$TOTAL] Cloning $FORK_REPO (branch: $BRANCH)...${NC}"
        git clone --branch "$BRANCH" --recurse-submodules "https://github.com/$FORK_REPO.git" dograh
        cd dograh
        echo -e "${GREEN}✓ Repo cloned${NC}"
    else
        echo -e "${BLUE}[1/$TOTAL] Using existing repo at $(pwd)${NC}"
    fi
else
    if [[ "${DOGRAH_SKIP_DOWNLOAD:-}" != "1" ]]; then
        mkdir -p dograh 2>/dev/null || true
        cd dograh

        echo -e "${BLUE}[1/$TOTAL] Downloading deployment bundle...${NC}"
        curl -fsSL -o docker-compose.yaml "https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml"
        dograh_download_remote_support_bundle "$(pwd)" "main"
        echo -e "${GREEN}✓ Deployment bundle downloaded${NC}"
    else
        echo -e "${BLUE}[1/$TOTAL] Using deployment files in current directory${NC}"
    fi
fi

DOGRAH_DEPLOY_PROJECT_DIR="$(pwd)"

if [[ "$DEPLOY_MODE" != "prebuilt" ]]; then
    chmod +x remote_up.sh
fi

echo -e "${BLUE}[2/$TOTAL] Creating SSL certificate generation script...${NC}"
cat > generate_certificate.sh << CERT_EOF
#!/bin/bash
mkdir -p certs
openssl req -x509 -nodes -newkey rsa:2048 \\
  -keyout certs/local.key \\
  -out certs/local.crt \\
  -days 365 \\
  -subj "/CN=$SERVER_IP"
CERT_EOF
chmod +x generate_certificate.sh
echo -e "${GREEN}✓ generate_certificate.sh created${NC}"

echo -e "${BLUE}[3/$TOTAL] Generating SSL certificates...${NC}"
./generate_certificate.sh
echo -e "${GREEN}✓ SSL certificates generated${NC}"

echo -e "${BLUE}[4/$TOTAL] Creating environment file...${NC}"
OSS_JWT_SECRET=$(openssl rand -hex 32)

cat > .env << ENV_EOF
# Change environment from local to production so that coturn filters local IPs
ENVIRONMENT=production

# Canonical public host/base URL for this install.
SERVER_IP=$SERVER_IP
PUBLIC_HOST=$SERVER_IP
PUBLIC_BASE_URL=https://$SERVER_IP

# Backend API endpoint (public URL the backend uses to build webhook/embed links)
BACKEND_API_ENDPOINT=https://$SERVER_IP

# Public URL browsers use to fetch objects from MinIO (proxied by nginx)
MINIO_PUBLIC_ENDPOINT=https://$SERVER_IP

# TURN Server Configuration (time-limited credentials via TURN REST API)
TURN_HOST=$SERVER_IP
TURN_SECRET=$TURN_SECRET

# JWT secret for OSS authentication
OSS_JWT_SECRET=$OSS_JWT_SECRET

# Telemetry (set to false to disable)
ENABLE_TELEMETRY=$ENABLE_TELEMETRY

# Number of uvicorn worker processes; nginx load-balances across them
FASTAPI_WORKERS=$FASTAPI_WORKERS
ENV_EOF
echo -e "${GREEN}✓ .env file created${NC}"

echo -e "${BLUE}[5/$TOTAL] Validating remote init configuration...${NC}"
dograh_prepare_remote_install "$(pwd)"
echo -e "${GREEN}✓ Remote init configuration validated${NC}"

if [[ "$DEPLOY_MODE" == "build" ]]; then
    echo -e "${BLUE}[6/$TOTAL] Creating docker-compose.override.yaml...${NC}"
    cat > docker-compose.override.yaml << 'OVERRIDE_EOF'
# Auto-generated by setup_remote.sh (build mode).
# Overrides docker-compose.yaml to build api and ui images from local source
# instead of pulling them from a registry. Remove this file to revert to
# pulling prebuilt images.
services:
  api:
    build:
      context: .
      dockerfile: api/Dockerfile
    image: dograh-local/dograh-api:local
    pull_policy: never

  ui:
    build:
      context: .
      dockerfile: ui/Dockerfile
    image: dograh-local/dograh-ui:local
    pull_policy: never
OVERRIDE_EOF
    echo -e "${GREEN}✓ docker-compose.override.yaml created${NC}"
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Setup Complete!                           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Files created in ${BLUE}$(pwd)${NC}:"
echo "  - docker-compose.yaml"
if [[ "$DEPLOY_MODE" == "build" ]]; then
    echo "  - docker-compose.override.yaml  (build directives)"
fi
echo "  - remote_up.sh"
echo "  - scripts/run_dograh_init.sh"
echo "  - deploy/templates/"
echo "  - generate_certificate.sh"
echo "  - certs/local.crt"
echo "  - certs/local.key"
echo "  - .env"
echo ""
echo -e "${YELLOW}To start Dograh, run:${NC}"
echo ""
if [[ "$DEPLOY_MODE" != "build" || "${REPO_SOURCE:-}" != "existing" ]]; then
    echo -e "  ${BLUE}cd $(pwd)${NC}"
fi
if [[ "$DEPLOY_MODE" == "build" ]]; then
    echo -e "  ${BLUE}./remote_up.sh --build${NC}"
    echo ""
    echo -e "${YELLOW}A docker-compose.override.yaml has been created alongside${NC}"
    echo -e "${YELLOW}docker-compose.yaml. Compose auto-loads it, so no -f flag is${NC}"
    echo -e "${YELLOW}needed — it swaps the prebuilt images for local builds.${NC}"
else
    echo -e "  ${BLUE}./remote_up.sh${NC}"
fi
echo ""
echo -e "${YELLOW}Your application will be available at:${NC}"
echo ""
echo -e "  ${BLUE}https://$SERVER_IP${NC}"
echo ""
echo -e "${YELLOW}Note:${NC} Your browser will show a security warning for the self-signed"
echo "certificate. You can safely accept it to proceed."
echo ""
