# API - Backend Service

FastAPI backend for the Dograh voice AI platform.

## Project Structure

```
api/
├── routes/           # API endpoint handlers
├── services/         # Domain logic, runtime systems, and extension seams
├── db/               # Database models and data access
├── schemas/          # Pydantic request/response schemas
├── tasks/            # Background jobs and post-call work
├── mcp_server/       # MCP surface exposed by the backend
├── utils/            # Shared utilities
├── alembic/          # Database migrations
└── tests/            # Test suite
```

## Where to Find Things

| Looking for...               | Go to...                                                                      |
| ---------------------------- | ----------------------------------------------------------------------------- |
| API endpoints                | `routes/` - domain routers mounted under `/api/v1`                            |
| Workflow graph and node data | `services/workflow/`                                                          |
| Live pipeline runtime        | `services/pipecat/`                                                           |
| Telephony providers/call flow| `services/telephony/`                                                         |
| Third-party integrations     | `services/integrations/`                                                      |
| Campaign and other domains   | `services/`                                                                   |
| Database access              | `db/`                                                                         |
| Request/response types       | `schemas/`                                                                    |
| Background jobs              | `tasks/`                                                                      |
| MCP backend surface          | `mcp_server/`                                                                 |
| Tests                        | `tests/`                                                                      |

## API Structure

- All routes are mounted at `/api/v1` prefix
- Routes are organized by domain under `routes/`
- Workflow execution spans `services/workflow/`, `services/pipecat/`, and `tasks/`
- Telephony is a full subsystem under `services/telephony/`, with provider-specific packages under `services/telephony/providers/`
- Integrations extend through `services/integrations/`; package-specific rules should live in that subtree's own `AGENTS.md`

## Routes vs Service Layer

**Keep route handlers focused on HTTP concerns** — parse and validate the request, resolve auth and `organization_id`, and shape the response. Simple CRUD, list, and detail handlers may call `db/` client methods directly when the call is organization-scoped, or when a scoped parent lookup has already established ownership of a child identifier. All runtime query construction, ORM loading choices, direct session use, and transaction details belong in `db/`; routes, services, tasks, and MCP code must call DB clients instead of importing SQLAlchemy or opening sessions. Do not create pass-through services solely to preserve a route → service → DB call chain.

Put reusable or multi-step orchestration, business rules, external calls, and substantial computation in `services/` (or `tasks/` for background work). Before adding such logic to a handler, extend an existing `services/<domain>/` module that owns the concern (see *Where to Find Things*) before adding a focused new module; never a catch-all. Litmus test: if `tasks/`, `mcp_server/`, or another route could reuse it, it must live in `services/` to be importable.

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
