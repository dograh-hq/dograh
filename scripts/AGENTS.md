# scripts/

## Bash ↔ PowerShell parity — keep them in sync

Most contributor-facing scripts ship as a `.sh` + `.ps1` pair so macOS/Linux and Windows users get the same workflow. **When you edit one, edit the other in the same change.** Env-var names, defaults, flags, and behavior should match — if `start_services_dev.sh` reads `HEALTH_MAX_ATTEMPTS`, so should `start_services_dev.ps1`.

Current pairs:

- `setup_fork.{sh,ps1}` — contributor bootstrap (git remotes, submodule, venv, env files)
- `setup_requirements.{sh,ps1}` — Python + pipecat dependency install
- `start_services_dev.{sh,ps1}` — local backend launcher (auto-reload + health-check wait)
- `stop_services.{sh,ps1}`
- `makemigrate.{sh,ps1}` / `migrate.{sh,ps1}` — Alembic helpers
- `setup_local.{sh,ps1}` — OSS local Docker-compose setup (optional coturn/TURN)

Bash-only (deployment / CI / OSS-user setup — not intended for Windows contributors):

- `start_services.sh` — VM production
- `start_services_docker.sh` — Docker image CMD
- `rolling_update.sh` — zero-downtime VM redeploy
- `setup_remote.sh` — OSS remote Docker-compose setup
- `format.sh` / `lint.sh` / `pre_commit.sh`
- `generate_sdk.sh` / `release_sdks.sh` / `dump_docs_openapi.py`

## Deployment Memory — current OSS Docker state

This directory now has a shared deployment model for OSS Docker installs. If you touch any of the scripts below, assume they are coupled and review them together:

- `scripts/lib/setup_common.sh` is the shared deployment helper library. It is sourced by `setup_local.sh`, `setup_remote.sh`, `update_remote.sh`, `setup_custom_domain.sh`, `run_dograh_init.sh`, and repo-root `remote_up.sh`.
- `setup_common.sh` must stay safe to source. It should not set shell options like `set -u` for callers.
- `.env` is the single operator-owned source of truth for remote deployment settings. Remote/runtime config should derive from it, not the other way around.
- Canonical remote keys in `.env`: `ENVIRONMENT`, `SERVER_IP`, `PUBLIC_HOST`, `PUBLIC_BASE_URL`, `BACKEND_API_ENDPOINT`, `MINIO_PUBLIC_ENDPOINT`, `TURN_HOST`, `TURN_SECRET`, `FASTAPI_WORKERS`, `OSS_JWT_SECRET`.
- `remote_up.sh` is the supported remote startup entrypoint. It runs preflight via `dograh_prepare_remote_install`, runs `docker compose config -q`, then starts the stack.
- `docker-compose.yaml` uses a one-shot `dograh-init` service for profiles `remote` and `local-turn`.
- `dograh-init` executes `scripts/run_dograh_init.sh`, which renders nginx/coturn runtime config into named volumes consumed by `nginx` and `coturn`.
- Remote nginx/coturn config is runtime-generated. Host-managed `nginx.conf` / `turnserver.conf` are legacy only; update flow may back them up and delete them, but current installs should not depend on them.
- `setup_remote.sh` writes `.env`, downloads the deployment helper bundle, generates self-signed certs, validates the init-based config, and tells operators to start via `./remote_up.sh` or `./remote_up.sh --build`.
- `update_remote.sh` is the migration/upgrade path for prebuilt remote installs. It refreshes `docker-compose.yaml`, `remote_up.sh`, `scripts/run_dograh_init.sh`, `scripts/lib/setup_common.sh`, and `deploy/templates/*`, backs up touched files, removes legacy host `nginx.conf` / `turnserver.conf`, and revalidates the init-based path.
- `setup_custom_domain.sh` is certificate/domain glue only. It must not own nginx config. It updates canonical public URL keys in `.env`, copies Let's Encrypt certs into `certs/`, installs renewal hook, and restarts through `./remote_up.sh`.
- `setup_local.{sh,ps1}` has an interactive `Enable coturn? [y/N]` prompt unless `ENABLE_COTURN` is preset. If coturn is enabled, it downloads the minimal helper bundle needed for `local-turn` (`setup_common.sh`, `run_dograh_init.sh`, templates) and relies on `dograh-init` to render coturn config.
- `setup_local.{sh,ps1}` must remain safe under unset env vars; use `${VAR:-}` guards in Bash and null/empty checks in PowerShell for optional inputs like `ENABLE_COTURN`, `TURN_HOST`, `TURN_SECRET`, `DOGRAH_SKIP_DOWNLOAD`.
- `run_dograh_init.sh` is an executable entrypoint, not a library. Compose runs it directly. If it ever gets refactored, keep the distinction between sourced helper logic (`lib/`) and executable entrypoints.
- `dograh_prepare_remote_install` in `setup_common.sh` currently does three things: sync canonical `.env` keys, reject legacy compose layouts that do not use `dograh-init`, and preflight the init render in a temp directory.
- `dograh_uses_init_compose_layout` / `dograh_require_init_compose_layout` are the guardrails for old installs. If a remote install still bind-mounts host `nginx.conf` / `turnserver.conf`, the intended fix path is `./update_remote.sh`.
- Templates live under `deploy/templates/`. `nginx.remote.conf.template` contains the static shape and `dograh_render_remote_nginx_conf` expands the multi-worker upstream block dynamically. `turnserver.remote.conf.template` is also rendered from env.
- If you rename/move any of these deployment files, update all of: bootstrap curl URLs inside scripts, helper-bundle download paths in `setup_common.sh`, backup lists in `update_remote.sh`, docs under `docs/deployment/`, and any existence checks in `setup_local.{sh,ps1}` / `setup_custom_domain.sh`.

## The three "start" scripts — pick the right one

| Script                     | Where it runs      | Key behavior                                                                                                                    |
| -------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `start_services_dev.sh`    | Local dev shell    | `uvicorn --reload`, exits after launching, restart by re-running, single arq worker, waits for `/api/v1/health` before exiting. |
| `start_services.sh`        | VM production      | Multi-port uvicorn behind nginx, `sudo nginx -t && systemctl reload`, writes `run/active_band` for `rolling_update.sh`.         |
| `start_services_docker.sh` | Docker image `CMD` | PID 1: traps SIGTERM, uvicorn `--workers $FASTAPI_WORKERS`, `wait -n` so a dying child tears the container down.                |

If you find yourself adding nginx/sudo logic to the dev script, or `--reload` to the production/Docker scripts, stop — you probably want a different file.
