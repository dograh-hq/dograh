# Conductor workspace setup

This directory makes [Conductor](https://conductor.build) workspaces (git
worktrees) self-contained: each one installs its own deps and runs its own
backend + UI on a dedicated port range, so you can develop several branches at
once without collisions.

## How it works

Conductor gives every workspace a block of **10 ports** starting at
`$CONDUCTOR_PORT`. We use:

| Port            | Service            | Script                  |
| --------------- | ------------------ | ----------------------- |
| `CONDUCTOR_PORT`     | UI (Next.js)       | `run-ui.sh`      |
| `CONDUCTOR_PORT + 1` | Backend (uvicorn)  | `run-backend.sh` |
| `+2 .. +9`           | reserved           | —                       |

The UI sits on the base port so Conductor's **Open** button (`preview_urls`)
lands on it directly — preview templates only substitute `$CONDUCTOR_PORT`, with
no `+1` arithmetic. The UI is wired to its own workspace's backend
(`NEXT_PUBLIC_BACKEND_URL`, `BACKEND_URL`) and the backend's CORS / `UI_APP_URL`
point back at its own UI — all derived from `$CONDUCTOR_PORT`, so no two
workspaces interfere.

### Shared, NOT per-workspace

Postgres, Redis, and MinIO run as a **single shared Docker stack** (compose
project `dograh`). `setup.sh` brings it up idempotently with
`COMPOSE_PROJECT_NAME=dograh` so every workspace reuses the same one instead of
fighting over the fixed 5432/6379/9000 ports. All workspaces therefore share one
database — fine for app servers, but it means the **Arq worker should run in only
one workspace** (`run-worker.sh`), since a single worker drains the shared queue
for everyone.

## The Run menu

> **Important:** a Conductor workspace runs **one run script at a time**.
> `run_mode = "concurrent"` lets *different workspaces* run simultaneously — it
> does **not** let you click two run buttons in the *same* workspace. That's why
> **dev** starts the UI and backend together instead of relying on two buttons.

The Run dropdown offers:

- **dev** (default) — UI (`$CONDUCTOR_PORT`) **and** backend (`$CONDUCTOR_PORT+1`)
  together, via `concurrently`. **Use this day to day.**
- **ui** — Next.js only, for debugging the frontend alone.
- **backend** — uvicorn only, for debugging the API alone.
- **worker** — Arq worker; start in just one workspace.

Hit **dev** and both servers come up; click Conductor's **Open** button to launch
the UI. Start **worker** once (e.g. in your primary workspace) when you need
background jobs — the same way you used to run a single arq worker in VS Code.

## Creating a new workspace

In Conductor: **New Workspace** → pick a branch. `setup.sh` then runs
automatically and:

1. copies the gitignored env files from your main checkout (see
   [Environment files](#environment-files)),
2. checks out the `pipecat` submodule,
3. builds `venv` (Python 3.13) + installs backend/pipecat deps,
4. `npm install` for the UI,
5. ensures the shared Docker stack is up,
6. runs `alembic upgrade head`.

The first setup is slow (deps); afterwards the Run buttons are instant.

## Environment files

The app's env files hold real secrets, so they're **gitignored** and never
committed — a fresh worktree won't have them. `setup.sh` copies them from your
main checkout (`$CONDUCTOR_ROOT_PATH`) into each new workspace:

| File                          | Used by                          |
| ----------------------------- | -------------------------------- |
| `api/.env`                    | backend (DB/Redis URLs, secrets) |
| `api/.env.test`               | backend test runs                |
| `ui/.env`                     | UI (backend URL, public config)  |
| `ui/.env.local`               | UI secrets (Stack/PostHog/etc.)  |
| `ui/.env.sentry-build-plugin` | UI Sentry source-map upload      |

The copy is idempotent (only fills in what's missing), so re-running setup won't
clobber a workspace-local edit. **Add a new env file?** List it in the loop near
the top of `setup.sh`.

## Files

| File                  | Purpose                                            |
| --------------------- | -------------------------------------------------- |
| `settings.toml`       | Conductor config: setup + run scripts, preview_urls |
| `setup.sh`            | One-time workspace bootstrap                        |
| `run-dev.sh`          | Default run: UI + backend together (`concurrently`) |
| `run-ui.sh`           | Foreground Next.js on `$CONDUCTOR_PORT`             |
| `run-backend.sh`      | Foreground uvicorn on `$CONDUCTOR_PORT + 1`         |
| `run-worker.sh`       | Foreground Arq worker (run in one workspace only)   |

> Need machine-local tweaks (e.g. a different port base or skipping the worker)?
> Put them in `.conductor/settings.local.toml`, which is personal and not
> committed.
