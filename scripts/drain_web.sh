#!/bin/sh
# preStop drain hook for the web (uvicorn) pod.
#
# Long-lived call WebSockets keep state in-process; SIGTERM makes uvicorn
# force-close them (code 1012), cutting live calls. On termination the pod is
# already removed from the Service endpoints, so NO new calls arrive here — this
# hook just holds off SIGTERM until the calls already on this pod finish.
#
# This is what makes KEDA/HPA scale-down and rolling updates call-safe: without
# it, scaling down deletes a pod mid-call. Kubernetes caps the wait at
# terminationGracePeriodSeconds, so keep DRAIN_MAX_WAIT below it.
#
# Polls the local /health/active-calls (same endpoint + secret as
# scripts/rolling_update.sh) via python — curl isn't in the slim image.
set -eu

PORT="${WEB_PORT:-8000}"
INITIAL_DELAY="${DRAIN_INITIAL_DELAY:-15}"  # let the gateway observe endpoint removal
INTERVAL="${DRAIN_INTERVAL:-5}"
MAX_WAIT="${DRAIN_MAX_WAIT:-570}"           # MUST stay < terminationGracePeriodSeconds

# Give the load balancer / gateway time to stop routing new connections here.
sleep "$INITIAL_DELAY"

# Without the devops secret we cannot read the count — fall back to the old
# fixed-sleep behavior rather than block the full grace window on every stop.
if [ -z "${DOGRAH_DEVOPS_SECRET:-}" ]; then
  echo "drain: DOGRAH_DEVOPS_SECRET unset — skipping active-call drain (sleep-only)"
  exit 0
fi

deadline=$(( $(date +%s) + MAX_WAIT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  count=$(WEB_PORT="$PORT" python - <<'PY' || echo ERR
import json, os, urllib.request
port = os.environ.get("WEB_PORT", "8000")
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/api/v1/health/active-calls",
    headers={"X-Dograh-Devops-Secret": os.environ.get("DOGRAH_DEVOPS_SECRET", "")},
)
with urllib.request.urlopen(req, timeout=3) as r:
    print(json.load(r)["active_calls"])
PY
)
  if [ "$count" = "0" ]; then
    echo "drain: no active calls — releasing SIGTERM"
    exit 0
  fi
  # ERR (endpoint/auth error) is treated like "still busy": wait it out rather
  # than mistake an error for a drained pod and cut calls.
  echo "drain: active_calls=${count} — waiting ${INTERVAL}s"
  sleep "$INTERVAL"
done

echo "drain: DRAIN_MAX_WAIT (${MAX_WAIT}s) reached — proceeding to SIGTERM"
exit 0
