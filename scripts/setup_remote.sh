#!/bin/bash
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║                   Dograh Remote Setup                        ║"
echo "║      Automated HTTPS deployment with TURN server             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Get the public IP address (skip prompt if SERVER_IP is already set)
if [[ -z "$SERVER_IP" ]]; then
    echo -e "${YELLOW}Enter your server's public IP address:${NC}"
    read -p "> " SERVER_IP
fi

if [[ -z "$SERVER_IP" ]]; then
    echo -e "${RED}Error: IP address cannot be empty${NC}"
    exit 1
fi

# Validate IP address format (basic validation)
if ! [[ "$SERVER_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}Error: Invalid IP address format${NC}"
    exit 1
fi

# Get the TURN secret (skip prompt if TURN_SECRET is already set)
if [[ -z "$TURN_SECRET" ]]; then
    echo -e "${YELLOW}Enter a shared secret for the TURN server (press Enter to generate a random one):${NC}"
    read -sp "> " TURN_SECRET
    echo ""
fi

if [[ -z "$TURN_SECRET" ]]; then
    TURN_SECRET=$(openssl rand -hex 32)
    echo -e "${BLUE}Generated random TURN secret${NC}"
fi

# Deployment mode. Skip prompt if DEPLOY_MODE is already set. Non-interactive
# callers (cloud-init, CI, terraform) without a TTY default to "prebuilt" so
# existing automation keeps working without changes - explicitly set
# DEPLOY_MODE=build to opt into source builds from a non-interactive context.
if [[ -z "$DEPLOY_MODE" ]]; then
    if [[ -t 0 ]]; then
        echo ""
        echo -e "${YELLOW}Deployment mode:${NC}"
        echo "  1) prebuilt - pull official dograh images (recommended, fastest)"
        echo "  2) build    - build images from source (for forks or local customizations)"
        read -p "Choose [1]: " mode_choice
        mode_choice="${mode_choice:-1}"
        case "$mode_choice" in
            1|prebuilt) DEPLOY_MODE="prebuilt" ;;
            2|build)    DEPLOY_MODE="build" ;;
            *) echo -e "${RED}Error: invalid choice '$mode_choice'${NC}"; exit 1 ;;
        esac
    else
        DEPLOY_MODE="prebuilt"
    fi
fi

# Build mode needs source code - either use existing repo or clone fresh.
# Same TTY rule: prompt interactively, otherwise pick sensible defaults so
# automation that sets DEPLOY_MODE=build doesn't need to spell everything out.
if [[ "$DEPLOY_MODE" == "build" ]]; then
    if [[ -z "$REPO_SOURCE" ]]; then
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
        if [[ -z "$FORK_REPO" ]]; then
            if [[ -t 0 ]]; then
                echo ""
                echo -e "${YELLOW}GitHub repo to clone (format: owner/name):${NC}"
                read -p "[dograh-hq/dograh]: " FORK_REPO
                FORK_REPO="${FORK_REPO:-dograh-hq/dograh}"
            else
                FORK_REPO="dograh-hq/dograh"
            fi
        fi
        if [[ -z "$BRANCH" ]]; then
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

# Telemetry opt-out (default: true)
ENABLE_TELEMETRY="${ENABLE_TELEMETRY:-true}"

# Number of uvicorn worker processes. Each runs as its own process on a
# distinct port (8000, 8001, ...) and nginx balances across them with
# least_conn. Better than uvicorn --workers for long-lived WebSocket
# connections, which would otherwise stick to whichever worker accepted them.
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

if ! [[ "$FASTAPI_WORKERS" =~ ^[1-9][0-9]*$ ]]; then
    echo -e "${RED}Error: FASTAPI_WORKERS must be a positive integer (got: $FASTAPI_WORKERS)${NC}"
    exit 1
fi

# Where setup artifacts (.env, certs, nginx.conf, etc.) will land. Build mode
# with an existing repo writes them next to docker-compose.yaml in cwd;
# everything else writes into a fresh dograh/ subdirectory.
if [[ "$DEPLOY_MODE" == "build" && "$REPO_SOURCE" == "existing" ]]; then
    TARGET_DIR="."
else
    TARGET_DIR="dograh"
fi

# Refuse to overwrite an existing install - re-running this script would
# regenerate OSS_JWT_SECRET (invalidating logged-in sessions), reset the
# TURN secret (breaking WebRTC auth), and overwrite nginx.conf customizations.
# Set DOGRAH_FORCE_OVERWRITE=1 to bypass; DOGRAH_SKIP_DOWNLOAD=1 (used by e2e)
# also bypasses since those flows manage state themselves.
if [[ "$DOGRAH_FORCE_OVERWRITE" != "1" && "$DOGRAH_SKIP_DOWNLOAD" != "1" ]]; then
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
        echo -e "${RED}  - reset nginx.conf and turnserver.conf customizations${NC}"
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

# Total step count depends on mode (build adds the override-file step)
if [[ "$DEPLOY_MODE" == "build" ]]; then
    TOTAL=7
else
    TOTAL=6
fi

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Server IP:        ${BLUE}$SERVER_IP${NC}"
echo -e "  TURN Secret:      ${BLUE}********${NC}"
echo -e "  Deploy mode:      ${BLUE}$DEPLOY_MODE${NC}"
echo -e "  FastAPI workers:  ${BLUE}$FASTAPI_WORKERS${NC}  (ports 8000..$((8000 + FASTAPI_WORKERS - 1)))"
if [[ "$DEPLOY_MODE" == "build" ]]; then
    if [[ "$REPO_SOURCE" == "clone" ]]; then
        echo -e "  Source:        ${BLUE}clone $FORK_REPO@$BRANCH${NC}"
    else
        echo -e "  Source:        ${BLUE}existing repo at $(pwd)${NC}"
    fi
fi
echo ""

# Step 1: get the source - either the standalone compose file (prebuilt mode)
# or the full repo (build mode). Skip the download/clone when
# DOGRAH_SKIP_DOWNLOAD=1 (e.g. e2e tests that already have everything in place).
if [[ "$DEPLOY_MODE" == "build" ]]; then
    if [[ "$DOGRAH_SKIP_DOWNLOAD" == "1" ]]; then
        echo -e "${BLUE}[1/$TOTAL] Using existing repo in current directory${NC}"
    elif [[ "$REPO_SOURCE" == "clone" ]]; then
        if [[ -e "dograh" ]]; then
            echo -e "${RED}Error: 'dograh' directory already exists. Remove it or re-run with REPO_SOURCE=existing from inside it.${NC}"
            exit 1
        fi
        echo -e "${BLUE}[1/$TOTAL] Cloning $FORK_REPO (branch: $BRANCH)...${NC}"
        git clone --branch "$BRANCH" --recurse-submodules "https://github.com/$FORK_REPO.git" dograh
        cd dograh
        echo -e "${GREEN}✓ Repo cloned${NC}"
    else
        echo -e "${BLUE}[1/$TOTAL] Using existing repo at $(pwd)${NC}"
    fi
else
    if [[ "$DOGRAH_SKIP_DOWNLOAD" != "1" ]]; then
        mkdir -p dograh 2>/dev/null || true
        cd dograh

        echo -e "${BLUE}[1/$TOTAL] Downloading docker-compose.yaml...${NC}"
        curl -sS -o docker-compose.yaml https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml
        echo -e "${GREEN}✓ docker-compose.yaml downloaded${NC}"
    else
        echo -e "${BLUE}[1/$TOTAL] Using docker-compose.yaml in current directory${NC}"
    fi
fi

echo -e "${BLUE}[2/$TOTAL] Creating nginx.conf...${NC}"
# Build the upstream block first (needs shell interpolation for the server
# lines), then append the static server blocks via a quoted heredoc. The
# SERVER_IP_PLACEHOLDER gets replaced by sed below.
{
    echo "# Backend API workers — one uvicorn process per port, balanced by least_conn."
    echo "# Generated by setup_remote.sh; regenerate to change worker count."
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

# Replace placeholder with actual IP
sed -i.bak "s/SERVER_IP_PLACEHOLDER/$SERVER_IP/g" nginx.conf && rm -f nginx.conf.bak
echo -e "${GREEN}✓ nginx.conf created${NC}"

echo -e "${BLUE}[3/$TOTAL] Creating SSL certificate generation script...${NC}"
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

echo -e "${BLUE}[4/$TOTAL] Generating SSL certificates...${NC}"
./generate_certificate.sh
echo -e "${GREEN}✓ SSL certificates generated${NC}"

echo -e "${BLUE}[5/$TOTAL] Creating TURN server configuration...${NC}"
cat > turnserver.conf << TURN_EOF
# Coturn TURN Server - Docker Configuration
# Auto-generated by setup_remote.sh

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
echo -e "${GREEN}✓ turnserver.conf created${NC}"

echo -e "${BLUE}[6/$TOTAL] Creating environment file...${NC}"
OSS_JWT_SECRET=$(openssl rand -hex 32)

cat > .env << ENV_EOF
# Change environment from local to production so that coturn filters local IPs
ENVIRONMENT=production

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
# (ports 8000..$((8000 + FASTAPI_WORKERS - 1))) with least_conn.
# Must match the upstream block in nginx.conf — re-run setup_remote.sh
# (with DOGRAH_FORCE_OVERWRITE=1) to change.
FASTAPI_WORKERS=$FASTAPI_WORKERS
ENV_EOF
echo -e "${GREEN}✓ .env file created${NC}"

# In build mode, write the override file that swaps prebuilt images for
# local builds. Compose auto-loads docker-compose.override.yaml, so no -f flag
# is needed at runtime.
if [[ "$DEPLOY_MODE" == "build" ]]; then
    echo -e "${BLUE}[7/$TOTAL] Creating docker-compose.override.yaml...${NC}"
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
echo "  - nginx.conf"
echo "  - turnserver.conf"
echo "  - generate_certificate.sh"
echo "  - certs/local.crt"
echo "  - certs/local.key"
echo "  - .env"
echo ""
echo -e "${YELLOW}To start Dograh, run:${NC}"
echo ""
# The script's own cd into dograh/ doesn't persist to the user's shell, so
# remind them to cd themselves — except when they're already there (build mode
# with REPO_SOURCE=existing, which writes into cwd).
if [[ "$DEPLOY_MODE" != "build" || "$REPO_SOURCE" != "existing" ]]; then
    echo -e "  ${BLUE}cd $(pwd)${NC}"
fi
if [[ "$DEPLOY_MODE" == "build" ]]; then
    echo -e "  ${BLUE}sudo docker compose --profile remote up -d --build${NC}"
    echo ""
    echo -e "${YELLOW}A docker-compose.override.yaml has been created alongside${NC}"
    echo -e "${YELLOW}docker-compose.yaml. Compose auto-loads it, so no -f flag is${NC}"
    echo -e "${YELLOW}needed — it swaps the prebuilt images for local builds.${NC}"
    echo ""
    echo -e "${YELLOW}The first build can take several minutes${NC}"
    echo -e "${YELLOW}(downloading base images, installing dependencies).${NC}"
    echo -e "${YELLOW}If you know how to speed this up, we would love a pull request.${NC}"
    echo ""
    echo -e "${YELLOW}To rebuild after editing api/ or ui/ code:${NC}"
    echo ""
    echo -e "  ${BLUE}sudo docker compose --profile remote build && sudo docker compose --profile remote up -d${NC}"
else
    echo -e "  ${BLUE}sudo docker compose --profile remote up --pull always${NC}"
fi
echo ""
echo -e "${YELLOW}Your application will be available at:${NC}"
echo ""
echo -e "  ${BLUE}https://$SERVER_IP${NC}"
echo ""
echo -e "${YELLOW}Note:${NC} Your browser will show a security warning for the self-signed"
echo "certificate. You can safely accept it to proceed."
echo ""
