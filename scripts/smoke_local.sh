#!/usr/bin/env bash
set -euo pipefail

# Authenticated local API smoke test. It deliberately uses credentials from
# the environment and never prints tokens or passwords. Interactive browser
# checks (audio and five-turn simulation) remain separate because they require
# a user gesture and configured model providers.

BASE_URL="${BASE_URL:-http://localhost:8000}"
SMOKESCREEN_EMAIL="${SMOKESCREEN_EMAIL:-smokescreen@calmos.io}"
TESTUSER_EMAIL="${TESTUSER_EMAIL:-testuser@calmos.io}"

if [[ -z "${SMOKESCREEN_PASSWORD:-}" || -z "${TESTUSER_PASSWORD:-}" ]]; then
    echo "Set SMOKESCREEN_PASSWORD and TESTUSER_PASSWORD in your shell or password manager." >&2
    exit 2
fi

BASE_URL="$BASE_URL" \
SMOKESCREEN_EMAIL="$SMOKESCREEN_EMAIL" \
TESTUSER_EMAIL="$TESTUSER_EMAIL" \
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
    except (urllib.error.HTTPError, json.JSONDecodeError, urllib.error.URLError) as error:
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
    except (urllib.error.HTTPError, json.JSONDecodeError, urllib.error.URLError) as error:
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
