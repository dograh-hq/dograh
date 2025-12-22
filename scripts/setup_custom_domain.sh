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
echo "║              Dograh Custom Domain Setup                      ║"
echo "║     Automated Let's Encrypt SSL certificate setup            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check if running as root or with sudo
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Error: This script must be run as root or with sudo${NC}"
    exit 1
fi

# Check if dograh directory exists
if [[ ! -d "dograh" ]]; then
    echo -e "${RED}Error: 'dograh' directory not found.${NC}"
    echo -e "${YELLOW}Please run this script from the directory containing your Dograh installation.${NC}"
    echo -e "${YELLOW}If you haven't set up Dograh yet, run the remote setup first:${NC}"
    echo -e "${BLUE}  curl -o setup_remote.sh https://raw.githubusercontent.com/dograh-hq/dograh/main/scripts/setup_remote.sh && chmod +x setup_remote.sh && ./setup_remote.sh${NC}"
    exit 1
fi

# Get the domain name
echo -e "${YELLOW}Enter your domain name (e.g., voice.yourcompany.com):${NC}"
read -p "> " DOMAIN_NAME

if [[ -z "$DOMAIN_NAME" ]]; then
    echo -e "${RED}Error: Domain name cannot be empty${NC}"
    exit 1
fi

# Basic domain validation
if ! [[ "$DOMAIN_NAME" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$ ]]; then
    echo -e "${RED}Error: Invalid domain name format${NC}"
    exit 1
fi

# Get email for Let's Encrypt notifications
echo -e "${YELLOW}Enter your email address for SSL certificate notifications:${NC}"
read -p "> " EMAIL_ADDRESS

if [[ -z "$EMAIL_ADDRESS" ]]; then
    echo -e "${RED}Error: Email address cannot be empty (required by Let's Encrypt)${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Domain:  ${BLUE}$DOMAIN_NAME${NC}"
echo -e "  Email:   ${BLUE}$EMAIL_ADDRESS${NC}"
echo ""

# Verify DNS is pointing to this server
echo -e "${BLUE}[1/7] Verifying DNS configuration...${NC}"
SERVER_IP=$(curl -s ifconfig.me || curl -s icanhazip.com || echo "")
RESOLVED_IP=$(dig +short "$DOMAIN_NAME" | tail -1)

if [[ -z "$SERVER_IP" ]]; then
    echo -e "${YELLOW}Warning: Could not detect server's public IP${NC}"
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

# Detect package manager and install certbot
echo -e "${BLUE}[2/7] Installing Certbot...${NC}"
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq certbot
elif command -v yum &> /dev/null; then
    yum install -y -q certbot
elif command -v dnf &> /dev/null; then
    dnf install -y -q certbot
else
    echo -e "${RED}Error: Could not detect package manager. Please install certbot manually.${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Certbot installed${NC}"

# Stop Dograh services to free port 80
echo -e "${BLUE}[3/7] Stopping Dograh services...${NC}"
cd dograh
if docker compose --profile remote ps --quiet 2>/dev/null | grep -q .; then
    docker compose --profile remote down
    echo -e "${GREEN}✓ Dograh services stopped${NC}"
else
    echo -e "${YELLOW}⚠ No running services found${NC}"
fi

# Generate SSL certificate
echo -e "${BLUE}[4/7] Generating Let's Encrypt SSL certificate...${NC}"
CERTBOT_OUTPUT=$(certbot certonly --standalone \
    --non-interactive \
    --agree-tos \
    --email "$EMAIL_ADDRESS" \
    -d "$DOMAIN_NAME" 2>&1) || {
    echo -e "${RED}✗ Certificate generation failed${NC}"
    echo ""

    # Check for common errors and provide helpful hints
    if echo "$CERTBOT_OUTPUT" | grep -qi "timeout\|firewall\|connection"; then
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  Port 80 appears to be blocked by a firewall.${NC}"
        echo -e "${YELLOW}═══════════════════════════════════════════════════════════════${NC}"
        echo ""
        echo -e "Let's Encrypt needs to connect to port 80 to verify domain ownership."
        echo ""
        echo -e "${BLUE}If using AWS EC2:${NC}"
        echo "  1. Go to AWS Console → EC2 → Security Groups"
        echo "  2. Find the security group for your instance"
        echo "  3. Add inbound rule: HTTP | TCP | Port 80 | 0.0.0.0/0"
        echo ""
        echo -e "${BLUE}If using another cloud provider:${NC}"
        echo "  • Ensure port 80 (TCP) is open for inbound traffic from all sources"
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

# Verify and display certificate location
CERT_PATH="/etc/letsencrypt/live/$DOMAIN_NAME"
echo ""
echo -e "${BLUE}Certificate location:${NC}"
echo -e "  ${CERT_PATH}/"
if [[ -f "$CERT_PATH/fullchain.pem" ]]; then
    echo -e "  ${GREEN}✓${NC} fullchain.pem exists"
else
    echo -e "  ${RED}✗${NC} fullchain.pem NOT FOUND"
fi
if [[ -f "$CERT_PATH/privkey.pem" ]]; then
    echo -e "  ${GREEN}✓${NC} privkey.pem exists"
else
    echo -e "  ${RED}✗${NC} privkey.pem NOT FOUND"
fi
echo ""

# Copy certificates to dograh/certs directory
cp /etc/letsencrypt/archive/$DOMAIN_NAME/fullchain1.pem certs/local.crt
cp /etc/letsencrypt/archive/$DOMAIN_NAME/privkey1.pem certs/local.key
chmod 644 certs/local.crt certs/local.key
echo -e "${GREEN}✓${NC} Certificates copied to certs/ directory"
echo ""

# Update nginx.conf
echo -e "${BLUE}[5/7] Updating nginx configuration...${NC}"
cat > nginx.conf << NGINX_EOF
server {
    listen 80;
    server_name $DOMAIN_NAME;

    # Redirect all HTTP to HTTPS
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $DOMAIN_NAME;

    ssl_certificate     /etc/nginx/certs/local.crt;
    ssl_certificate_key /etc/nginx/certs/local.key;

    # TLS settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;

    location / {
        proxy_pass         http://ui:3010;
        proxy_http_version 1.1;

        # Important for WebSockets / hot reload etc.
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Rewrite localhost MinIO URLs in API responses to use current domain
        sub_filter 'http://localhost:9000/voice-audio/' 'https://\$host/voice-audio/';
        sub_filter_once off;
        sub_filter_types application/json text/html;
    }

    location /voice-audio/ {
        proxy_pass http://minio:9000/voice-audio/;

        proxy_http_version 1.1;

        # Headers for file downloads from MinIO
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # Allow large file downloads
        proxy_buffering off;
        client_max_body_size 100M;
    }
}
NGINX_EOF
echo -e "${GREEN}✓ nginx.conf updated${NC}"

# Setup auto-renewal
echo -e "${BLUE}[6/7] Setting up automatic certificate renewal...${NC}"
DOGRAH_PATH=$(pwd)

# Create renewal hook script that copies new certificates and restarts nginx
cat > /etc/letsencrypt/renewal-hooks/deploy/dograh-reload.sh << HOOK_EOF
#!/bin/bash
# Copy renewed certificates to dograh certs directory
cp /etc/letsencrypt/archive/$DOMAIN_NAME/fullchain1.pem $DOGRAH_PATH/certs/local.crt
cp /etc/letsencrypt/archive/$DOMAIN_NAME/privkey1.pem $DOGRAH_PATH/certs/local.key
chmod 644 $DOGRAH_PATH/certs/local.crt $DOGRAH_PATH/certs/local.key

# Restart nginx to load new certificates
cd $DOGRAH_PATH
docker compose --profile remote restart nginx 2>/dev/null || true
HOOK_EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/dograh-reload.sh

# Test renewal
certbot renew --dry-run --quiet && echo -e "${GREEN}✓ Auto-renewal configured and tested${NC}" || echo -e "${YELLOW}⚠ Auto-renewal test had issues, but certificates are installed${NC}"

# Start Dograh services
echo ""
echo -e "${BLUE}[7/7] Starting Dograh services...${NC}"
docker compose --profile remote up -d --pull always

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
echo "  - dograh/nginx.conf (updated with domain name)"
echo "  - dograh/certs/local.crt (SSL certificate)"
echo "  - dograh/certs/local.key (SSL private key)"
echo "  - /etc/letsencrypt/renewal-hooks/deploy/dograh-reload.sh (renewal hook)"
echo ""
echo -e "${GREEN}Your SSL certificate will automatically renew before expiration.${NC}"
echo ""
