#!/usr/bin/env bash
set -euo pipefail

export VERIMOR_SIP_USERNAME=testuser
export VERIMOR_SIP_PASSWORD=testpass
export VERIMOR_SIP_HOST=sip.verimor.com.tr
export VERIMOR_SIP_PORT=5060
export VERIMOR_DID=908500000000
export VERIMOR_CALLERID=908500000000
export ARI_APP_NAME=dograh
export ARI_PASSWORD=testpass123
export ARI_HTTP_PORT=8088
export DOGRAH_WS_SCHEME=ws
export DOGRAH_API_HOST=api
export DOGRAH_API_PORT=8000
export WS_CLIENT_NAME=dograh
export EXTERNAL_SIGNALING_ADDRESS=203.0.113.10
export EXTERNAL_MEDIA_ADDRESS=203.0.113.10
export LOCAL_NET=""
export SIP_PORT=5060
export RTP_START=10000
export RTP_END=10100
export DOGRAH_NETWORK_NAME=dograh_app-network
export ASTERISK_VERSION=22

echo "=== Docker Compose Config Validation ==="
docker compose -f /home/ubuntu/workspace/dograh/deploy/asterisk/docker-compose.asterisk.yaml config -q 2>&1
echo "compose config OK (exit 0)"

echo ""
echo "=== Template Rendering Test (envsubst allowlist) ==="
# Verify envsubst allowlist works with the entrypoint allowlist
CONF_TEMPLATE_DIR="/home/ubuntu/workspace/dograh/deploy/asterisk/conf"
CONF_OUT_DIR="/tmp/asterisk_test_render"
mkdir -p "$CONF_OUT_DIR"

ALLOWLIST='${VERIMOR_SIP_USERNAME} ${VERIMOR_SIP_PASSWORD} ${VERIMOR_SIP_HOST} ${VERIMOR_SIP_PORT} ${VERIMOR_DID} ${VERIMOR_CALLERID} ${ARI_APP_NAME} ${ARI_PASSWORD} ${ARI_HTTP_PORT} ${DOGRAH_WS_SCHEME} ${DOGRAH_API_HOST} ${DOGRAH_API_PORT} ${WS_CLIENT_NAME} ${EXTERNAL_SIGNALING_ADDRESS} ${EXTERNAL_MEDIA_ADDRESS} ${LOCAL_NET} ${SIP_PORT} ${RTP_START} ${RTP_END}'

shopt -s nullglob
for tpl in "$CONF_TEMPLATE_DIR"/*.template; do
  base="$(basename "$tpl" .template)"
  out="$CONF_OUT_DIR/$base"
  envsubst "$ALLOWLIST" < "$tpl" > "$out"
  echo "--- rendered: $base ---"
  # Do not print rendered credentials or config values. The validation only
  # needs to prove that the file was produced; later checks inspect syntax and
  # placeholder preservation without echoing secret-like fields.
  test -s "$out"
  echo "rendered: $(basename "$out")"
  echo ""
done

echo "=== Verify no unresolved operator vars remain ==="
for f in "$CONF_OUT_DIR"/*.conf; do
  # Asterisk dialplan variables (${EXTEN}, ${CALLERID(num)}, ${DEST}, ...)
  # are intentionally preserved. Only deployment/operator variables must be
  # fully rendered by the allowlist above.
  if grep -Eq '\$\{(VERIMOR_|ARI_|DOGRAH_|WS_CLIENT_NAME|EXTERNAL_|LOCAL_NET|SIP_PORT|RTP_)' "$f"; then
    echo "ERROR: unresolved operator variables in $(basename "$f")"
    grep -n -E '\$\{(VERIMOR_|ARI_|DOGRAH_|WS_CLIENT_NAME|EXTERNAL_|LOCAL_NET|SIP_PORT|RTP_)' "$f"
    exit 1
  fi
done
echo "template render check complete (dialplan variables intentionally preserved)"
