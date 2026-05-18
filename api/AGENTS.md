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

## Organization Scoping (Security)

Most resources in this codebase are scoped to an organization. **Whenever you read or write an organization-scoped field, you must filter or validate by `organization_id`.** This is a tenant-isolation requirement, not a stylistic one — skipping the check lets a user in one org touch resources owned by another.

Concretely:

- **Reading** an org-scoped row by id: pass `organization_id=user.selected_organization_id` to the DB client (or query through an org-scoped helper). Never trust an id from the request body to imply ownership.
- **Writing** a foreign key that points at another org-scoped resource (e.g. attaching `inbound_workflow_id` to a phone number, setting `telephony_configuration_id` on a campaign): fetch the referenced row with the user's `organization_id` and reject with 404 if it doesn't belong. The FK constraint only proves the row exists — it doesn't prove the caller is allowed to reference it.
- **Listing** org-scoped resources: filter by `organization_id` at the query level, not in Python after the fact.

If a route's handler does not have access to an `organization_id` (e.g. webhook callbacks), derive it from the request payload and validate that derivation explicitly — don't assume.

## Development

```bash
uvicorn api.app:app --reload --port 8000
```
