---
name: review-pr
description: Review a Dograh pull request, branch diff, or pasted patch for repo-specific security and correctness risks that are not obvious from generic FastAPI, Next.js, or Python conventions. Use when the user asks to review a PR, audit a diff, check whether changes are safe to merge, review their own changes, or asks what to look for in a Dograh PR. Focus on tenant isolation, route auth, webhook signing and org derivation, DB layering, worker-sync, migrations, generated SDK usage, and test hazards.
---

# Reviewing PRs (dograh)

This skill is for reviewing any PR, including PRs written by maintainers. Focus on Dograh-specific regression risks. Skip generic lint, formatting, and type-check comments unless they connect to one of the repo-specific issues below.

The main failure modes in this repo are:

- Missing org scoping on request-reachable reads or writes
- Authless routes or websockets
- Trusting unsigned webhook fields
- SQL written outside `api/db/*_client.py`
- Per-worker cache state updated without worker sync
- UI calls that bypass the generated SDK
- Migrations that are not safe on existing production data

## How to drive the review

1. Get the diff:
   - GitHub PR: `gh pr diff <N>` or `gh pr view <N> --json files,additions,deletions`
   - Local branch: `git diff origin/main...HEAD`
2. Bucket changed files into the sections below.
3. Read the current repo as source of truth before finalizing findings:
   - `api/AGENTS.md` for org scoping and worker-sync
   - `ui/AGENTS.md` for generated client rules
   - Touched models, DB clients, routes, services, and migrations
4. Run only the sections relevant to the changed files.
5. Report findings as `<file>:<line> -> <problem> -> <correct pattern>`.

## Freshness rule

Treat this file as review policy and navigation, not as a frozen inventory.

- If the current repo conflicts with this skill, trust the repo and mention the drift.
- Do not rely on static allowlists or exact line numbers from this file.
- Review against the code in the PR and the current local repo, not against old prose.

### File to section map

| Path pattern in diff                                  | Sections to run |
| ----------------------------------------------------- | --------------- |
| `api/routes/*.py`                                     | 1, 2, 8         |
| `api/db/*_client.py`, `api/db/models.py`              | 2, 3            |
| `api/services/**/*.py`                                | 2, 3, 4         |
| `api/tasks/*.py`                                      | 2, 3, 5         |
| `api/alembic/versions/*.py`                           | 6               |
| `api/mcp_server/**`, `api/services/workflow/mcp_*.py` | 1, 2, 7         |
| `ui/**`                                               | 9               |
| `api/constants.py`, anything `os.getenv`              | 10              |
| `api/tests/**`                                        | 11              |
| `api/schemas/*.py`                                    | 12              |

---

## 1. Route authentication (`api/routes/*.py`)

There is no global auth middleware. Each route declares its own auth behavior. Forgetting one creates a silently public endpoint.

Common auth deps from `api.services.auth.depends`:

- `get_user`
- `get_user_ws`
- `get_superuser`

Checks:

- A new `@router.<verb>(...)` handler with no auth dependency is public. Treat that as a finding unless the current file already establishes a deliberate public auth pattern such as public token auth, signed webhook auth, or an equivalent websocket token flow.
- `get_user` on an impersonation, cross-org, or global reporting endpoint should usually be `get_superuser`.
- A route that reimplements bearer or API-key parsing instead of using the shared auth dependency is a finding.
- A websocket handler without `Depends(get_user_ws)` and without a clear public token path is a finding.
- A PR tightening `CORSMiddleware` to a fixed origin list needs strong justification. Dograh relies on cross-origin embedding; endpoint auth is the real control.

Useful commands:

```bash
rg -n "Depends\\((get_user|get_user_ws|get_superuser)\\)" api/routes
rg -n "@router\\.(get|post|put|delete|patch|websocket)" api/routes
```

---

## 2. Organization scoping (the cross-tenant rule)

This is the highest priority rule in the repo. Every request-reachable read or write of an org-scoped resource must filter or validate by `organization_id`.

Use `api/AGENTS.md` as the canonical summary.

Determine scope from the current code:

- Direct scope: the model has `organization_id`
- Indirect scope: the model reaches an org through a parent FK or relationship
- Legacy spelling may exist in old migrations or old code, but new runtime code should use `organization_id`

Checks:

- Any `*_by_id(...)` call in a route handler is suspicious. If request-reachable and unscoped, it is usually a finding.
- New `list_*` or `get_*` endpoints must filter in SQL, not in Python after `.all()`.
- If a request writes an FK to another org-scoped resource, the route must first fetch that target row with `user.selected_organization_id` and reject if it does not belong to the org.
- Services called from routes must preserve scoping. If a service method or DB client call drops `organization_id`, trace the caller.
- Background tasks do not get org context for free. They must reload the parent row and derive org from there.
- Webhooks must derive org from a signed or otherwise authenticated identifier, not from caller-supplied body fields like `organization_id`.
- New runtime code should use canonical `organization_id`, not `org_id`, `tenant_id`, or `organisation_id`.

Useful commands:

```bash
rg -n "_by_id\\(" api/routes api/services api/tasks
rg -n "db_client\\.get_\\w+\\(" api/routes api/services api/tasks
rg -n "organization_id|selected_organization_id" api/routes api/services api/tasks api/db
```

---

## 3. DB query layering (`api/db/` is the only home for SQL)

Production SQL belongs in `api/db/*_client.py`. Routes, services, and tasks should call DB client methods, not write SQLAlchemy directly.

Checks:

- `select`, `update`, `delete`, `insert`, `AsyncSession`, `sessionmaker`, or `async_session` in `api/routes/`, `api/services/`, or `api/tasks/` is a finding.
- `api/services/admin_utils/` is the exception. It is not a template for production code.
- Session lifecycle belongs inside the DB client.
- New DB client parameters should use canonical `organization_id`.

Useful commands:

```bash
rg -n "(from sqlalchemy|AsyncSession|sessionmaker|async_session)" api/routes api/services api/tasks
```

---

## 4. Worker sync - multi-process state coherence (`api/services/worker_sync/`)

Production runs multiple workers. Per-process mutable caches become stale unless updates are broadcast.

Use `api/AGENTS.md` as the canonical summary.

Checks:

- A new module-level or class-level mutable cache written by an endpoint needs a `WorkerSyncManager` broadcast path.
- Local invalidation alone is not enough if other workers can still serve stale state.
- If a PR introduces a new cached object, the diff should usually contain all three:
  - the broadcast call
  - the event type or equivalent signal definition
  - the handler registration that reloads fresh state

---

## 5. Background tasks (`api/tasks/`, ARQ)

Checks:

- User-triggered enqueue paths must validate org ownership before enqueue.
- Tasks that accept IDs and reload rows must derive org from those rows, not assume shared context.
- Tasks must be idempotent or explicitly retry-safe.
- Only real task entrypoints belong in `api/tasks/arq.py::WorkerSettings.functions`.
- Secret logging rules from section 10 apply here too.

---

## 6. Migrations (`api/alembic/versions/`)

Checks:

- `upgrade()` and `downgrade()` should both exist and be meaningfully reversible unless the change truly cannot be reversed.
- Adding a `NOT NULL` column to a populated table needs a safe default or a backfill before the constraint.
- Tightening nullable to non-nullable needs the backfill before `alter_column(..., nullable=False)`.
- New JSON columns should match the table's existing JSON or JSONB conventions.
- Large backfills in a migration should be questioned; they often belong out-of-band.
- Indexes on large tables need concurrent-safe handling.
- Do not turn historical migration naming into a finding by itself. Review the migration being changed, not old untouched migration prose.

---

## 7. MCP server (`api/mcp_server/`)

Checks:

- New tools should use `authenticate_mcp_request()`, not reimplement API-key validation.
- New tool DB lookups should preserve org scoping just like REST routes.
- Tools that call external URLs must validate those URLs and consider SSRF.

---

## 8. Telephony and webhook handlers

Checks:

- New provider webhook flows should implement `verify_inbound_signature()` or the provider equivalent.
- Minimal pre-verification work may be required to identify the candidate config, but the route should not do unrelated workflow, user, or stateful work before verification.
- Org derivation should come from provider identifiers that are validated by the webhook auth flow, then the derived org/config should drive downstream lookups.
- A webhook must not trust raw body `organization_id`.
- If a webhook references a phone number, validate that the number exists for the derived org.

---

## 9. UI (`ui/`) - generated SDK only

Use `ui/AGENTS.md` as the canonical summary.

The frontend should talk to the backend through `ui/src/client/`. Raw `fetch` to internal `/api/v1/` routes is suspicious by default.

Checks:

- `fetch('/api/v1/...')` or `fetch(\`${backendUrl}/api/v1/...\`)` in app code is usually a finding unless the current code proves a narrow exception.
- Hardcoded backend URLs are a finding.
- Manual `Authorization` header construction in regular components is a finding; auth should be injected centrally.
- SDK calls fired before auth state is ready are a finding.
- Local interfaces that duplicate generated types are a finding.
- If backend API shape changed and the UI consumes it, `ui/src/client/` should usually change too.

Useful commands:

```bash
rg -n "fetch\\(['\"\\`].*api/v1" ui/src
rg -n "Authorization" ui/src
```

---

## 10. Logging, secrets, constants

Checks:

- New code should use Loguru, not stdlib `logging`.
- New `os.getenv(...)` outside `api/constants.py` is a finding.
- Do not log API keys, bearer tokens, credentials, full webhook bodies, or PII.

Common offender shapes:

- `logger.info(f"config: {config}")`
- `logger.debug(request_body)`
- Logging raw config or user configuration rows

---

## 11. Tests (`api/tests/`)

Checks:

- Async waits in tests should use `asyncio.wait_for(...)` or another bounded timeout pattern.
- Tests should run against `.env.test`, not `.env`.
- Integration tests should not be neutered by replacing real DB behavior with mocks just to make the test pass.
- Tests that depend on mutable shared DB state across test cases are suspicious.

---

## 12. Schemas (`api/schemas/`)

Checks:

- New response schemas should not expose internal FKs or IDs unless the caller genuinely needs them.
- Request schemas that accept org-scoped FK values are a trigger to inspect the corresponding route for section 2 ownership validation.

---

## Final pass: shape the report

Present findings in three buckets:

- **Blocker**
  - Missing org scope on a request-reachable lookup
  - Route added without auth and without a proven deliberate public auth mechanism
  - Webhook without signature verification, or significant unrelated work done before verification
  - Migration without safe backfill or without a meaningful downgrade
  - UI bypasses generated SDK for internal API calls
  - Secrets logged

- **Should-fix**
  - Cached state mutated without worker sync
  - JSON vs JSONB inconsistency
  - Response schema leaks internal identifiers
  - Backend API changed without client regen where UI consumes it
  - Test path can hang indefinitely

- **Nit**
  - Naming inconsistencies
  - Minor convention drift
  - Low-risk schema or report-shape cleanup

Cite `file:line` for each finding. Skip anything a formatter, linter, or IDE would already catch unless it connects to one of the repo-specific risks above.
