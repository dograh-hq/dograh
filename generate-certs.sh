#!/bin/bash
mkdir -p ssl

# Create a config file for the certificate with SAN (Subject Alternative Names)
cat > ssl/cert.conf <<EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
C = US
ST = State
L = City
O = Organization
CN = localhost

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
DNS.2 = *.localhost
IP.1 = 127.0.0.1
IP.2 = 0.0.0.0
EOF

# Generate the certificate with SAN
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout ssl/key.pem \
    -out ssl/cert.pem \
    -config ssl/cert.conf \
    -extensions v3_req

echo "SSL certificates generated in ssl/ directory"
echo "You can now access your app at https://your-ip:3010"