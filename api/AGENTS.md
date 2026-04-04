# API - Backend Service

FastAPI backend for the Dograh voice AI platform.

## Project Structure

```
api/
├── app.py            # Application entry point, FastAPI setup
├── routes/           # API endpoint handlers
├── services/         # Business logic and integrations
├── db/               # Database models and data access
├── schemas/          # Pydantic request/response schemas
├── tasks/            # Background jobs (ARQ)
├── utils/            # Utility functions
├── alembic/          # Database migrations
├── constants.py      # Environment variables and constants
└── tests/            # Test suite
```

## Where to Find Things

| Looking for...         | Go to...                                                                 |
| ---------------------- | ------------------------------------------------------------------------ |
| API endpoints          | `routes/` - each file is a router module, aggregated in `routes/main.py` |
| Business logic         | `services/` - organized by domain (telephony, workflow, campaign, etc.)  |
| Database models        | `db/models.py`                                                           |
| Database queries       | `db/*_client.py` files (repository pattern)                              |
| Request/response types | `schemas/`                                                               |
| Background tasks       | `tasks/` - uses ARQ for async job processing                             |
| Environment config     | `constants.py`                                                           |

## API Structure

- All routes are mounted at `/api/v1` prefix
- Routes are organized by domain (workflow, telephony, campaign, user, etc.)
- `routes/main.py` aggregates all routers

## Database Migrations

```bash
./scripts/makemigrate.sh "description"  # Create migration
./scripts/migrate.sh                     # Run migrations
```

## Cross-Worker State Sync

When an API endpoint updates in-memory state (e.g. cached credentials, config objects), that change only affects the worker process that handled the request. With multiple FastAPI workers, **use `WorkerSyncManager`** (`services/worker_sync/`) to propagate changes to all workers via Redis pub/sub instead of updating local state directly.

## Development

```bash
uvicorn api.app:app --reload --port 8000
```
