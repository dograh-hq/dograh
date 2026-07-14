#!/usr/bin/env bash
set -e
###############################################################################
### End-to-end test: inbound SIP call → LiveKit → dograh-livekit → Dograh
###
### Prerequisites:
###   - LiveKit stack running (./scripts/start_livekit_dev.sh)
###   - Dograh API running (./scripts/start_services_dev.sh)
###   - A Dograh deploy exists (see below)
###
### This script:
###   1. Creates a SIP inbound trunk on LiveKit (if not exists)
###   2. Creates a dispatch rule routing calls → dograh-agent
###   3. Prints the SIP URI to dial
###
### Then use baresip to dial:
###   baresip sip:livekit@127.0.0.1:15060
###
### Or use LiveKit CLI:
###   lk sip participant create --trunk-id <id> --sip-number <uri>
###############################################################################

LIVEKIT_URL="${LIVEKIT_URL:-http://localhost:7880}"
LIVEKIT_API_KEY="${LIVEKIT_API_KEY:-devkey}"
LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET:-secret}"
SIP_HOST="${SIP_HOST:-127.0.0.1}"
SIP_PORT="${SIP_PORT:-15060}"

# Which Dograh deploy to test — MUST exist and have a published workflow
DEPLOY_ID="${1:-}"
ORG_ID="${2:-}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

if [[ -z "$DEPLOY_ID" || -z "$ORG_ID" ]]; then
  echo -e "${YELLOW}Usage: $0 <deploy_id> <org_id>${NC}"
  echo ""
  echo "  deploy_id  — Dograh deploy UUID (must have a published workflow)"
  echo "  org_id     — Dograh organization ID"
  echo ""
  echo "  Example: $0 dp_abc123 org_456"
  echo ""
  echo "  Find a deploy: curl -s http://localhost:8000/api/v1/workflows | jq '.[].deploy_id'"
  exit 1
fi

# ── Helper: call LiveKit API ────────────────────────────────────────────────

lk_api() {
  local method="$1" path="$2" body="${3:-}"
  curl -s -X "$method" \
    "${LIVEKIT_URL}${path}" \
    -H "Authorization: Bearer $(echo -n "${LIVEKIT_API_KEY}:${LIVEKIT_API_SECRET}" | base64)" \
    -H "Content-Type: application/json" \
    ${body:+-d "$body"}
}

echo -e "${CYAN}━━━ LiveKit SIP Inbound Test Setup ━━━${NC}"
echo ""

# ── 1) Check dograh-livekit .env ────────────────────────────────────────────

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"
if [[ ! -f "$BASE_DIR/dograh-livekit/.env" ]]; then
  echo -e "${YELLOW}Creating dograh-livekit/.env from .env.example...${NC}"
  cp "$BASE_DIR/dograh-livekit/.env.example" "$BASE_DIR/dograh-livekit/.env"
  echo "Edit dograh-livekit/.env and set your API keys, then re-run."
  echo ""
  echo "For local testing, set these values:"
  echo "  LIVEKIT_URL=ws://localhost:7880"
  echo "  LIVEKIT_API_KEY=devkey"
  echo "  LIVEKIT_API_SECRET=secret"
  echo "  DOGRAH_API_URL=http://localhost:8000"
  echo "  DOGRAH_INTERNAL_TOKEN=<your-token>"
  exit 1
fi

# ── 2) Check LiveKit is reachable ───────────────────────────────────────────

echo -n "Checking LiveKit... "
if curl -s -o /dev/null -w "%{http_code}" "$LIVEKIT_URL" | grep -q "200\|404"; then
  echo -e "${GREEN}OK${NC}"
else
  echo -e "${RED}FAIL${NC} — is LiveKit running? Run: docker compose -f ~/dev/livekit/docker-compose.yml ps"
  exit 1
fi

# ── 3) Check Dograh API is reachable ────────────────────────────────────────

DOGRAH_API="http://localhost:8000"
echo -n "Checking Dograh API... "
if curl -s -o /dev/null -w "%{http_code}" "$DOGRAH_API/api/v1/health" | grep -q "200"; then
  echo -e "${GREEN}OK${NC}"
else
  echo -e "${RED}FAIL${NC} — is Dograh running? Run: ./scripts/start_services_dev.sh"
  exit 1
fi

# ── 4) Verify deploy exists ─────────────────────────────────────────────────

echo -n "Verifying deploy $DEPLOY_ID... "
DEPLOY_CONFIG=$(curl -s "$DOGRAH_API/api/internal/deploy/$DEPLOY_ID/runtime-config" \
  -H "X-Internal-Token: ${DOGRAH_INTERNAL_TOKEN:-}" 2>/dev/null || echo "")
if echo "$DEPLOY_CONFIG" | grep -q "deploy_id"; then
  echo -e "${GREEN}OK${NC}"
else
  echo -e "${RED}FAIL${NC}"
  echo "  Deploy $DEPLOY_ID not found or internal API not configured."
  echo "  Make sure DOGRAH_INTERNAL_TOKEN is set in api/.env and dograh-livekit/.env"
  exit 1
fi

# ── 5) Create SIP inbound trunk ─────────────────────────────────────────────

echo ""
echo -e "${CYAN}Creating SIP inbound trunk...${NC}"
TRUNK_NAME="dograh-test-${DEPLOY_ID}"

TRUNK_RESPONSE=$(lk_api POST "/twirp/livekit.SIP/CreateSIPInboundTrunk" \
  "{\"name\":\"${TRUNK_NAME}\",\"auth_username\":\"livekit\",\"auth_password\":\"livekit-secret\"}")

echo "$TRUNK_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$TRUNK_RESPONSE"

SIP_TRUNK_ID=$(echo "$TRUNK_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sip_trunk_id',''))" 2>/dev/null)
if [[ -z "$SIP_TRUNK_ID" ]]; then
  echo -e "${RED}Failed to create trunk${NC}"
  exit 1
fi
echo -e "${GREEN}SIP trunk created: ${SIP_TRUNK_ID}${NC}"

# ── 6) Create dispatch rule ─────────────────────────────────────────────────

echo ""
echo -e "${CYAN}Creating dispatch rule...${NC}"

DISPATCH_RESPONSE=$(lk_api POST "/twirp/livekit.SIP/CreateSIPDispatchRule" \
  "{\"name\":\"dograh-${DEPLOY_ID}\",\"trunk_ids\":[\"${SIP_TRUNK_ID}\"],\"rule\":{\"dispatch_rule_individual\":{\"room_prefix\":\"dograh-call-\"}},\"metadata\":\"{\\\"deploy_id\\\":\\\"${DEPLOY_ID}\\\",\\\"org_id\\\":\\\"${ORG_ID}\\\",\\\"channel\\\":\\\"voice_sip\\\"}\",\"room_config\":{\"agents\":[{\"agent_name\":\"dograh-agent\"}]}}")

echo "$DISPATCH_RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$DISPATCH_RESPONSE"

DISPATCH_RULE_ID=$(echo "$DISPATCH_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('sip_dispatch_rule_id',''))" 2>/dev/null)
if [[ -z "$DISPATCH_RULE_ID" ]]; then
  echo -e "${YELLOW}Failed to create dispatch rule (may already exist)${NC}"
else
  echo -e "${GREEN}Dispatch rule created: ${DISPATCH_RULE_ID}${NC}"
fi

# ── 7) Print dial instructions ──────────────────────────────────────────────

echo ""
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Setup complete!${NC}"
echo ""
echo -e "Now dial with baresip:"
echo -e "  ${CYAN}baresip${NC}"
echo -e "  ${CYAN}/dial sip:livekit@${SIP_HOST}:${SIP_PORT}${NC}"
echo ""
echo -e "The call will route to deploy ${YELLOW}${DEPLOY_ID}${NC} (org ${YELLOW}${ORG_ID}${NC})"
echo ""
echo -e "Monitor logs:"
echo -e "  ${CYAN}tail -f logs/latest/dograh-livekit*.log${NC}"
echo -e "  ${CYAN}docker compose -f ~/dev/livekit/docker-compose.yml logs -f sip${NC}"
echo ""
echo -e "Cleanup (after testing):"
echo -e "  ${RED}curl -X DELETE ${LIVEKIT_URL}/twirp/livekit.SIP/DeleteSIPDispatchRule -d '{\"sip_dispatch_rule_id\":\"${DISPATCH_RULE_ID}\"}'${NC}"
echo -e "  ${RED}curl -X DELETE ${LIVEKIT_URL}/twirp/livekit.SIP/DeleteSIPInboundTrunk -d '{\"sip_trunk_id\":\"${SIP_TRUNK_ID}\"}'${NC}"
