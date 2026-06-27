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

    fail "Could not generate a secret. Install python3 or openssl, or set secrets manually in .env."
}

generate_minio_root_user() {
    printf 'dograh%s\n' "$(generate_secret | cut -c1-12)"
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

postgres_volume_name() {
    local volume_name=""
    local project_name=""

    if command -v python3 >/dev/null 2>&1; then
        volume_name="$(
            docker compose config --format json 2>/dev/null \
                | python3 -c 'import json, sys; print(json.load(sys.stdin).get("volumes", {}).get("postgres_data", {}).get("name", ""))' 2>/dev/null \
                || true
        )"
        if [[ -n "$volume_name" ]]; then
            printf '%s\n' "$volume_name"
            return
        fi
    fi

    project_name="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
    project_name="$(printf '%s' "$project_name" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')"
    printf '%s_postgres_data\n' "$project_name"
}

sync_postgres_password() {
    local postgres_password=$1
    local volume_name=""
    local postgres_ready=false

    [[ -n "$postgres_password" ]] || return

    volume_name="$(postgres_volume_name)"
    if ! docker volume inspect "$volume_name" >/dev/null 2>&1; then
        return
    fi

    echo "Existing Postgres volume detected; syncing postgres password from $ENV_FILE."
    REGISTRY="$REGISTRY" ENABLE_TELEMETRY="$ENABLE_TELEMETRY" docker compose up -d postgres

    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
        if docker compose exec -T postgres pg_isready -U postgres >/dev/null 2>&1; then
            postgres_ready=true
            break
        fi
        sleep 1
    done

    if [[ "$postgres_ready" != "true" ]]; then
        fail "Postgres did not become ready while syncing POSTGRES_PASSWORD."
    fi

    printf '%s\n' "ALTER USER postgres WITH PASSWORD :'dograh_password';" \
        | docker compose exec -T postgres psql \
        -U postgres \
        -d postgres \
        -v ON_ERROR_STOP=1 \
        -v "dograh_password=$postgres_password" >/dev/null
    echo "Postgres password synced."
}

[[ -f docker-compose.yaml ]] || fail "docker-compose.yaml not found. Download it first, then re-run this script."

env_file_existed=false
if [[ -f "$ENV_FILE" ]]; then
    env_file_existed=true
fi

existing_secret="$(dotenv_value OSS_JWT_SECRET || true)"
if [[ -z "$existing_secret" ]]; then
    set_dotenv_value OSS_JWT_SECRET "$(generate_secret)"
    echo "Created OSS_JWT_SECRET in $ENV_FILE."
else
    echo "OSS_JWT_SECRET is already set in $ENV_FILE."
fi

existing_postgres_password="$(dotenv_value POSTGRES_PASSWORD || true)"
if [[ -z "$existing_postgres_password" ]]; then
    if [[ "$env_file_existed" == "false" ]]; then
        set_dotenv_value POSTGRES_PASSWORD "$(generate_secret)"
        echo "Created POSTGRES_PASSWORD in $ENV_FILE."
    else
        echo "POSTGRES_PASSWORD is not set in $ENV_FILE; keeping the docker-compose fallback for existing local data volumes."
    fi
else
    echo "POSTGRES_PASSWORD is already set in $ENV_FILE."
fi

existing_redis_password="$(dotenv_value REDIS_PASSWORD || true)"
if [[ -z "$existing_redis_password" ]]; then
    set_dotenv_value REDIS_PASSWORD "$(generate_secret)"
    echo "Created REDIS_PASSWORD in $ENV_FILE."
else
    echo "REDIS_PASSWORD is already set in $ENV_FILE."
fi

existing_minio_root_user="$(dotenv_value MINIO_ROOT_USER || true)"
if [[ -z "$existing_minio_root_user" ]]; then
    existing_minio_access_key="$(dotenv_value MINIO_ACCESS_KEY || true)"
    if [[ -n "$existing_minio_access_key" ]]; then
        set_dotenv_value MINIO_ROOT_USER "$existing_minio_access_key"
        echo "Created MINIO_ROOT_USER in $ENV_FILE from existing MINIO_ACCESS_KEY."
    else
        set_dotenv_value MINIO_ROOT_USER "$(generate_minio_root_user)"
        echo "Created MINIO_ROOT_USER in $ENV_FILE."
    fi
else
    echo "MINIO_ROOT_USER is already set in $ENV_FILE."
fi

existing_minio_root_password="$(dotenv_value MINIO_ROOT_PASSWORD || true)"
if [[ -z "$existing_minio_root_password" ]]; then
    existing_minio_secret_key="$(dotenv_value MINIO_SECRET_KEY || true)"
    if [[ -n "$existing_minio_secret_key" ]]; then
        set_dotenv_value MINIO_ROOT_PASSWORD "$existing_minio_secret_key"
        echo "Created MINIO_ROOT_PASSWORD in $ENV_FILE from existing MINIO_SECRET_KEY."
    else
        set_dotenv_value MINIO_ROOT_PASSWORD "$(generate_secret)"
        echo "Created MINIO_ROOT_PASSWORD in $ENV_FILE."
    fi
else
    echo "MINIO_ROOT_PASSWORD is already set in $ENV_FILE."
fi

echo ""
echo "Docker registry: $REGISTRY"
echo ""
echo "This will run:"
echo "  REGISTRY=$REGISTRY ENABLE_TELEMETRY=$ENABLE_TELEMETRY docker compose --profile tunnel up --pull always"
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

postgres_password="$(dotenv_value POSTGRES_PASSWORD || true)"
sync_postgres_password "$postgres_password"

REGISTRY="$REGISTRY" ENABLE_TELEMETRY="$ENABLE_TELEMETRY" docker compose --profile tunnel up --pull always
