#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/workspaces/dograh"
UI_ENV_EXAMPLE="$ROOT_DIR/ui/.env.example"
UI_ENV_FILE="$ROOT_DIR/ui/.env"
VENV_PATH="$ROOT_DIR/venv"
VENV_TEMPLATE="/opt/venv-template"

TOTAL_STEPS=5
STEP=0
STEP_START=$SECONDS
SCRIPT_START=$SECONDS

step() {
  STEP=$((STEP + 1))
  STEP_START=$SECONDS
  printf '\n==> [%d/%d] %s\n' "$STEP" "$TOTAL_STEPS" "$1"
}

step_done() {
  printf '    done in %ds\n' "$((SECONDS - STEP_START))"
}

fail() {
  printf '\n!! FAILED at step %d/%d (%s) after %ds\n' \
    "$STEP" "$TOTAL_STEPS" "${1:-unknown}" "$((SECONDS - SCRIPT_START))" >&2
  exit 1
}
trap 'fail "exit $?"' ERR

copy_if_missing() {
  local src=$1
  local dst=$2
  if [[ -f "$dst" ]]; then
    echo "Keeping existing $dst"
    return
  fi
  cp "$src" "$dst"
  echo "Created $dst from $src"
}

# Copy an api/.env*.example template to its target, rewriting infra hostnames
# from `localhost` to the docker service names defined in
# docker-compose-local.yaml. MINIO_PUBLIC_ENDPOINT stays on localhost — that
# URL ends up in UI responses and is loaded by the host browser via the
# forwarded port. No-op if the target already exists.
copy_env_with_docker_hostnames() {
  local src=$1
  local dst=$2
  if [[ -f "$dst" ]]; then
    echo "Keeping existing $dst"
    return
  fi
  cp "$src" "$dst"
  sed -i \
    -e 's|@localhost:5432|@postgres:5432|g' \
    -e 's|@localhost:6379|@redis:6379|g' \
    -e 's|^MINIO_ENDPOINT=localhost:9000|MINIO_ENDPOINT=minio:9000|' \
    "$dst"
  echo "Created $dst from $src (rewrote service hostnames for docker network)"
}

# Seed the venv named volume from the image-baked template, but only when
# the template's build-stamp differs from what's currently in the volume
# (first start, or any rebuild that changed requirements.txt / pipecat).
seed_venv() {
  local image_stamp venv_stamp
  image_stamp=$(cat "$VENV_TEMPLATE/.build-stamp" 2>/dev/null || echo missing)
  venv_stamp=$(cat "$VENV_PATH/.build-stamp" 2>/dev/null || echo none)

  if [[ "$image_stamp" == "$venv_stamp" ]]; then
    echo "Venv already in sync with image template (stamp=$venv_stamp)"
    return
  fi

  echo "Re-seeding venv: image=$image_stamp, volume=$venv_stamp"
  rsync -a --delete "$VENV_TEMPLATE/" "$VENV_PATH/"
}

cd "$ROOT_DIR"

step "Fixing ownership of named volume mountpoints"
# Named volumes are created owned by root; postCreateCommand runs as the
# remote user. Chown the mountpoint roots so the steps below can write.
sudo chown "$(id -u):$(id -g)" \
  "$VENV_PATH" \
  "$ROOT_DIR/ui/node_modules" \
  "$ROOT_DIR/api/mcp_server/ts_validator/node_modules"
step_done

step "Seeding venv from image template"
seed_venv
step_done

step "Copying example env files into place"
copy_env_with_docker_hostnames "$ROOT_DIR/api/.env.example"      "$ROOT_DIR/api/.env"
copy_env_with_docker_hostnames "$ROOT_DIR/api/.env.test.example" "$ROOT_DIR/api/.env.test"
copy_if_missing "$UI_ENV_EXAMPLE" "$UI_ENV_FILE"
step_done

step "Switching pipecat to editable install from workspace"
# pipecat's deps are already in the seeded venv as a frozen snapshot from
# the build context. Re-register editable from the bind-mounted workspace
# so source edits take effect. --no-deps skips re-resolving transitive
# dependencies (already present from the seeded image template).
uv pip install -e "$ROOT_DIR/pipecat" --no-deps
step_done

step "Installing npm dependencies (ui + ts_validator in parallel)"
npm ci --prefix ui &
ui_pid=$!
npm ci --prefix api/mcp_server/ts_validator &
ts_pid=$!
wait "$ui_pid" || fail "npm ci ui"
wait "$ts_pid" || fail "npm ci ts_validator"
step_done

# Optional personal hook: gitignored script for per-developer tools (e.g.
# claude, codex, etc.). Runs only if present; safe to omit.
LOCAL_HOOK="$ROOT_DIR/.devcontainer/install.local.sh"
if [[ -f "$LOCAL_HOOK" ]]; then
  printf '\n==> Running local install hook (%s)\n' "$LOCAL_HOOK"
  bash "$LOCAL_HOOK"
fi

printf '\nDevcontainer bootstrap complete in %ds.\n' "$((SECONDS - SCRIPT_START))"
