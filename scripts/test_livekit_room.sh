#!/usr/bin/env bash
set -e
###############################################################################
### Quick test: create a LiveKit room → dograh-agent picks it up
###
### This bypasses SIP — creates a room directly via LiveKit API with
### the dograh-agent dispatch. The dograh-livekit worker will join
### and start the Agno workflow.
###
### Prerequisites:
###   - LiveKit stack + dograh-livekit worker running
###   - Dograh API running
###   - A deploy with a published workflow
###############################################################################

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"

LIVEKIT_URL="${LIVEKIT_URL:-http://localhost:7880}"
LIVEKIT_API_KEY="${LIVEKIT_API_KEY:-devkey}"
LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET:-secret}"
LIVEKIT_WS="${LIVEKIT_WS:-ws://localhost:7880}"

DEPLOY_ID="${1:-}"
ORG_ID="${2:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

usage() {
  echo -e "${YELLOW}Usage: $0 <deploy_id> <org_id>${NC}"
  echo ""
  echo "  deploy_id  — Dograh deploy UUID"
  echo "  org_id     — Dograh organization ID"
  echo ""
  echo "  Example: $0 dp_abc123 org_456"
  exit 1
}

[[ -z "$DEPLOY_ID" || -z "$ORG_ID" ]] && usage

# ── Load env ────────────────────────────────────────────────────────────────

if [[ -f "$BASE_DIR/dograh-livekit/.env" ]]; then
  set -a && . "$BASE_DIR/dograh-livekit/.env" && set +a
fi
if [[ -f "$BASE_DIR/api/.env" ]]; then
  set -a && . "$BASE_DIR/api/.env" && set +a
fi

DOGRAH_TOKEN="${DOGRAH_INTERNAL_TOKEN:-test-token}"

lk_auth() {
  echo -n "${LIVEKIT_API_KEY}:${LIVEKIT_API_SECRET}" | base64
}

# ── 1) Verify deploy ────────────────────────────────────────────────────────

echo -e "${CYAN}━━━ Quick Test: LiveKit Room → dograh-agent ━━━${NC}"
echo ""

DOGRAH_API="${DOGRAH_API_URL:-http://localhost:8000}"
echo -n "Verifying deploy $DEPLOY_ID... "
RESP=$(curl -s "$DOGRAH_API/api/internal/deploy/$DEPLOY_ID/runtime-config" \
  -H "X-Internal-Token: $DOGRAH_TOKEN" 2>/dev/null || echo "{}")
if echo "$RESP" | grep -q '"deploy_id"'; then
  AGENT_NAME=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent_name','unknown'))" 2>/dev/null)
  echo -e "${GREEN}OK ($AGENT_NAME)${NC}"
else
  echo -e "${RED}FAIL${NC} — check deploy_id and DOGRAH_INTERNAL_TOKEN"
  exit 1
fi

# ── 2) Create room with agent dispatch ──────────────────────────────────────

ROOM_NAME="dograh-test-$(date +%s)"
echo ""
echo -n "Creating room $ROOM_NAME... "

ROOM_RESP=$(curl -s -X POST "$LIVEKIT_URL/twirp/livekit.RoomService/CreateRoom" \
  -H "Authorization: Bearer $(lk_auth)" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"${ROOM_NAME}\",
    \"metadata\": \"{\\\"deploy_id\\\":\\\"${DEPLOY_ID}\\\",\\\"org_id\\\":\\\"${ORG_ID}\\\",\\\"channel\\\":\\\"voice_sip\\\"}\",
    \"agent_dispatch\": [{\"agent_name\": \"dograh-agent\"}]
  }")

if echo "$ROOM_RESP" | grep -q '"sid"'; then
  echo -e "${GREEN}OK${NC}"
else
  echo -e "${RED}FAIL${NC}"
  echo "$ROOM_RESP"
  exit 1
fi

# ── 3) Generate access token for a test participant ─────────────────────────

echo -n "Generating test participant token... "

TOKEN_RESP=$(curl -s -X POST "$LIVEKIT_URL/twirp/livekit.TokenService/CreateToken" \
  -H "Authorization: Bearer $(lk_auth)" \
  -H "Content-Type: application/json" \
  -d "{
    \"identity\": \"test-user\",
    \"name\": \"Test Caller\",
    \"room\": \"${ROOM_NAME}\",
    \"grant\": {
      \"room_join\": true,
      \"can_publish\": true,
      \"can_subscribe\": true,
      \"room_record\": false
    }
  }")

TOKEN=$(echo "$TOKEN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['jwt'])" 2>/dev/null)
if [[ -n "$TOKEN" ]]; then
  echo -e "${GREEN}OK${NC}"
else
  echo -e "${RED}FAIL${NC}"
  echo "$TOKEN_RESP"
  exit 1
fi

# ── 4) Print connection info ────────────────────────────────────────────────

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Room created!${NC}"
echo ""
echo -e "Room:        ${YELLOW}${ROOM_NAME}${NC}"
echo -e "Deploy:      ${YELLOW}${DEPLOY_ID}${NC}"
echo -e "Agent:       ${YELLOW}${AGENT_NAME:-unknown}${NC}"
echo ""
echo -e "The ${CYAN}dograh-livekit${NC} worker should connect to this room automatically."
echo ""
echo -e "To simulate a user joining as a participant (optional):"
echo ""
echo -e "  ${CYAN}# Using LiveKit CLI:${NC}"
echo -e "  export LIVEKIT_URL=${LIVEKIT_WS}"
echo -e "  export LIVEKIT_API_KEY=${LIVEKIT_API_KEY}"
echo -e "  export LIVEKIT_API_SECRET=${LIVEKIT_API_SECRET}"
echo -e "  lk room join ${ROOM_NAME} --identity test-user"
echo ""
echo -e "  ${CYAN}# Or copy this into a browser with the LiveKit demo app:${NC}"
echo -e "  Token: ${YELLOW}${TOKEN:0:50}...${NC}"
echo ""
echo -e "Monitor logs:"
echo -e "  ${CYAN}tail -f /tmp/dograh-livekit*.log${NC}"
echo -e "  ${CYAN}journalctl -u dograh-livekit -f${NC}"
echo -e "  (or check stdout of the worker process)"
echo ""
echo -e "Cleanup room:"
echo -e "  ${RED}curl -X POST ${LIVEKIT_URL}/twirp/livekit.RoomService/DeleteRoom \\"
echo -e "    -H 'Authorization: Bearer $(lk_auth)' \\"
echo -e "    -H 'Content-Type: application/json' \\"
echo -e "    -d '{\"room\":\"${ROOM_NAME}\"}'${NC}"
