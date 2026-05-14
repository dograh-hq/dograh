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
    echo -e "${BLUE}  curl -o setup_remote.sh https://raw.githubusercontent.com/dograh-hq/dograh/main/scripts/setup_remote.sh && chmod +x setup_remote.sh && ./setup_remote.sh${NC}"
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

echo -e "${BLUE}[1/7] Verifying DNS configuration...${NC}"
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

echo -e "${BLUE}[2/7] Installing Certbot...${NC}"
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq certbot
elif command -v yum &> /dev/null; then
    yum install -y -q certbot
elif command -v dnf &> /dev/null; then
    dnf install -y -q certbot
else
    dograh_fail "Could not detect package manager. Please install certbot manually."
fi
echo -e "${GREEN}✓ Certbot installed${NC}"

echo -e "${BLUE}[3/7] Stopping Dograh services...${NC}"
cd dograh
DOGRAH_DEPLOY_PROJECT_DIR="$(pwd)"

if [[ ! -f remote_up.sh || ! -f scripts/lib/setup_common.sh ]]; then
    dograh_download_remote_support_bundle "$(pwd)" "main"
fi

dograh_require_init_compose_layout "$(pwd)"

if docker compose --profile remote ps --quiet 2>/dev/null | grep -q .; then
    docker compose --profile remote down
    echo -e "${GREEN}✓ Dograh services stopped${NC}"
else
    echo -e "${YELLOW}⚠ No running services found${NC}"
fi

echo -e "${BLUE}[4/7] Generating Let's Encrypt SSL certificate...${NC}"
CERTBOT_OUTPUT=$(certbot certonly --standalone \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL_ADDRESS" \
    -d "$DOMAIN_NAME" 2>&1) || {
    echo -e "${RED}✗ Certificate generation failed${NC}"
    echo ""

    if echo "$CERTBOT_OUTPUT" | grep -qi "timeout\|firewall\|connection"; then
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  Port 80 appears to be blocked by a firewall.${NC}"
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo ""
        echo -e "Let's Encrypt needs to connect to port 80 to verify domain ownership."
        echo ""
    elif echo "$CERTBOT_OUTPUT" | grep -qi "too many\|rate.limit"; then
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  Let's Encrypt rate limit reached.${NC}"
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo ""
        echo "You've requested too many certificates recently."
        echo "Please wait before trying again (usually 1 hour)."
        echo ""
    elif echo "$CERTBOT_OUTPUT" | grep -qi "dns\|resolve\|NXDOMAIN"; then
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  DNS resolution failed.${NC}"
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo ""
        echo "The domain '$DOMAIN_NAME' does not resolve to this server."
        echo "Please verify your DNS A record is correctly configured."
        echo ""
    else
        echo -e "${YELLOW}Certbot output:${NC}"
        echo "$CERTBOT_OUTPUT"
        echo ""
    fi

    echo -e "After fixing the issue, re-run this script:"
    echo -e "  ${BLUE}sudo ./setup_custom_domain.sh${NC}"
    echo ""
    exit 1
}
echo -e "${GREEN}✓ SSL certificate generated${NC}"

CERT_PATH="/etc/letsencrypt/live/$DOMAIN_NAME"
echo ""
echo -e "${BLUE}Certificate location:${NC}"
echo -e "  ${CERT_PATH}/"
[[ -f "$CERT_PATH/fullchain.pem" ]] && echo -e "  ${GREEN}✓${NC} fullchain.pem exists" || echo -e "  ${RED}✗${NC} fullchain.pem NOT FOUND"
[[ -f "$CERT_PATH/privkey.pem" ]] && echo -e "  ${GREEN}✓${NC} privkey.pem exists" || echo -e "  ${RED}✗${NC} privkey.pem NOT FOUND"
echo ""

mkdir -p certs
cp "$CERT_PATH/fullchain.pem" certs/local.crt
cp "$CERT_PATH/privkey.pem" certs/local.key
chmod 644 certs/local.crt certs/local.key
echo -e "${GREEN}✓${NC} Certificates copied to certs/ directory"
echo ""

echo -e "${BLUE}[5/7] Updating canonical remote settings and validating init-based config...${NC}"
dograh_load_env_file .env

if [[ -z "${SERVER_IP:-}" ]]; then
    SERVER_IP="$(dograh_infer_server_ip "$(pwd)" || true)"
fi

[[ -n "${SERVER_IP:-}" ]] || dograh_fail "Could not determine SERVER_IP from the existing install"

dograh_set_env_key .env SERVER_IP "$SERVER_IP"
dograh_set_env_key .env PUBLIC_HOST "$DOMAIN_NAME"
dograh_set_env_key .env PUBLIC_BASE_URL "https://$DOMAIN_NAME"
dograh_delete_env_key .env BACKEND_URL
dograh_prepare_remote_install "$(pwd)"
echo -e "${GREEN}✓ .env synchronized and init-based config validated${NC}"

echo -e "${BLUE}[6/7] Setting up automatic certificate renewal...${NC}"
DOGRAH_PATH="$(pwd)"

cat > /etc/letsencrypt/renewal-hooks/deploy/dograh-reload.sh << HOOK_EOF
#!/bin/bash
cp /etc/letsencrypt/live/$DOMAIN_NAME/fullchain.pem $DOGRAH_PATH/certs/local.crt
cp /etc/letsencrypt/live/$DOMAIN_NAME/privkey.pem $DOGRAH_PATH/certs/local.key
chmod 644 $DOGRAH_PATH/certs/local.crt $DOGRAH_PATH/certs/local.key

cd $DOGRAH_PATH
docker compose --profile remote restart nginx 2>/dev/null || true
HOOK_EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/dograh-reload.sh

if certbot renew --dry-run --quiet; then
    echo -e "${GREEN}✓ Auto-renewal configured and tested${NC}"
else
    echo -e "${YELLOW}⚠ Auto-renewal test had issues, but certificates are installed${NC}"
fi

echo ""
echo -e "${BLUE}[7/7] Starting Dograh services through validated startup wrapper...${NC}"
./remote_up.sh

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
