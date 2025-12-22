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

# Get the public IP address
echo -e "${YELLOW}Enter your server's public IP address:${NC}"
read -p "> " SERVER_IP

if [[ -z "$SERVER_IP" ]]; then
    echo -e "${RED}Error: IP address cannot be empty${NC}"
    exit 1
fi

# Validate IP address format (basic validation)
if ! [[ "$SERVER_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo -e "${RED}Error: Invalid IP address format${NC}"
    exit 1
fi

# Get the TURN password
echo -e "${YELLOW}Enter a password for the TURN server (press Enter for default 'dograh-turn-secret'):${NC}"
read -sp "> " TURN_PASSWORD
echo ""

if [[ -z "$TURN_PASSWORD" ]]; then
    TURN_PASSWORD="dograh-turn-secret"
    echo -e "${BLUE}Using default TURN password${NC}"
fi

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo -e "  Server IP:     ${BLUE}$SERVER_IP${NC}"
echo -e "  TURN Password: ${BLUE}********${NC}"
echo ""

# Create project directory if it doesn't exist
mkdir -p dograh 2>/dev/null || true
cd dograh

echo -e "${BLUE}[1/5] Downloading docker-compose.yaml...${NC}"
curl -sS -o docker-compose.yaml https://raw.githubusercontent.com/dograh-hq/dograh/main/docker-compose.yaml
echo -e "${GREEN}✓ docker-compose.yaml downloaded${NC}"

echo -e "${BLUE}[2/5] Creating nginx.conf...${NC}"
cat > nginx.conf << 'NGINX_EOF'
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

# Replace placeholder with actual IP
sed -i.bak "s/SERVER_IP_PLACEHOLDER/$SERVER_IP/g" nginx.conf && rm -f nginx.conf.bak
echo -e "${GREEN}✓ nginx.conf created${NC}"

echo -e "${BLUE}[3/5] Creating SSL certificate generation script...${NC}"
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

echo -e "${BLUE}[4/5] Generating SSL certificates...${NC}"
./generate_certificate.sh
echo -e "${GREEN}✓ SSL certificates generated${NC}"

echo -e "${BLUE}[5/5] Creating environment file...${NC}"
cat > .env << ENV_EOF
# TURN Server Configuration
TURN_HOST=$SERVER_IP
TURN_USERNAME=dograh
TURN_PASSWORD=$TURN_PASSWORD

# Telemetry (set to false to disable)
ENABLE_TELEMETRY=true
ENV_EOF
echo -e "${GREEN}✓ .env file created${NC}"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    Setup Complete!                           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Files created in ${BLUE}$(pwd)${NC}:"
echo "  - docker-compose.yaml"
echo "  - nginx.conf"
echo "  - generate_certificate.sh"
echo "  - certs/local.crt"
echo "  - certs/local.key"
echo "  - .env"
echo ""
echo -e "${YELLOW}To start Dograh, run:${NC}"
echo ""
echo -e "  ${BLUE}sudo docker compose --profile remote up --pull always${NC}"
echo ""
echo -e "${YELLOW}Your application will be available at:${NC}"
echo ""
echo -e "  ${BLUE}https://$SERVER_IP${NC}"
echo ""
echo -e "${YELLOW}Note:${NC} Your browser will show a security warning for the self-signed"
echo "certificate. You can safely accept it to proceed."
echo ""
