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
| `CONDUCTOR_PORT`     | Backend (uvicorn)  | `run-backend.sh` |
| `CONDUCTOR_PORT + 1` | UI (Next.js)       | `run-ui.sh`      |
| `+2 .. +9`           | reserved           | —                       |

The UI is wired to its own workspace's backend (`NEXT_PUBLIC_BACKEND_URL`,
`BACKEND_URL`) and the backend's CORS / `UI_APP_URL` point back at its own UI —
all derived from `$CONDUCTOR_PORT`, so no two workspaces interfere.

### Shared, NOT per-workspace

Postgres, Redis, and MinIO run as a **single shared Docker stack** (compose
project `dograh`). `setup.sh` brings it up idempotently with
`COMPOSE_PROJECT_NAME=dograh` so every workspace reuses the same one instead of
fighting over the fixed 5432/6379/9000 ports. All workspaces therefore share one
database — fine for app servers, but it means the **Arq worker should run in only
one workspace** (`run-worker.sh`), since a single worker drains the shared queue
for everyone.

## The Run menu

Conductor shows three buttons (`run_mode = "concurrent"`, so they coexist):

- **backend** (default) — FastAPI/uvicorn with `--reload` on `$CONDUCTOR_PORT`
- **ui** — Next.js dev server on `$CONDUCTOR_PORT + 1`
- **worker** — Arq worker; start in just one workspace

Start **backend** and **ui** in each workspace you want to use. Start **worker**
once (e.g. in your primary workspace) when you need background jobs — the same
way you used to run a single arq worker in VS Code.

## Proving you're in the right worktree

`run-ui.sh` exports `NEXT_PUBLIC_WORKSPACE_NAME=$CONDUCTOR_WORKSPACE_NAME`, which
`ui/src/components/WorkspaceBadge.tsx` renders as a small color-coded pill in the
bottom-left corner (e.g. `⬡ pattaya :8201`). The color is derived from the
workspace name, so two worktrees are instantly distinguishable. The badge is
invisible in production and in a plain `npm run dev` (no env var set).

## Creating a new workspace

In Conductor: **New Workspace** → pick a branch. Conductor first copies the
gitignored env files listed in `../.worktreeinclude` (`api/.env`, `ui/.env`,
`ui/.env.local`, …) from your main checkout, then `setup.sh` runs automatically
and:

1. checks out the `pipecat` submodule,
2. builds `venv` (Python 3.13) + installs backend/pipecat deps,
3. `npm install` for the UI,
4. ensures the shared Docker stack is up,
5. runs `alembic upgrade head`.

The first setup is slow (deps); afterwards the Run buttons are instant.

## Files

| File                  | Purpose                                            |
| --------------------- | -------------------------------------------------- |
| `settings.toml`       | Conductor config: setup + run scripts, run_mode    |
| `setup.sh`            | One-time workspace bootstrap                        |
| `run-backend.sh`      | Foreground uvicorn on `$CONDUCTOR_PORT`             |
| `run-ui.sh`           | Foreground Next.js on `$CONDUCTOR_PORT + 1`         |
| `run-worker.sh`       | Foreground Arq worker (run in one workspace only)   |
| `../.worktreeinclude` | Gitignored env files Conductor copies per workspace |

> Need machine-local tweaks (e.g. a different port base or skipping the worker)?
> Put them in `.conductor/settings.local.toml`, which is personal and not
> committed.
