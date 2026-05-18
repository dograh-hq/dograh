# Dograh - Project Overview

Dograh is a voice AI platform for building and deploying conversational AI agents with telephony and WebRTC support.

## Project Structure

```
dograh/
├── api/              # Backend - FastAPI application
├── ui/               # Frontend - Next.js application
├── scripts/          # Helper scripts for local development
├── docs/             # Mintlify documentation
├── pipecat/          # Pipecat framework (git submodule)
├── docker-compose.yaml       # Production/OSS deployment
├── docker-compose-local.yaml # Local development services
```

## Tech Stack

- **Backend**: Python with FastAPI
- **Frontend**: Next.js 15 with React 19, TypeScript, Tailwind CSS
- **Database**: PostgreSQL with SQLAlchemy (async)
- **Cache/Queue**: Redis with ARQ for background tasks
- **Storage**: MinIO (S3-compatible) for audio files

## Local Development

### Starting Services

```bash
# Start infrastructure services (postgres, redis, minio)
./scripts/start_services_dev.sh

# Stop all services
./scripts/stop_services.sh
```

On Windows (PowerShell):
```powershell
.\scripts\start_services_dev.ps1
.\scripts\stop_services.ps1
```

## Environment Configuration

- `api/.env` - Backend environment variables. Source this when running diagnostic scripts or one-off services against the dev DB (e.g. `python -m api.services.admin_utils.local_exec`).
- `api/.env.test` - Test-only environment variables. Source this when running pytest so tests hit the test DB and never the dev/prod credentials in `api/.env`.
- `ui/.env` - Frontend environment variables

Typical invocation:

```bash
# Tests
source venv/bin/activate && set -a && source api/.env.test && set +a && python -m pytest api/tests/...

# Diagnostics / scripts
source venv/bin/activate && set -a && source api/.env && set +a && python -m api.services.admin_utils.local_exec
```
