#!/usr/bin/env bash
#
# entrypoint.sh — render Asterisk config from templates, then exec Asterisk.
#
# Templates under $CONF_TEMPLATE_DIR are rendered with envsubst using an
# EXPLICIT variable allowlist, so operator-supplied values are substituted
# while Asterisk dialplan variables (${EXTEN}, ${CALLERID(num)}, ${CHANNEL(...)}
# etc.) are preserved verbatim. Required variables are validated up front and
# the container fails closed if any are missing.

set -euo pipefail

CONF_TEMPLATE_DIR="${CONF_TEMPLATE_DIR:-/etc/asterisk/templates}"
CONF_OUT_DIR="${CONF_OUT_DIR:-/etc/asterisk}"

log() { printf '[dograh-asterisk] %s\n' "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }

# --- Defaults for optional variables --------------------------------------
export VERIMOR_SIP_HOST="${VERIMOR_SIP_HOST:-sip.verimor.com.tr}"
export VERIMOR_SIP_PORT="${VERIMOR_SIP_PORT:-5060}"
export VERIMOR_CALLERID="${VERIMOR_CALLERID:-${VERIMOR_DID:-}}"
export ARI_APP_NAME="${ARI_APP_NAME:-dograh}"
export ARI_HTTP_PORT="${ARI_HTTP_PORT:-8088}"
export DOGRAH_WS_SCHEME="${DOGRAH_WS_SCHEME:-ws}"
export DOGRAH_API_HOST="${DOGRAH_API_HOST:-api}"
export DOGRAH_API_PORT="${DOGRAH_API_PORT:-8000}"
export WS_CLIENT_NAME="${WS_CLIENT_NAME:-dograh}"
export SIP_PORT="${SIP_PORT:-5060}"
export RTP_START="${RTP_START:-10000}"
export RTP_END="${RTP_END:-10100}"
export LOCAL_NET="${LOCAL_NET:-}"

# --- Required variables (fail closed) -------------------------------------
REQUIRED_VARS=(
  VERIMOR_SIP_USERNAME
  VERIMOR_SIP_PASSWORD
  VERIMOR_SIP_HOST
  VERIMOR_DID
  ARI_APP_NAME
  ARI_PASSWORD
  WS_CLIENT_NAME
  EXTERNAL_SIGNALING_ADDRESS
  EXTERNAL_MEDIA_ADDRESS
)
missing=()
for v in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!v:-}" ]; then
    missing+=("$v")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  die "missing required environment variable(s): ${missing[*]} — set them in deploy/asterisk/.env"
fi

# --- Explicit substitution allowlist --------------------------------------
# Only these variables are substituted. Anything else (Asterisk dialplan
# variables such as ${EXTEN}) is left untouched in the rendered output.
ALLOWLIST='${VERIMOR_SIP_USERNAME} ${VERIMOR_SIP_PASSWORD} ${VERIMOR_SIP_HOST} ${VERIMOR_SIP_PORT} ${VERIMOR_DID} ${VERIMOR_CALLERID} ${ARI_APP_NAME} ${ARI_PASSWORD} ${ARI_HTTP_PORT} ${DOGRAH_WS_SCHEME} ${DOGRAH_API_HOST} ${DOGRAH_API_PORT} ${WS_CLIENT_NAME} ${EXTERNAL_SIGNALING_ADDRESS} ${EXTERNAL_MEDIA_ADDRESS} ${LOCAL_NET} ${SIP_PORT} ${RTP_START} ${RTP_END}'

[ -d "$CONF_TEMPLATE_DIR" ] || die "template dir not found: $CONF_TEMPLATE_DIR"

shopt -s nullglob
rendered=0
for tpl in "$CONF_TEMPLATE_DIR"/*.template; do
  base="$(basename "$tpl" .template)"
  out="$CONF_OUT_DIR/$base"
  # Render into a temp file first so a partial write never yields a broken conf.
  tmp="$(mktemp "${out}.XXXXXX")"
  envsubst "$ALLOWLIST" < "$tpl" > "$tmp"
  mv "$tmp" "$out"
  chmod 0640 "$out" 2>/dev/null || true
  # The entrypoint runs as root before Asterisk drops to the asterisk user.
  # Chown rendered configs so Asterisk (running as asterisk) can read them.
  chown asterisk:asterisk "$out" 2>/dev/null || true
  # Strip empty directives that envsubst can't remove (e.g. "local_net = "
  # when LOCAL_NET is blank). An empty value directive can cause Asterisk to
  # fail config parsing.
  sed -i '/^[[:space:]]*[a-z_]\+[[:space:]]*=[[:space:]]*$/d' "$out"
  log "rendered $(basename "$out")"
  rendered=$((rendered + 1))
done
[ "$rendered" -gt 0 ] || die "no *.template files found in $CONF_TEMPLATE_DIR"

log "configuration rendered ($rendered file(s)); starting Asterisk"
exec "$@"
