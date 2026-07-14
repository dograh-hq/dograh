#!/usr/bin/env bash
set -e
###############################################################################
### Quick test: LiveKit room → dograh-agent picks it up
###
### Creates a room with workflow_id in metadata — dograh-livekit worker
### loads config from Dograh API and runs the Agno workflow.
###############################################################################

BASE_DIR="$(cd "$(dirname "$(dirname "${BASH_SOURCE[0]}")")" && pwd)"

LIVEKIT_URL="${LIVEKIT_URL:-http://localhost:7880}"
LIVEKIT_API_KEY="${LIVEKIT_API_KEY:-devkey}"
LIVEKIT_API_SECRET="${LIVEKIT_API_SECRET:-secret}"

WORKFLOW_ID="${1:-}"
ORG_ID="${2:-}"

usage() {
  echo "Usage: $0 <workflow_id> <org_id>"
  echo ""
  echo "  workflow_id — Dograh workflow ID (must have published version)"
  echo "  org_id      — Dograh organization ID"
  echo ""
  echo "  Example: $0 42 7"
  exit 1
}

[[ -z "$WORKFLOW_ID" || -z "$ORG_ID" ]] && usage

if [[ -f "$BASE_DIR/dograh-livekit/.env" ]]; then
  set -a && . "$BASE_DIR/dograh-livekit/.env" && set +a
fi

DOGRAH_TOKEN="${DOGRAH_INTERNAL_TOKEN:-test-token}"
DOGRAH_API="${DOGRAH_API_URL:-http://localhost:8000}"
lk_auth() { echo -n "${LIVEKIT_API_KEY}:${LIVEKIT_API_SECRET}" | base64; }

echo "━━━ Test: LiveKit Room → dograh-agent ━━━"
echo ""

# Verify workflow exists
echo -n "Verifying workflow $WORKFLOW_ID... "
RESP=$(curl -s "$DOGRAH_API/api/internal/workflows/$WORKFLOW_ID/runtime-config" \
  -H "X-Internal-Token: $DOGRAH_TOKEN" 2>/dev/null || echo "{}")
if echo "$RESP" | grep -q '"workflow_id"'; then
  AGENT_NAME=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('agent_name','unknown'))" 2>/dev/null)
  echo "OK ($AGENT_NAME)"
else
  echo "FAIL — check workflow_id and DOGRAH_INTERNAL_TOKEN"
  exit 1
fi

# Create room
ROOM_NAME="dograh-test-$(date +%s)"
echo -n "Creating room $ROOM_NAME... "
ROOM_RESP=$(curl -s -X POST "$LIVEKIT_URL/twirp/livekit.RoomService/CreateRoom" \
  -H "Authorization: Bearer $(lk_auth)" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"${ROOM_NAME}\",\"metadata\":\"{\\\"workflow_id\\\":${WORKFLOW_ID},\\\"org_id\\\":${ORG_ID},\\\"channel\\\":\\\"voice_sip\\\"}\",\"agent_dispatch\":[{\"agent_name\":\"dograh-agent\"}]}")
echo "$ROOM_RESP" | grep -q '"sid"' && echo "OK" || { echo "FAIL"; echo "$ROOM_RESP"; exit 1; }

echo ""
echo "✓ Room created: $ROOM_NAME"
echo "  Workflow: $WORKFLOW_ID ($AGENT_NAME)"
echo "  The dograh-livekit worker connects automatically."
echo ""
echo "Monitor: docker compose -f ~/dev/livekit/docker-compose.yml logs -f"
