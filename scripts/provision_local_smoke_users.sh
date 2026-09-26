#!/usr/bin/env bash
set -euo pipefail

# Provision the two repeatable local smoke-test accounts through the normal
# application signup endpoint. Passwords are supplied at runtime only; never
# put them in this file or in the repository.

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


def request(path, payload):
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


users = (
    ("smokescreen", os.environ["SMOKESCREEN_EMAIL"], os.environ["SMOKESCREEN_PASSWORD"]),
    ("testuser", os.environ["TESTUSER_EMAIL"], os.environ["TESTUSER_PASSWORD"]),
)

for label, email, password in users:
    login_status = request("/api/v1/auth/login", {"email": email, "password": password})
    if login_status == 200:
        print(f"{label}: existing local account verified")
        continue
    if login_status != 401:
        print(f"{label}: login probe failed with HTTP {login_status}", file=sys.stderr)
        sys.exit(1)

    signup_status = request(
        "/api/v1/auth/signup",
        {"email": email, "password": password, "name": label},
    )
    if signup_status not in (200, 201):
        print(f"{label}: signup failed with HTTP {signup_status}", file=sys.stderr)
        sys.exit(1)
    print(f"{label}: local account provisioned")
PY
