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
echo "║              Dograh Custom Domain Setup                      ║"
echo "║     Automated Let's Encrypt SSL certificate setup            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

if [[ $EUID -ne 0 ]]; then
    dograh_fail "This script must be run as root or with sudo"
fi

if [[ ! -d "dograh" ]]; then
    echo -e "${RED}Error: 'dograh' directory not found.${NC}"
    echo -e "${YELLOW}Please run this script from the directory containing your Dograh installation.${NC}"
    echo -e "${YELLOW}If you haven't set up Dograh yet, run the remote setup first:${NC}"
    echo -e "${BLUE}  curl -o setup_remote.sh https://raw.githubusercontent.com/dograh-hq/dograh/main/scripts/setup_remote.sh && chmod +x setup_remote.sh && sudo ./setup_remote.sh${NC}"
    exit 1
fi

echo -e "${YELLOW}Enter your domain name (e.g., voice.yourcompany.com):${NC}"
read -p "> " DOMAIN_NAME
[[ -n "$DOMAIN_NAME" ]] || dograh_fail "Domain name cannot be empty"

if ! [[ "$DOMAIN_NAME" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$ ]]; then
    dograh_fail "Invalid domain name format"
fi

echo -e "${YELLOW}Enter your email address for SSL certificate notifications:${NC}"
read -p "> " EMAIL_ADDRESS
[[ -n "$EMAIL_ADDRESS" ]] || dograh_fail "Email address cannot be empty (required by Let's Encrypt)"

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Domain:  ${BLUE}$DOMAIN_NAME${NC}"
echo -e "  Email:   ${BLUE}$EMAIL_ADDRESS${NC}"
echo ""

echo -e "${BLUE}[1/6] Verifying DNS configuration...${NC}"
SERVER_IP="$(curl -s ifconfig.me || curl -s icanhazip.com || echo "")"
RESOLVED_IP="$(dig +short "$DOMAIN_NAME" | tail -1)"

if [[ -z "$SERVER_IP" ]]; then
    dograh_warn "Warning: Could not detect server's public IP"
elif [[ "$RESOLVED_IP" != "$SERVER_IP" ]]; then
    echo -e "${YELLOW}Warning: Domain '$DOMAIN_NAME' resolves to '$RESOLVED_IP' but this server's IP is '$SERVER_IP'${NC}"
    echo -e "${YELLOW}Make sure your DNS A record points to this server before proceeding.${NC}"
    echo ""
    read -p "Continue anyway? (y/N) > " CONTINUE
    if [[ ! "$CONTINUE" =~ ^[Yy]$ ]]; then
        echo -e "${RED}Setup cancelled. Please configure DNS and try again.${NC}"
        exit 1
    fi
else
    echo -e "${GREEN}✓ DNS is correctly configured (${RESOLVED_IP})${NC}"
fi

echo -e "${BLUE}[2/6] Installing Certbot...${NC}"
dograh_install_certbot || dograh_fail "Could not install certbot. Please install it manually and re-run."
echo -e "${GREEN}✓ Certbot installed${NC}"

echo -e "${BLUE}[3/6] Pointing .env at $DOMAIN_NAME and starting services...${NC}"
cd dograh
DOGRAH_DEPLOY_PROJECT_DIR="$(pwd)"
DOGRAH_PATH="$(pwd)"

if [[ ! -f remote_up.sh || ! -f scripts/lib/setup_common.sh ]]; then
    dograh_download_remote_support_bundle "$(pwd)" "main"
fi

dograh_require_init_compose_layout "$(pwd)"

dograh_load_env_file .env
if [[ -z "${SERVER_IP:-}" ]]; then
    SERVER_IP="$(dograh_infer_server_ip "$(pwd)" || true)"
fi
[[ -n "${SERVER_IP:-}" ]] || dograh_fail "Could not determine SERVER_IP from the existing install"

dograh_set_env_key .env SERVER_IP "$SERVER_IP"
dograh_set_env_key .env PUBLIC_HOST "$DOMAIN_NAME"
dograh_set_env_key .env PUBLIC_BASE_URL "https://$DOMAIN_NAME"
dograh_delete_env_key .env BACKEND_URL
# Switching domains is an explicit repoint of the whole deployment. Drop any
# legacy per-subsystem endpoint keys an older install pinned to the previous host
# so they re-derive from the new PUBLIC_BASE_URL / PUBLIC_HOST (see api/constants.py).
# No-op on current installs, which don't write these keys.
dograh_delete_env_key .env BACKEND_API_ENDPOINT
dograh_delete_env_key .env MINIO_PUBLIC_ENDPOINT
dograh_delete_env_key .env TURN_HOST
dograh_prepare_remote_install "$(pwd)"

# Bring the stack up (recreating it) so dograh-init re-renders nginx with the
# domain server_name and the ACME challenge location, served with the existing
# certificate. certbot --webroot then validates against the running nginx:
# no downtime, and (unlike --standalone) renewal keeps working later while
# nginx holds port 80.
./remote_up.sh

echo -e "${BLUE}Waiting for nginx to answer on port 80...${NC}"
nginx_ready=0
for ((i=1; i<=60; i++)); do
    if curl -s -o /dev/null --max-time 3 "http://127.0.0.1/"; then
        nginx_ready=1
        break
    fi
    sleep 2
done
[[ "$nginx_ready" == "1" ]] || dograh_fail "nginx did not come up on port 80; cannot run the ACME challenge."
echo -e "${GREEN}✓ Services running and serving the ACME challenge${NC}"

echo -e "${BLUE}[4/6] Obtaining Let's Encrypt certificate for $DOMAIN_NAME...${NC}"
if ! dograh_issue_letsencrypt_webroot "$(pwd)" "$DOMAIN_NAME" "$EMAIL_ADDRESS"; then
    echo -e "${RED}✗ Certificate issuance failed${NC}"
    echo ""
    echo -e "${YELLOW}Common causes:${NC}"
    echo "  - Port 80 not reachable from the internet (open it in your firewall)"
    echo "  - DNS A record for $DOMAIN_NAME does not point to this server yet"
    echo "  - Let's Encrypt rate limit reached (wait, then retry)"
    echo "  - Upgrading an older install: run ./update_remote.sh first to refresh the"
    echo "    nginx template so it serves the ACME challenge, then re-run this script"
    echo ""
    echo -e "The stack is still running with the previous certificate."
    echo -e "After fixing the issue, re-run: ${BLUE}sudo ./setup_custom_domain.sh${NC}"
    echo ""
    exit 1
fi
echo -e "${GREEN}✓ Certificate issued and copied to certs/${NC}"

echo -e "${BLUE}[5/6] Loading the new certificate (restarting nginx)...${NC}"
docker compose --profile remote restart nginx >/dev/null 2>&1 || true
echo -e "${GREEN}✓ nginx restarted${NC}"

echo -e "${BLUE}[6/6] Configuring automatic certificate renewal...${NC}"
dograh_install_cert_renewal_hook "$(pwd)" "$DOMAIN_NAME"
if certbot renew --dry-run --quiet; then
    echo -e "${GREEN}✓ Auto-renewal configured and tested${NC}"
else
    echo -e "${YELLOW}⚠ Auto-renewal dry-run had issues, but the certificate is installed${NC}"
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              Custom Domain Setup Complete!                   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Your application is now available at:${NC}"
echo ""
echo -e "  ${BLUE}https://$DOMAIN_NAME${NC}"
echo ""
echo -e "${GREEN}SSL Certificate Details:${NC}"
echo -e "  Certificate: $DOGRAH_PATH/certs/local.crt"
echo -e "  Private Key: $DOGRAH_PATH/certs/local.key"
echo -e "  Auto-renewal: Enabled (certificates renew automatically)"
echo ""
echo -e "${YELLOW}Files modified:${NC}"
echo "  - dograh/.env (canonical public host/base URL updated)"
echo "  - dograh/certs/local.crt (SSL certificate)"
echo "  - dograh/certs/local.key (SSL private key)"
echo "  - /etc/letsencrypt/renewal-hooks/deploy/dograh-reload.sh (renewal hook)"
echo ""
echo -e "${GREEN}Your SSL certificate will automatically renew before expiration.${NC}"
echo ""
