# scripts/

## Bash ↔ PowerShell parity — keep them in sync

Most contributor-facing scripts ship as a `.sh` + `.ps1` pair so macOS/Linux and Windows users get the same workflow. **When you edit one, edit the other in the same change.** Env-var names, defaults, flags, and behavior should match — if `start_services_dev.sh` reads `HEALTH_MAX_ATTEMPTS`, so should `start_services_dev.ps1`.

Current pairs:

- `setup_fork.{sh,ps1}` — contributor bootstrap (git remotes, submodule, venv, env files)
- `setup_requirements.{sh,ps1}` — Python + pipecat dependency install
- `start_services_dev.{sh,ps1}` — local backend launcher (auto-reload + health-check wait)
- `stop_services.{sh,ps1}`
- `makemigrate.{sh,ps1}` / `migrate.{sh,ps1}` — Alembic helpers

Bash-only (deployment / CI / OSS-user setup — not intended for Windows contributors):

- `start_services.sh` — VM production
- `start_services_docker.sh` — Docker image CMD
- `rolling_update.sh` — zero-downtime VM redeploy
- `setup_local.sh` / `setup_remote.sh` — OSS Docker-compose setup
- `format.sh` / `lint.sh` / `pre_commit.sh`
- `generate_sdk.sh` / `release_sdks.sh` / `dump_docs_openapi.py`

## The three "start" scripts — pick the right one

| Script                     | Where it runs      | Key behavior                                                                                                                    |
| -------------------------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `start_services_dev.sh`    | Local dev shell    | `uvicorn --reload`, exits after launching, restart by re-running, single arq worker, waits for `/api/v1/health` before exiting. |
| `start_services.sh`        | VM production      | Multi-port uvicorn behind nginx, `sudo nginx -t && systemctl reload`, writes `run/active_band` for `rolling_update.sh`.         |
| `start_services_docker.sh` | Docker image `CMD` | PID 1: traps SIGTERM, uvicorn `--workers $FASTAPI_WORKERS`, `wait -n` so a dying child tears the container down.                |

If you find yourself adding nginx/sudo logic to the dev script, or `--reload` to the production/Docker scripts, stop — you probably want a different file.
