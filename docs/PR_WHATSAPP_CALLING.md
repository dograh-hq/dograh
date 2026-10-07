# Pull Request: WhatsApp Outbound Calling (Business-Initiated Calling & Permission Management)

> **Prerequisite Notice for Reviewers**:  
> This pull request builds upon the **WhatsApp Inbound Calling** PR (`feature/whatsapp-inbound-calling`). Please review and merge the Inbound Calling PR first. This PR is strictly scoped to adding **Outbound Business-Initiated Calling (BIC)**, recipient permission management, and campaign dispatch orchestration on top of that foundation.

---

## 1. Summary

While the prerequisite Inbound PR introduced the base WhatsApp telephony provider for user-initiated incoming calls, this pull request implements **Business-Initiated Calling (BIC)**. 

Because Meta platform regulations require explicit recipient consent prior to initiating outbound WhatsApp calls, this PR introduces:
1. **Destination Policy Restriction Engine**: Enforces Meta country restrictions prior to initiating calls.
2. **Permission Gating and Lifecycle State Machine**: Tracks temporary (24-hour) and permanent call permissions.
3. **Interactive Permission Request Dispatching**: Enables businesses to send interactive WhatsApp messages requesting call permission.
4. **Campaign Lead Parking and Automated Reactivation**: Parks leads awaiting permission and reactivates them via an automated ARQ periodic sweep task when consent is granted.
5. **Architectural Decoupling (`service.py`)**: Establishes a dedicated service layer for active WebRTC connections, client caching, and Redis pub/sub, removing all provider dependencies on HTTP route handlers.
6. **Frontend Outbound Controls**: Adds real-time permission evaluation, custom permission message previews, in-dialog call management (live duration timer, hangup), and campaign permission settings.

---

## 2. Architecture and Technical Design

### 2.1 Provider Decoupling via Dedicated Service Layer (`service.py`)
In the initial implementation, `WhatsAppProvider` imported internal connection globals and helpers directly from `routes.py`. This PR decouples the architecture:
- **`service.py`**: Encapsulates:
  - Active WebRTC connection registry (`_active_connections`).
  - Outbound answered events (`_outbound_answered_events`).
  - Cross-worker Redis pub/sub synchronization (`whatsapp:call:terminate`, `whatsapp:call:events`).
  - Reusable HTTP client sessions and cached `WhatsAppClient` instances.
  - Pipeline runner registration (`set_pipeline_runner`), enabling the provider to register active calls without directly importing route modules.
- **`provider.py`**: Imports exclusively from `service.py`. It initiates outbound calls via WebRTC after validating country eligibility and checking local recipient permission records.

### 2.2 Destination Country Policy Restrictions (`restrictions.py`)
Meta strictly restricts Business-Initiated WhatsApp Calls in specific jurisdictions:
- United States and Canada (`+1`)
- Egypt (`+20`)
- Vietnam (`+84`)
- Nigeria (`+234`)

`restrictions.py` validates destination numbers before any API call is made. Phone numbers belonging to restricted jurisdictions fail fast with `DestinationCountryRestrictedError`. The frontend queries the backend `/permissions/check` endpoint directly to display policy notices, ensuring a single source of truth without duplicated client-side prefix matching.

### 2.3 Recipient Permission Lifecycle and Concurrency-Safe Upserts
Outbound calls require an active permission record (`granted_temporary` or `granted_permanent`):
- **Phone Number Normalization**: Queries evaluate numbers across canonical E.164, raw digits, leading `+`, and local variants to prevent duplicate rows or missed permissions across formatting differences.
- **Interactive Messaging**: Dispatches Meta interactive messages with `call_permission_request` payloads when permission is absent or expired.
- **Concurrency Protection**: `upsert_whatsapp_call_permission` handles PostgreSQL unique constraint collisions (`uq_whatsapp_perm_config_recipient`) caused by simultaneous webhook deliveries. On conflict, the transaction rolls back and safely updates the existing record.

### 2.4 Campaign Dispatcher and Orchestrator Integration
- **Polymorphic Exception Handling**: Defined `TelephonyPermissionRequiredError` in `api/services/telephony/base.py`. `WhatsAppPermissionRequiredError` inherits from this class. `campaign_call_dispatcher.py` handles permission-gated calls polymorphically without importing provider-specific classes.
- **Lead Parking**: When a campaign has `whatsapp_permission_action = "request_and_wait"`, unpermitted leads receive a permission request and their run is parked with `scheduled_for = now + 24h` in `queued` state.
- **Campaign Completion Safety**: `campaign_orchestrator.py` checks total queued and processing runs before marking a campaign complete. Campaigns containing parked leads are prevented from completing prematurely while awaiting recipient responses.
- **Periodic Sweeper (`sweep_parked_whatsapp_permissions`)**: Registered an ARQ cron task running every 2 minutes in `WorkerSettings.cron_jobs` to poll for granted permissions and reactivate parked runs automatically.

---

## 3. Database Schema Changes

This PR includes two Alembic migrations:
1. **`e1a2b3c4d5e6_add_whatsapp_call_permissions.py`**:
   - Creates `whatsapp_call_permissions` table:
     - Columns: `id`, `organization_id`, `telephony_configuration_id`, `phone_number_id`, `recipient_phone_number`, `status`, `permission_type`, `meta_message_id`, `requested_at`, `granted_at`, `expires_at`, `updated_at`.
     - Unique constraint: `uq_whatsapp_perm_config_recipient` on `(telephony_configuration_id, recipient_phone_number)`.
     - Index: `ix_whatsapp_perm_lookup` on `(phone_number_id, recipient_phone_number)`.
2. **`b7d2f04c8a15_index_parked_queued_runs.py`**:
   - Adds composite index `ix_queued_runs_retry_reason_state` on `(retry_reason, state)` to optimize queries for parked leads during cron sweeps.

---

## 4. Frontend Enhancements

### 4.1 Phone Call Dialog (`PhoneCallDialog.tsx`)
- Replaces the placeholder tooltip on the call button with active outbound WebRTC calling logic.
- Evaluates recipient permission in real time via `/permissions/check`.
- Displays contextual permission badges (`granted`, `pending`, `not_requested`, `restricted`).
- Provides inline permission request dispatching with customized message text preview.
- Adds active call state progression: calling indicator, live duration counter, and hangup control.

### 4.2 Campaign Settings (`WhatsAppPermissionCard.tsx`)
- Provides UI configuration for campaign permission behavior:
  - `request_and_wait`: Sends permission request and parks the lead for up to 24 hours.
  - `skip`: Skips unpermitted leads immediately.

---

## 5. Pipecat Submodule Updates

Updates the `pipecat` submodule pointer to include:
- `WhatsAppClient.initiate_outbound_call`: Creates local SDP offer and sends it to Meta Graph API via action `"connect"`.
- `WhatsAppClient.check_call_permission`: Queries Meta's `call_permissions` API.
- `WhatsAppClient.send_call_permission_request`: Dispatches interactive permission messages.
- Connection track pre-allocation and audio lifecycle synchronization in `SmallWebRTCClient`.

---

## 6. Testing and Verification Guide

### 6.1 Automated Test Execution

```bash
# Outbound provider initiation and permission gating tests
pytest api/tests/telephony/whatsapp/test_provider.py

# Campaign dispatcher parking, completion protection, and sweeper cron tests
pytest api/tests/telephony/whatsapp/test_campaign_permissions.py

# Country restriction validation tests
pytest api/tests/telephony/whatsapp/test_restrictions.py

# Format-tolerant recipient matching tests
pytest api/tests/telephony/whatsapp/test_recipient_matching.py
```

### 6.2 Key Test Scenarios Covered
1. **Jurisdiction Restrictions**: Phone numbers with prefixes `+1`, `+20`, `+84`, and `+234` raise `DestinationCountryRestrictedError` and are rejected before placing any API call.
2. **Permission Gating**: Calling a recipient without an active permission record raises `WhatsAppPermissionRequiredError` (and `TelephonyPermissionRequiredError`).
3. **Database Concurrency**: Simulated concurrent webhook writes to `upsert_whatsapp_call_permission` verify rollback on `IntegrityError` and successful retry update.
4. **Campaign Completion Protection**: Campaigns with claimable count = 0 but parked runs count > 0 remain active.
5. **Periodic Sweeper**: Verified `sweep_parked_whatsapp_permissions` reactivates parked leads when permission is granted.
6. **Architecture Layering**: Verified `WhatsAppProvider` imports from `service.py` with zero imports from `routes.py`.

---

## 7. Reviewer Checklist

- [ ] **Prerequisite Verification**: Confirm the Inbound Calling PR is reviewed or merged first.
- [ ] **Alembic Migrations**: Review `e1a2b3c4d5e6` and `b7d2f04c8a15` under `api/alembic/versions/`.
- [ ] **Provider Layering**: Verify `provider.py` does not reference route handlers and delegates state to `service.py`.
- [ ] **Error Hierarchy**: Inspect `TelephonyPermissionRequiredError` in `api/services/telephony/base.py` and its handling in `campaign_call_dispatcher.py`.
- [ ] **Cron Registration**: Verify `sweep_parked_whatsapp_permissions` is registered in `api/tasks/arq.py` under `WorkerSettings.cron_jobs`.
- [ ] **UI Integration**: Verify `PhoneCallDialog.tsx` uses backend `/permissions/check` response without hardcoded client-side country lists.
