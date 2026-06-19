#!/usr/bin/env bash
set -e

ENV_FILE=".env"
REGISTRY="${REGISTRY:-ghcr.io/dograh-hq}"
ENABLE_TELEMETRY="${ENABLE_TELEMETRY:-true}"

fail() {
    echo "Error: $*" >&2
    exit 1
}

generate_secret() {
    if command -v python3 >/dev/null 2>&1 && python3 -c 'import secrets; print(secrets.token_hex(32))'; then
        return
    fi

    if command -v openssl >/dev/null 2>&1 && openssl rand -hex 32; then
        return
    fi

    if [[ -r /dev/urandom ]] && command -v od >/dev/null 2>&1 && command -v tr >/dev/null 2>&1 && od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; then
        return
    fi

    fail "Could not generate OSS_JWT_SECRET. Install python3 or openssl, or set OSS_JWT_SECRET manually in .env."
}

dotenv_value() {
    local key=$1
    local line

    [[ -f "$ENV_FILE" ]] || return 1

    while IFS= read -r line || [[ -n "$line" ]]; do
        case "$line" in
            "$key"=*)
                printf '%s\n' "${line#*=}"
                return 0
                ;;
        esac
    done < "$ENV_FILE"

    return 1
}

set_dotenv_value() {
    local key=$1
    local value=$2
    local tmp_file="${ENV_FILE}.tmp.$$"
    local line
    local updated=false

    if [[ -f "$ENV_FILE" ]]; then
        while IFS= read -r line || [[ -n "$line" ]]; do
            case "$line" in
                "$key"=*)
                    printf '%s=%s\n' "$key" "$value"
                    updated=true
                    ;;
                *)
                    printf '%s\n' "$line"
                    ;;
            esac
        done < "$ENV_FILE" > "$tmp_file"

        if [[ "$updated" != "true" ]]; then
            printf '%s=%s\n' "$key" "$value" >> "$tmp_file"
        fi

        mv "$tmp_file" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" > "$ENV_FILE"
    fi
}

[[ -f docker-compose.yaml ]] || fail "docker-compose.yaml not found. Download it first, then re-run this script."

existing_secret="$(dotenv_value OSS_JWT_SECRET || true)"
if [[ -z "$existing_secret" ]]; then
    set_dotenv_value OSS_JWT_SECRET "$(generate_secret)"
    echo "Created OSS_JWT_SECRET in $ENV_FILE."
else
    echo "OSS_JWT_SECRET is already set in $ENV_FILE."
fi

echo ""
echo "Docker registry: $REGISTRY"
echo "Telemetry enabled: $ENABLE_TELEMETRY"
echo ""
echo "This will run:"
echo "  REGISTRY=$REGISTRY ENABLE_TELEMETRY=$ENABLE_TELEMETRY docker compose up --pull always"
echo ""

if [[ ! -t 0 ]]; then
    echo "Run the command above from an interactive shell to start Dograh."
    exit 0
fi

read -r -p "Start Dograh now? [Y/n]: " answer
case "$answer" in
    [Nn]*)
        echo "Dograh was not started."
        exit 0
        ;;
esac

REGISTRY="$REGISTRY" ENABLE_TELEMETRY="$ENABLE_TELEMETRY" docker compose up --pull always
