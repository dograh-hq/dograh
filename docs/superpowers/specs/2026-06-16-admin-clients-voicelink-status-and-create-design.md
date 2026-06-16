# Admin Clients — live VoiceLink status + one-click create — Design

- **Date:** 2026-06-16
- **Status:** Approved (pending spec review)
- **Branch:** `feat/voicelink-saas`
- **Area:** `api/routes/admin_clients.py`, `api/services/voicelink_clients/`, `api/db/`, `ui/src/app/clients/`

## Problem

The superuser admin **Clients** page (`/clients`) is meant to show each client org's VoiceLink
provisioning state and let an operator (re)create a missing client. Two real gaps:

1. **The status shown is our *stored guess*, not the truth.** The badge renders
   `organization.voicelink_status` (`provisioned` / `pending` / null → "Not provisioned"). That field
   can drift from reality: a signup that hit a *silent skip* (reseller creds unset, e.g. the "amit"
   case) shows "Not provisioned" with no client, **and** a client can exist in VoiceLink while our DB
   says `pending` (the success-misread bug patched in `8aa72c2`). The operator cannot tell from the
   page whether the client *actually exists in VoiceLink right now*.

2. **Recreating a client has password friction.** The existing **Retry** action
   (`POST /admin/clients/{org}/retry-provision`) requires the admin to *type a new VoiceLink password*
   every time, because `provision_voicelink_client` needs a password and we only keep a one-way bcrypt
   hash of the user's platform password. The operator wants to *press one button* and have the client
   created — with the **same password the user signed up with**.

## Goals

- Show, per row, whether the client **actually exists in VoiceLink** (live), reconciled against our
  stored state.
- A **one-click "Create client"** action that (re)provisions a missing client using the **user's
  platform password**, with no typing.
- Self-heal stored state when the live check finds a client we lost the link to.

## Non-goals (YAGNI)

- No per-row background polling or scheduled sync job — reconcile happens on page load only.
- No bulk "create all" action.
- No change to how *telephony* credentials are stored (they remain plaintext-in-JSONB, masked on
  read — see Security note). This spec encrypts only the new provisioning secret.
- No inbound-call work (out of scope; tracked separately).

## Current state (verified)

- **Org columns** (`api/db/models.py:112-115`): `voicelink_client_id` `String(64)`,
  `voicelink_username` `String(128)`, `voicelink_status` `String(32)`, `voicelink_error` `Text`.
- **Update helper** `update_organization_voicelink(...)` (`api/db/organization_client.py:134`) uses a
  `_UNSET` sentinel — only passed kwargs are written; explicit `None` clears a field.
- **Admin endpoints** (`api/routes/admin_clients.py`): `GET /admin/clients` (list, stored status),
  `POST /{org}/retry-provision` (requires `password`), `POST /{org}/assign-did`. Superuser-gated via
  `Depends(get_superuser)`.
- **Provisioning** (`api/services/voicelink_clients/service.py`):
  `provision_voicelink_client(org, *, email, password, name?, username?, client?)` calls VoiceLink
  `create_client` and writes the outcome to the org (`provisioned` + client_id, or `pending` + error).
  `provision_voicelink_client_for_signup(...)` is the best-effort signup hook that *skips* (returns
  early, writes nothing) for ADMIN_EMAILS users and when reseller creds are unset.
- **Reseller client** `VoiceLinkClientsClient` (`.../voicelink_clients/client.py`) extends
  `VoiceLinkKycClient`; `is_configured == bool(username and password)` from
  `VOICELINK_RESELLER_USERNAME`/`VOICELINK_RESELLER_PASSWORD`. There is **no `list_clients` method yet**.
- **No reversible-crypto util exists** in `api/`. `cryptography` is not pinned in `api/requirements`.
- **UI** (`ui/src/app/clients/page.tsx`, `ui/src/lib/adminClients.ts`): table with a
  `VoiceLinkStatusBadge` off `voicelink_status`, a **Retry** dialog (password), Assign-DID, Impersonate.

The live VoiceLink response shapes are confirmed against the live API (2026-06-16):
`GET /v1/reseller/clients` → `{status, message, data:[{id, username, email, ...}]}`.

## Design

### A. Reversible provisioning secret

The user's platform password is available in plaintext **only at signup**. To reuse the *same*
password at a later admin-triggered create, store a reversible copy.

- **New column** `organizations.voicelink_provision_secret` `Text` nullable. Holds a **Fernet-encrypted**
  copy of the user's signup password.
- **Encryption util** — new `api/services/voicelink_clients/secrets.py`:
  `encrypt_provision_secret(plaintext) -> str` / `decrypt_provision_secret(token) -> str | None` using
  `cryptography.fernet.Fernet` with key from env **`VOICELINK_PROVISION_KEY`** (a urlsafe-base64 32-byte
  Fernet key). If the key is unset, encryption is a no-op that stores nothing and logs once (feature
  degrades to the password-prompt fallback — never crashes). Add `cryptography` to `api/requirements`.
- **Write rules** (bounded exposure — the secret only ever exists for not-yet-provisioned orgs):
  - In `provision_voicelink_client`: on outcome `provisioned` → **clear** the secret (`None`). On
    `pending` → **store** `encrypt(password)`.
  - In `provision_voicelink_client_for_signup`: on the **reseller-creds-unset** skip → **store**
    `encrypt(password)` (this is the amit case: lets a later one-click create reuse the password once
    creds are set). On the **ADMIN_EMAILS** skip → store nothing (that org is intentionally never a
    client).
- `update_organization_voicelink(...)` gains a `provision_secret=_UNSET` kwarg.

### B. Live status reconciliation (`GET /admin/clients`)

- Add `list_clients()` to `VoiceLinkClientsClient` → `GET /v1/reseller/clients`, returning the `data`
  list (raises `VoiceLinkClientError` on failure).
- In `list_clients` route: if reseller `is_configured`, make **one** `list_clients()` call and build two
  in-memory indexes — by `id` (string) and by `username`. For each org compute `live_state`:
  - **`active`** — matched. Match precedence: `voicelink_client_id` → `voicelink_username` → derived
    `derive_username(owner_email, org_id)`. *Email is never used as a match key — it repeats across
    clients.* Include `live_client_id` from the matched record.
  - **`missing`** — reseller configured, no match.
  - **`unconfigured`** — reseller creds unset (no call made).
  - **`unknown`** — the reseller call raised; fall back to stored status, never 500 the page.
- **Auto-heal:** when `active` but our DB `voicelink_client_id` is absent or differs, write the
  discovered `client_id` + `status=provisioned` + clear `error` + clear `provision_secret` (the
  `8aa72c2` drift fix; clearing the secret keeps the "secret only for not-yet-provisioned orgs"
  invariant from §A).
- **New `AdminClientItem` fields:** `live_state: "active"|"missing"|"unconfigured"|"unknown"`,
  `live_client_id: Optional[str]`. Existing `voicelink_status`/`voicelink_error` remain (shown as the
  stored value when it differs from live).

### C. One-click create (`POST /admin/clients/{org}/create`)

New endpoint, superuser-gated. Body: none (optional `password` accepted only as an explicit override).

1. Load org (`get_organization_with_users`). 404 if missing.
2. Reseller must be configured → else `503` (same message as retry-provision).
3. **Reconcile first:** call `list_clients()`; if a matching client already exists →
   **link, don't duplicate**: write `client_id` + `status=provisioned` + clear `error` + clear
   `provision_secret`; return `{status: "linked", ...}`. (Avoids a duplicate-username 422.)
4. Else **create:** resolve the password — `password` from body if provided, else
   `decrypt_provision_secret(org.voicelink_provision_secret)`. If neither is available →
   `409` with a message telling the operator to use the password dialog (Retry). Otherwise call
   `provision_voicelink_client(org, email=owner.email, password=<resolved>, username=org.voicelink_username)`.
   On `provisioned`, the secret is cleared by rule A. Return the provisioning result.
5. **Retry-provision endpoint stays** as the explicit "type a password" fallback (legacy orgs with no
   stored secret, e.g. orgs that signed up before this ships).

### D. UI (`/clients`)

- `AdminClient` type + `adminClients.ts` gain `live_state` / `live_client_id`; add `createClient(token, orgId)`.
- `VoiceLinkStatusBadge` renders from **`live_state`**: **Active in VoiceLink** (green, shows
  `live_client_id`) / **Missing** (red) / **Not configured** (grey) / **Unknown** (amber). When the
  stored `voicelink_status`/`voicelink_error` disagrees with live, show it in the tooltip.
- Primary action becomes **"Create client"** (one click, no dialog) shown whenever `live_state !=
  active`; on click → `createClient` → toast → refetch. The existing **Retry** (password) dialog is
  demoted to a fallback (shown e.g. when create returns `409 needs-password`).
- Auto-check is automatic — the page already fetches on load; the list endpoint now returns live state.

## Data model / migration

- Alembic migration: add `organizations.voicelink_provision_secret TEXT NULL`. No backfill.

## Security considerations

- **The tradeoff (approved):** a **reversible** copy of the user's signup password is stored at rest,
  Fernet-encrypted under a dedicated `VOICELINK_PROVISION_KEY`, **only for not-yet-provisioned orgs**,
  and **wiped on successful provisioning**. This is what enables "same password as platform, one-click"
  — a bcrypt hash cannot be reversed.
- The encryption key lives only in env (`VOICELINK_PROVISION_KEY`), never in the DB. If the key is
  unset, the secret is never written and create falls back to the password prompt.
- The plaintext password is still **never logged** (existing guarantee preserved).
- **Known, out-of-scope inconsistency:** telephony-config credentials (incl. any stored VoiceLink
  password) remain plaintext-in-JSONB, masked on read only. Encrypting those is a separate effort and
  not expanded here.
- All new endpoints are `Depends(get_superuser)`.

## Testing plan (TDD)

Backend (`api/tests/`, mock the reseller HTTP layer per `test_admin_clients_routes.py` /
`test_voicelink_clients.py`):

- **secrets util:** encrypt→decrypt round-trips; `decrypt(None)`→`None`; key-unset → encrypt no-ops.
- **provision write rules:** success clears the secret; pending stores it; signup creds-unset skip
  stores it; admin-email skip stores nothing.
- **reconcile:** match by client_id / username / derived username; `missing`; `unconfigured` (no call);
  reseller-error → `unknown` + stored fallback (no 500); auto-heal writes the discovered client_id.
- **create endpoint:** link-when-exists (no createClient call); create-when-missing using the stored
  secret; `409` when no secret + no body password; `503` when reseller unset; `404` unknown org;
  superuser-only (non-superuser → 404/403 per existing convention).

UI: minimal — badge renders each `live_state`; "Create client" calls the endpoint and refetches. (Match
the repo's existing UI test depth.)

## Rollout / ops

- Set `VOICELINK_PROVISION_KEY` (Fernet key) in the VPS API env before deploy; without it the one-click
  create degrades to the password prompt (no crash).
- Existing failed orgs (e.g. amit) have no stored secret yet → use the password-prompt fallback once, or
  they self-heal via reconcile if their client is created out-of-band. New signups get one-click.
