#!/usr/bin/env bash

DOGRAH_DEPLOY_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOGRAH_DEPLOY_REPO_ROOT="$(cd "$DOGRAH_DEPLOY_LIB_DIR/../.." 2>/dev/null && pwd || true)"

: "${RED:=\033[0;31m}"
: "${GREEN:=\033[0;32m}"
: "${YELLOW:=\033[1;33m}"
: "${BLUE:=\033[0;34m}"
: "${NC:=\033[0m}"

dograh_info() {
    echo -e "${BLUE}$*${NC}"
}

dograh_success() {
    echo -e "${GREEN}$*${NC}"
}

dograh_warn() {
    echo -e "${YELLOW}$*${NC}"
}

dograh_fail() {
    echo -e "${RED}Error: $*${NC}" >&2
    exit 1
}

dograh_project_dir() {
    if [[ -n "${DOGRAH_DEPLOY_PROJECT_DIR:-}" ]]; then
        printf '%s\n' "$DOGRAH_DEPLOY_PROJECT_DIR"
    else
        pwd
    fi
}

dograh_template_path() {
    local template_name=$1
    local candidate=""
    local project_dir

    project_dir="$(dograh_project_dir)"

    for candidate in \
        "$project_dir/deploy/templates/$template_name" \
        "$DOGRAH_DEPLOY_REPO_ROOT/deploy/templates/$template_name"
    do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    dograh_fail "Template '$template_name' not found"
}

dograh_init_script_path() {
    local candidate=""
    local project_dir

    project_dir="$(dograh_project_dir)"

    for candidate in \
        "$project_dir/scripts/run_dograh_init.sh" \
        "$DOGRAH_DEPLOY_REPO_ROOT/scripts/run_dograh_init.sh"
    do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    dograh_fail "run_dograh_init.sh not found"
}

dograh_load_env_file() {
    local env_file=${1:-.env}

    [[ -f "$env_file" ]] || dograh_fail "$env_file not found"

    set -a
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
}

dograh_host_from_url() {
    local url=$1

    url="${url#https://}"
    url="${url#http://}"
    url="${url%%/*}"

    printf '%s\n' "$url"
}

dograh_is_ipv4() {
    [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
}

dograh_is_local_ipv4() {
    local ip=$1
    local o1 o2 o3 o4 octet

    dograh_is_ipv4 "$ip" || return 1
    IFS=. read -r o1 o2 o3 o4 <<< "$ip"

    for octet in "$o1" "$o2" "$o3" "$o4"; do
        [[ "$octet" =~ ^[0-9]+$ ]] || return 1
        (( octet >= 0 && octet <= 255 )) || return 1
    done

    (( o1 == 10 )) && return 0
    (( o1 == 127 )) && return 0
    (( o1 == 169 && o2 == 254 )) && return 0
    (( o1 == 172 && o2 >= 16 && o2 <= 31 )) && return 0
    (( o1 == 192 && o2 == 168 )) && return 0
    (( o1 == 100 && o2 >= 64 && o2 <= 127 )) && return 0

    return 1
}

dograh_infer_server_ip() {
    local project_dir=${1:-$(dograh_project_dir)}
    local turn_conf="$project_dir/turnserver.conf"
    local ip=""

    if [[ -n "${SERVER_IP:-}" ]]; then
        printf '%s\n' "$SERVER_IP"
        return 0
    fi

    if [[ -f "$turn_conf" ]]; then
        ip="$(sed -n 's/^external-ip=//p' "$turn_conf" | head -1)"
        if [[ -n "$ip" ]]; then
            printf '%s\n' "$ip"
            return 0
        fi
    fi

    if [[ -n "${TURN_HOST:-}" ]] && dograh_is_ipv4 "$TURN_HOST"; then
        printf '%s\n' "$TURN_HOST"
        return 0
    fi

    if [[ -n "${PUBLIC_HOST:-}" ]] && dograh_is_ipv4 "$PUBLIC_HOST"; then
        printf '%s\n' "$PUBLIC_HOST"
        return 0
    fi

    return 1
}

dograh_infer_public_base_url() {
    if [[ -n "${PUBLIC_BASE_URL:-}" ]]; then
        printf '%s\n' "${PUBLIC_BASE_URL%/}"
        return 0
    fi

    if [[ -n "${BACKEND_API_ENDPOINT:-}" ]]; then
        printf '%s\n' "${BACKEND_API_ENDPOINT%/}"
        return 0
    fi

    if [[ -n "${PUBLIC_HOST:-}" ]]; then
        printf 'https://%s\n' "$PUBLIC_HOST"
        return 0
    fi

    if [[ -n "${SERVER_IP:-}" ]]; then
        printf 'https://%s\n' "$SERVER_IP"
        return 0
    fi

    return 1
}

dograh_infer_public_host() {
    local public_base_url=""

    if [[ -n "${PUBLIC_HOST:-}" ]]; then
        printf '%s\n' "$PUBLIC_HOST"
        return 0
    fi

    public_base_url="$(dograh_infer_public_base_url 2>/dev/null || true)"
    if [[ -n "$public_base_url" ]]; then
        dograh_host_from_url "$public_base_url"
        return 0
    fi

    if [[ -n "${TURN_HOST:-}" ]]; then
        printf '%s\n' "$TURN_HOST"
        return 0
    fi

    return 1
}

dograh_set_env_key() {
    local env_file=$1
    local key=$2
    local value=$3
    local tmp_file="${env_file}.tmp.$$"

    awk -v key="$key" -v value="$value" '
        BEGIN { updated = 0 }
        $0 ~ "^" key "=" {
            print key "=" value
            updated = 1
            next
        }
        { print }
        END {
            if (!updated) {
                print key "=" value
            }
        }
    ' "$env_file" > "$tmp_file"

    mv "$tmp_file" "$env_file"
}

dograh_delete_env_key() {
    local env_file=$1
    local key=$2
    local tmp_file="${env_file}.tmp.$$"

    awk -v key="$key" '$0 !~ "^" key "=" { print }' "$env_file" > "$tmp_file"
    mv "$tmp_file" "$env_file"
}

dograh_sync_remote_env_file() {
    local env_file=${1:-.env}
    local project_dir
    local public_base_url=""
    local public_host=""
    local server_ip=""

    project_dir="$(cd "$(dirname "$env_file")" && pwd)"
    dograh_load_env_file "$env_file"

    public_base_url="$(dograh_infer_public_base_url)" || dograh_fail "Could not determine PUBLIC_BASE_URL"
    public_base_url="${public_base_url%/}"
    public_host="$(dograh_infer_public_host)" || dograh_fail "Could not determine PUBLIC_HOST"
    server_ip="$(dograh_infer_server_ip "$project_dir")" || dograh_fail "Could not determine SERVER_IP"

    [[ "$public_base_url" =~ ^https?:// ]] || dograh_fail "PUBLIC_BASE_URL must include http:// or https://"
    dograh_is_ipv4 "$server_ip" || dograh_fail "SERVER_IP must be an IPv4 address (got: $server_ip)"

    dograh_set_env_key "$env_file" ENVIRONMENT "${ENVIRONMENT:-production}"
    dograh_set_env_key "$env_file" SERVER_IP "$server_ip"
    dograh_set_env_key "$env_file" PUBLIC_HOST "$public_host"
    dograh_set_env_key "$env_file" PUBLIC_BASE_URL "$public_base_url"

    # BACKEND_API_ENDPOINT / MINIO_PUBLIC_ENDPOINT / TURN_HOST are derived in-app
    # from PUBLIC_BASE_URL / PUBLIC_HOST (see api/constants.py), so sync neither
    # writes nor removes them: new installs simply omit them, and any value an
    # operator set by hand is left untouched as an explicit override.
}

dograh_validate_remote_runtime_env() {
    [[ "${FASTAPI_WORKERS:-}" =~ ^[1-9][0-9]*$ ]] || dograh_fail "FASTAPI_WORKERS must be a positive integer"
    [[ -n "${TURN_SECRET:-}" ]] || dograh_fail "TURN_SECRET is missing"
    [[ -n "${PUBLIC_HOST:-}" ]] || dograh_fail "PUBLIC_HOST is missing"
    [[ -n "${PUBLIC_BASE_URL:-}" ]] || dograh_fail "PUBLIC_BASE_URL is missing"
    dograh_is_ipv4 "${SERVER_IP:-}" || dograh_fail "SERVER_IP must be a valid IPv4 address"
    [[ "${PUBLIC_BASE_URL}" =~ ^https?:// ]] || dograh_fail "PUBLIC_BASE_URL must include http:// or https://"
    # BACKEND_API_ENDPOINT / MINIO_PUBLIC_ENDPOINT / TURN_HOST are derived in-app
    # from PUBLIC_BASE_URL / PUBLIC_HOST (see api/constants.py), so they are not
    # required here. When an operator sets them explicitly (split deployment),
    # their value is honored as-is — no equality check.
}

dograh_uses_init_compose_layout() {
    local project_dir=${1:-$(dograh_project_dir)}
    local compose_file="$project_dir/docker-compose.yaml"

    [[ -f "$compose_file" ]] || return 1
    grep -q "dograh-init:" "$compose_file" \
        && grep -q "nginx-generated:/etc/nginx/conf.d:ro" "$compose_file" \
        && grep -q "coturn-generated:/etc/coturn:ro" "$compose_file"
}

dograh_require_init_compose_layout() {
    local project_dir=${1:-$(dograh_project_dir)}

    if ! dograh_uses_init_compose_layout "$project_dir"; then
        dograh_fail "This install uses the legacy remote compose layout. Run ./update_remote.sh first so Docker uses dograh-init generated config."
    fi
}

dograh_render_remote_nginx_conf() {
    local project_dir=${1:-$(dograh_project_dir)}
    local destination=${2:-"$project_dir/nginx.conf"}
    local template=""
    local tmp_upstream=""

    template="$(dograh_template_path "nginx.remote.conf.template")"
    tmp_upstream="$(mktemp)"

    {
        echo "# Backend API workers - one uvicorn process per port, balanced by least_conn."
        echo "# Auto-generated by Dograh remote config renderer. Do not edit manually."
        echo "upstream dograh_api {"
        echo "    least_conn;"
        for ((i=0; i<FASTAPI_WORKERS; i++)); do
            printf '    server api:%d max_fails=3 fail_timeout=10s;\n' "$((8000 + i))"
        done
        echo "    keepalive 32;"
        echo "}"
    } > "$tmp_upstream"

    awk -v public_host="$PUBLIC_HOST" -v upstream_file="$tmp_upstream" '
        BEGIN {
            while ((getline line < upstream_file) > 0) {
                upstream = upstream line ORS
            }
            close(upstream_file)
        }
        {
            gsub(/__DOGRAH_PUBLIC_HOST__/, public_host)
            if ($0 == "__DOGRAH_UPSTREAM_BLOCK__") {
                printf "%s", upstream
            } else {
                print
            }
        }
    ' "$template" > "$destination"

    rm -f "$tmp_upstream"
}

dograh_render_remote_turn_conf() {
    local project_dir=${1:-$(dograh_project_dir)}
    local destination=${2:-"$project_dir/turnserver.conf"}
    local template=""
    local external_ip="${TURN_EXTERNAL_IP:-${SERVER_IP:-}}"

    template="$(dograh_template_path "turnserver.remote.conf.template")"
    [[ -n "$external_ip" ]] || dograh_fail "TURN external IP/host is missing"

    awk \
        -v external_ip="$external_ip" \
        -v turn_secret="$TURN_SECRET" \
        '
        {
            gsub(/__DOGRAH_TURN_EXTERNAL_IP__/, external_ip)
            gsub(/__DOGRAH_TURN_SECRET__/, turn_secret)
            print
        }
    ' "$template" > "$destination"
}

dograh_preflight_remote_init_render() {
    local project_dir=${1:-$(dograh_project_dir)}
    local env_file="$project_dir/.env"
    local cert_dir="$project_dir/certs"
    local init_script=""
    local tmp_root=""
    local nginx_conf=""
    local turn_conf=""
    local nginx_workers=0
    local rendered_secret=""
    local rendered_ip=""
    local rendered_server_name=""

    dograh_load_env_file "$env_file"
    dograh_validate_remote_runtime_env
    [[ -f "$cert_dir/local.crt" ]] || dograh_fail "certs/local.crt not found"
    [[ -f "$cert_dir/local.key" ]] || dograh_fail "certs/local.key not found"

    init_script="$(dograh_init_script_path)"
    tmp_root="$(mktemp -d)"
    nginx_conf="$tmp_root/nginx/default.conf"
    turn_conf="$tmp_root/coturn/turnserver.conf"

    (
        export ENVIRONMENT SERVER_IP PUBLIC_HOST PUBLIC_BASE_URL BACKEND_API_ENDPOINT MINIO_PUBLIC_ENDPOINT TURN_HOST TURN_SECRET FASTAPI_WORKERS
        export DOGRAH_INIT_WORKSPACE_DIR="$project_dir"
        export DOGRAH_INIT_OUTPUT_ROOT="$tmp_root"
        export DOGRAH_INIT_CERTS_DIR="$cert_dir"
        bash "$init_script" >/dev/null
    )

    [[ -f "$nginx_conf" ]] || dograh_fail "dograh-init did not render nginx config"
    [[ -f "$turn_conf" ]] || dograh_fail "dograh-init did not render coturn config"

    nginx_workers=$(awk '/^[[:space:]]*server api:[0-9]+/ { count += 1 } END { print count + 0 }' "$nginx_conf")
    [[ "$nginx_workers" -eq "$FASTAPI_WORKERS" ]] || dograh_fail "FASTAPI_WORKERS=$FASTAPI_WORKERS but nginx.conf has $nginx_workers upstream servers"

    rendered_server_name="$(awk '/^[[:space:]]*server_name / { print $2; exit }' "$nginx_conf" | sed 's/;$//')"
    [[ "$rendered_server_name" == "$PUBLIC_HOST" ]] || dograh_fail "nginx.conf server_name ($rendered_server_name) does not match PUBLIC_HOST ($PUBLIC_HOST)"

    rendered_secret="$(sed -n 's/^static-auth-secret=//p' "$turn_conf" | head -1)"
    [[ "$rendered_secret" == "$TURN_SECRET" ]] || dograh_fail "TURN_SECRET in .env does not match turnserver.conf"

    rendered_ip="$(sed -n 's/^external-ip=//p' "$turn_conf" | head -1)"
    [[ "$rendered_ip" == "$SERVER_IP" ]] || dograh_fail "SERVER_IP in .env does not match turnserver.conf"

    rm -rf "$tmp_root"
}

# Reconcile the running Postgres role password with POSTGRES_PASSWORD in .env.
#
# POSTGRES_PASSWORD only takes effect when the postgres data volume is first
# initialized. If the volume was created before .env had a generated password
# (e.g. an early start used the compose fallback `:-postgres`), or the password
# was later rotated, the role keeps its old password while the API connects with
# the .env value over TCP (pg_hba `scram-sha-256`) and dies with "password
# authentication failed for user postgres". start_docker.sh handles this for the
# OSS quickstart; the remote path (remote_up.sh) needs the same reconciliation.
#
# Bring postgres up on its own, then ALTER the role over the trusted local
# socket (pg_hba trusts `local`, so this works even when the password is
# currently mismatched). Idempotent: on a fresh volume it just re-sets the same
# value. Survives the later `--force-recreate` because the password lives in the
# data volume, not the container.
dograh_sync_postgres_password() {
    local project_dir=$1
    shift
    local compose=("$@")
    local env_file="$project_dir/.env"
    local password=""
    local ready=""
    local i

    [[ ${#compose[@]} -gt 0 ]] || compose=(docker compose)

    if [[ -f "$env_file" ]]; then
        password="$(awk -F= '/^POSTGRES_PASSWORD=/{sub(/^POSTGRES_PASSWORD=/, ""); print; exit}' "$env_file")"
    fi

    # No explicit password: the compose fallback (`:-postgres`) governs both the
    # DB init and the API's DATABASE_URL, so the two already agree — nothing to do.
    [[ -n "$password" ]] || return 0

    dograh_info "Syncing Postgres password from .env..."
    ( cd "$project_dir" && "${compose[@]}" up -d postgres ) >/dev/null

    for ((i = 0; i < 30; i++)); do
        if ( cd "$project_dir" && "${compose[@]}" exec -T postgres pg_isready -U postgres ) >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 1
    done
    [[ -n "$ready" ]] || dograh_fail "Postgres did not become ready while syncing POSTGRES_PASSWORD."

    printf '%s\n' "ALTER USER postgres WITH PASSWORD :'pw';" \
        | ( cd "$project_dir" && "${compose[@]}" exec -T postgres \
              psql -U postgres -d postgres -v ON_ERROR_STOP=1 -v "pw=$password" ) >/dev/null \
        || dograh_fail "Failed to sync Postgres password from .env."
    dograh_success "✓ Postgres password synced with .env"
}

dograh_prepare_remote_install() {
    local project_dir=${1:-$(dograh_project_dir)}
    local env_file="$project_dir/.env"

    dograh_sync_remote_env_file "$env_file"
    dograh_require_init_compose_layout "$project_dir"
    dograh_preflight_remote_init_render "$project_dir"
}

# ---------------------------------------------------------------------------
# TLS certificate helpers (self-signed bootstrap + Let's Encrypt via webroot)
# ---------------------------------------------------------------------------

# Map an IPv4 address to a public sslip.io / nip.io hostname, e.g.
# 203.0.113.10 -> 203-0-113-10.sslip.io. The hostname resolves back to the
# embedded IP from any public resolver, so Let's Encrypt can validate it over
# the HTTP-01 challenge without the operator owning a domain. Public IPs only:
# Let's Encrypt refuses to validate private/reserved addresses.
dograh_sslip_host_from_ip() {
    local ip=$1
    local suffix=${2:-sslip.io}

    dograh_is_ipv4 "$ip" || dograh_fail "dograh_sslip_host_from_ip: '$ip' is not an IPv4 address"
    printf '%s.%s\n' "${ip//./-}" "$suffix"
}

# Install certbot via the host package manager if it is not already present.
# Returns non-zero (instead of exiting) when no supported package manager is
# found or the install fails, so callers can fall back to a self-signed cert.
dograh_install_certbot() {
    if command -v certbot >/dev/null 2>&1; then
        return 0
    fi

    dograh_info "Installing Certbot..."
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update -qq && apt-get install -y -qq certbot
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y -q certbot
    elif command -v yum >/dev/null 2>&1; then
        yum install -y -q certbot
    else
        dograh_warn "Could not detect a package manager (apt/dnf/yum) to install certbot."
        return 1
    fi
}

# Obtain (or renew) a Let's Encrypt certificate for $host using the webroot
# challenge served by the running nginx container out of <project>/certs, then
# copy the issued cert to certs/local.{crt,key} (the files nginx reads). This
# needs nginx already running and serving /.well-known/acme-challenge/ on :80.
# Returns non-zero on failure so callers can keep the self-signed cert.
dograh_issue_letsencrypt_webroot() {
    local project_dir=$1
    local host=$2
    local email=${3:-}
    local webroot="$project_dir/certs"
    local live_dir="/etc/letsencrypt/live/$host"
    local -a email_args

    if [[ -n "$email" ]]; then
        email_args=(--email "$email")
    else
        email_args=(--register-unsafely-without-email)
    fi

    mkdir -p "$webroot/.well-known/acme-challenge"

    certbot certonly --webroot -w "$webroot" \
        --non-interactive --agree-tos --keep-until-expiring \
        "${email_args[@]}" \
        -d "$host" || return 1

    [[ -f "$live_dir/fullchain.pem" && -f "$live_dir/privkey.pem" ]] || return 1

    cp "$live_dir/fullchain.pem" "$webroot/local.crt"
    cp "$live_dir/privkey.pem" "$webroot/local.key"
    chmod 644 "$webroot/local.crt" "$webroot/local.key"
}

# Install a certbot deploy hook so renewed certificates are copied into
# <project>/certs and nginx is restarted to load them. Renewal itself is driven
# by certbot's packaged systemd timer / cron; webroot renewals need no downtime
# because the running nginx serves the challenge.
dograh_install_cert_renewal_hook() {
    local project_dir=$1
    local host=$2
    local hook_dir="/etc/letsencrypt/renewal-hooks/deploy"
    local hook_path="$hook_dir/dograh-reload.sh"

    mkdir -p "$hook_dir"

    cat > "$hook_path" << HOOK_EOF
#!/bin/bash
cp /etc/letsencrypt/live/$host/fullchain.pem $project_dir/certs/local.crt
cp /etc/letsencrypt/live/$host/privkey.pem $project_dir/certs/local.key
chmod 644 $project_dir/certs/local.crt $project_dir/certs/local.key

cd $project_dir
docker compose --profile remote restart nginx 2>/dev/null || true
HOOK_EOF
    chmod +x "$hook_path"
}

dograh_download_bundle_file_for_ref() {
    local destination=$1
    local remote_path=$2
    local ref=${3:-main}
    local raw_base="https://raw.githubusercontent.com/dograh-hq/dograh/$ref"
    local fallback_base="https://raw.githubusercontent.com/dograh-hq/dograh/main"

    if ! curl -fsSL -o "$destination" "$raw_base/$remote_path"; then
        dograh_warn "Warning: '$remote_path' not found at '$ref' - falling back to main"
        curl -fsSL -o "$destination" "$fallback_base/$remote_path"
    fi
}

dograh_download_init_support_bundle() {
    local project_dir=$1
    local ref=${2:-main}

    mkdir -p "$project_dir/scripts/lib" "$project_dir/deploy/templates"

    mkdir -p "$project_dir/scripts"
    dograh_download_bundle_file_for_ref "$project_dir/scripts/lib/setup_common.sh" "scripts/lib/setup_common.sh" "$ref"
    dograh_download_bundle_file_for_ref "$project_dir/scripts/run_dograh_init.sh" "scripts/run_dograh_init.sh" "$ref"
    chmod +x "$project_dir/scripts/run_dograh_init.sh"
    dograh_download_bundle_file_for_ref "$project_dir/deploy/templates/nginx.remote.conf.template" "deploy/templates/nginx.remote.conf.template" "$ref"
    dograh_download_bundle_file_for_ref "$project_dir/deploy/templates/turnserver.remote.conf.template" "deploy/templates/turnserver.remote.conf.template" "$ref"
}

dograh_download_remote_support_bundle() {
    local project_dir=$1
    local ref=${2:-main}

    dograh_download_bundle_file_for_ref "$project_dir/remote_up.sh" "remote_up.sh" "$ref"
    chmod +x "$project_dir/remote_up.sh"
    dograh_download_init_support_bundle "$project_dir" "$ref"
}
