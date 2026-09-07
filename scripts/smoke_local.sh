#!/usr/bin/env bash
set -euo pipefail

# Authenticated local API smoke test. It deliberately uses credentials from
# the environment and never prints tokens or passwords. This script is only
# an API/auth prerequisite check: it must never be reported as proof that
# browser Simulation audio works. The browser acceptance gate is documented in
# SAKINAH.md and requires a normal user gesture, audible speech from both
# agents, and at least five alternating turns.

BASE_URL="${BASE_URL:-http://localhost:8000}"
SMOKESCREEN_EMAIL="${SMOKESCREEN_EMAIL:-smokescreen.local@example.test}"
TESTUSER_EMAIL="${TESTUSER_EMAIL:-testuser.local@example.test}"

if [[ -z "${SMOKE_TEST_USER_PASSWORD:-}" || -z "${SMOKE_TEST_SECOND_USER_PASSWORD:-}" ]]; then
    echo "Set SMOKE_TEST_USER_PASSWORD and SMOKE_TEST_SECOND_USER_PASSWORD in your shell or password manager." >&2
    exit 2
fi

BASE_URL="$BASE_URL" \
SMOKESCREEN_EMAIL="$SMOKESCREEN_EMAIL" \
TESTUSER_EMAIL="$TESTUSER_EMAIL" \
SMOKESCREEN_PASSWORD="${SMOKE_TEST_USER_PASSWORD}" \
TESTUSER_PASSWORD="${SMOKE_TEST_SECOND_USER_PASSWORD}" \
python3 - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request


base_url = os.environ["BASE_URL"].rstrip("/")


def call(path, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(f"{base_url}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except (urllib.error.HTTPError, json.JSONDecodeError, urllib.error.URLError, OSError) as error:
        status = getattr(error, "code", 0)
        return status, None


def login(email, password):
    request = urllib.request.Request(
        f"{base_url}/api/v1/auth/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read())
            return response.status, body.get("token")
    except (urllib.error.HTTPError, json.JSONDecodeError, urllib.error.URLError, OSError) as error:
        return getattr(error, "code", 0), None


checks = (
    ("auth/me", "/api/v1/auth/me"),
    ("workflows", "/api/v1/workflow/fetch?status=active,archived"),
    ("folders", "/api/v1/folder"),
    ("scenario library", "/api/v1/sakinah/scenarios"),
    ("agent runs", "/api/v1/sakinah/runs"),
)

for label, email, password in (
    ("smokescreen", os.environ["SMOKESCREEN_EMAIL"], os.environ["SMOKESCREEN_PASSWORD"]),
    ("testuser", os.environ["TESTUSER_EMAIL"], os.environ["TESTUSER_PASSWORD"]),
):
    status, token = login(email, password)
    if status != 200 or not token:
        print(f"{label}: login FAIL (HTTP {status})")
        sys.exit(1)
    print(f"{label}: login PASS")
    for check, path in checks:
        status, _ = call(path, token)
        if status != 200:
            print(f"{label}: {check} FAIL (HTTP {status})")
            sys.exit(1)
        print(f"{label}: {check} PASS")

print("local authenticated API smoke: PASS")
PY
