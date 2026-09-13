# WhatsApp Business Outbound Calling (BIC) Architecture & Guide

This directory implements WhatsApp Business Voice Calling for the Dograh platform, supporting both **User-Initiated Inbound Calls (UIC)** and **Business-Initiated Outbound Calls (BIC)** over Meta's WhatsApp Cloud Calling API.

---

## 1. Overview: Why WhatsApp Outbound is Different

Traditional telephony providers (Twilio, Vonage, Plivo, Telnyx) dial directly over the Public Switched Telephone Network (PSTN) using E.164 phone numbers.

Meta's WhatsApp Business Calling API is fundamentally different:
1. **Zero PSTN carrier fees**: Audio streams end-to-end via WebRTC (OPUS 48 kHz).
2. **Explicit recipient consent gating**: Before an enterprise can place an outbound call to a WhatsApp user, the recipient **must explicitly grant calling permission** to the business in WhatsApp.
3. **Country restrictions**: Meta restricts business-initiated calls in certain jurisdictions (e.g. US, Canada, India).
4. **Asynchronous permission flow**: Permission is requested via interactive WhatsApp messages; permission grants and denials arrive asynchronously via webhooks.

---

## 2. User & Business Operator POV (Step-by-Step)

```
+---------------------------------------------------------------------------------------------------+
|                                  USER / OPERATOR WORKFLOW                                         |
+---------------------------------------------------------------------------------------------------+

 1. Configure Telephony           2. Launch Campaign / Call            3. Recipient Experience
    (Settings -> Telephony)          (Campaigns / Dial)                   (WhatsApp App)

  +-----------------------+        +-----------------------+            +-----------------------+
  | - Phone Number ID     |        | - Select WhatsApp cfg |            | User gets message:    |
  | - Access Token        | -----> | - Choose Action:      | ---------> | "Hi! We'd like to     |
  | - App Secret          |        |   • Skip unpermitted  |            |  speak with you..."   |
  | - Default Req Message |        |   • Request & Wait    |            | [Allow]     [Decline] |
  +-----------------------+        +-----------------------+            +-----------------------+
                                                                                    |
                                                                        +-----------+-----------+
                                                                        |                       |
                                                                     [Allow]                [Decline]
                                                                        |                       |
                                                                        v                       v
                                                               4a. Immediate Call      4b. Run Failed
                                                                   Recipient's phone       Lead marked as
                                                                   rings immediately       permission_denied;
                                                                   with AI agent.          campaign continues.
```

### Step 1: Telephony Configuration
1. Navigate to **Settings -> Telephony Configurations** in Dograh.
2. Click **Add Provider** and select **WhatsApp**.
3. Provide:
   - **Phone Number ID**: Your Meta WhatsApp Business Phone Number ID.
   - **Access Token**: Permanent System User Access Token with `whatsapp_business_messaging` and `whatsapp_business_management` permissions.
   - **App Secret**: App secret from Meta App Dashboard (used to cryptographically verify webhook signatures).
   - **Webhook Verify Token**: Random secret matching your Meta Webhook configuration.
   - **Default Permission Request Message**: The default crisp, polite invitation sent to users when requesting permission (e.g., *"Hi! We'd like to speak with you over a quick WhatsApp call. Please tap 'Allow' below to connect with us."*).
4. Toggle **Enable Business-Initiated Outbound Calls**.

### Step 2: Country Eligibility Check
When entering a phone number or launching a campaign, Dograh automatically checks country eligibility:
- If the destination country is blocked by Meta for outbound calling, Dograh informs the operator immediately, preventing failed calls and wasted time.

### Step 3: Launching an Outbound Campaign
When creating a campaign with WhatsApp telephony, the operator selects the **WhatsApp Permission Action**:
- **Skip unpermitted leads (`skip`)**: If permission is not already active, the lead is dispositioned as `no_permission` and skipped without blocking the campaign.
- **Request & Wait (`request_and_wait`)**:
  - If permission is active: dials immediately.
  - If permission is missing: sends the permission request message to the recipient's WhatsApp and **parks** the lead for up to 24 hours. The concurrency slot is released immediately so other leads can proceed.

### Step 4: What the Recipient Sees on WhatsApp
1. The user receives an official interactive WhatsApp message containing your permission request text.
2. Meta displays two native buttons below the message:
   - **Allow** (Grants call permission)
   - **Decline** (Denies permission)

### Step 5: What Happens Next
- **If the recipient taps "Allow"**:
  - Meta sends an instant webhook to Dograh.
  - Dograh captures the permission, immediately reactivates the parked campaign lead, and triggers dialing.
  - The recipient's phone rings with an incoming WhatsApp call from your verified business profile. When answered, the AI agent begins the conversation.
- **If the recipient taps "Decline"**:
  - Meta sends a denial webhook.
  - Dograh marks the lead as `permission_denied` and updates campaign progress (`processed_rows` and `failed_rows`).

---

## 3. Technical Architecture (Under the Hood)

### System Architecture Diagram

```
                             +-------------------------------+
                             |       Meta Graph API          |
                             +-------------------------------+
                                  ^                     |
               POST /messages     |                     | POST /webhook
               (request perm)     |                     | (reply/status/call)
                                  |                     v
+-----------------------------------------------------------------------------------+
|  Dograh Backend API                                                               |
|                                                                                   |
|  1. Webhook Authentication                                                        |
|     • _get_verified_config(phone_number_id)                                       |
|     • _verify_whatsapp_signature(HMAC-SHA256 with App Secret)                     |
|                                                                                   |
|  2. Event Routing                                                                 |
|     • field == "messages"               --> interactive call_permission_reply     |
|     • field == "user_call_permissions"  --> status change (granted/denied)        |
|     • field == "calls"                  --> WebRTC SDP offer/answer & signaling   |
|                                                                                   |
|  3. Campaign Reactive Engine                                                      |
|     • reactivate_campaign_runs_for_recipient()                                    |
|     • db_client.get_queued_runs_awaiting_whatsapp_permission(phone)               |
|     • db_client.activate_queued_run_for_immediate_dial()                          |
|     • enqueue_job("process_campaign_batch", campaign_id)                          |
+-----------------------------------------------------------------------------------+
        |                                       |
        v                                       v
+-----------------------------+       +-----------------------------+
|   PostgreSQL Database       |       |   Redis Event Bus & ARQ     |
|   • whatsapp_call_perm      |       |   • Pub/Sub: wa_perm_ch     |
|   • queued_runs (parked)    |       |   • ARQ Worker Queue        |
|   • campaigns (counters)    |       |                             |
+-----------------------------+       +-----------------------------+
```

---

## 4. Key Components & Implementation Details

### A. Database Models (`api/db/models.py`)
- **`WhatsAppCallPermissionModel` (`whatsapp_call_permissions`)**:
  - `organization_id`: Organization boundary.
  - `telephony_configuration_id`: Configuration ID.
  - `phone_number_id`: Meta business phone number ID.
  - `recipient_phone_number`: Clean E.164 phone number.
  - `status`: `pending`, `granted_temporary`, `granted_permanent`, `denied`, `revoked`.
  - `meta_message_id`: WhatsApp message ID (`wamid.HBg...`) correlating outbound request to recipient reply.
  - `expires_at`: UTC expiration timestamp.
  - `granted_at`: Timestamp when recipient approved.

- **`QueuedRunModel` (`queued_runs`)**:
  - `retry_reason = "awaiting_whatsapp_permission"`: Signifies that this run is parked waiting for the recipient's WhatsApp permission response.
  - `scheduled_for = now + 24 hours`: Timeout fallback.

### B. Safe Expiration Parsing (`api/services/telephony/providers/whatsapp/config.py`)
Meta can return permission expiration in varied formats (integer unix timestamps, numeric strings, or ISO-8601 strings like `"2026-09-18T12:37:07Z"`).
- `parse_whatsapp_expiration(expiration: Any) -> Optional[datetime]`: Normalizes all valid formats to UTC `datetime` safely without raising exceptions.

### C. Webhook Authentication & Security (`api/services/telephony/providers/whatsapp/routes.py`)
To prevent spoofing or unauthorized permission tampering:
- **`_get_verified_config(phone_number_id)`**:
  - Resolves the active configuration from the database.
  - If missing: rejects with `400 Bad Request` or `404 Not Found`.
  - Extracts the configured `app_secret` and computes `HMAC-SHA256(raw_body, app_secret)`.
  - Compares with the `X-Hub-Signature-256` header using timing-attack resistant `hmac.compare_digest`.
  - Signature validation protects **all** event types: `messages`, `user_call_permissions`, and `calls`.

### D. Campaign Call Dispatcher (`api/services/campaign/campaign_call_dispatcher.py`)
During outbound campaign execution:
1. `dispatch_call()` checks recipient permission.
2. If unpermitted and `whatsapp_permission_action == "request_and_wait"`:
   - Calls `provider.send_call_permission_request(to_number, org_id, config_id)`.
   - Persists a pending record with `meta_message_id`.
   - Parks the `QueuedRunModel` with `retry_reason = "awaiting_whatsapp_permission"` and `scheduled_for = now + 24h`.
   - Releases concurrency slot so subsequent campaign rows continue.

### E. Reactive Webhook & Lead Reactivation (`api/services/telephony/providers/whatsapp/routes.py`)
When recipient taps **Allow**:
1. Meta sends `field: "messages"` with `type: "interactive"` and `call_permission_reply: {response: "accept"}` (or `field: "user_call_permissions"`).
2. The webhook handler verifies signature and updates `whatsapp_call_permissions`.
3. Calls `reactivate_campaign_runs_for_recipient(clean_phone, status)`:
   - Calls `db_client.get_queued_runs_awaiting_whatsapp_permission(clean_phone)`.
   - Activates run immediately via `db_client.activate_queued_run_for_immediate_dial(q_run.id)` (`scheduled_for = None`, `retry_reason = "permission_granted"`).
   - Enqueues `process_campaign_batch` immediately via ARQ worker.

When recipient taps **Decline**:
1. Meta sends reply with `response: "decline"` / `status: "denied"`.
2. Calls `db_client.fail_queued_run_permission_denied(q_run.id)`:
   - Transitions `run.state = "failed"` and `retry_reason = "permission_denied"`.
   - Dispositions workflow run as `PERMISSION_DENIED`.
   - Atomically increments parent `campaign.processed_rows` and `campaign.failed_rows`.

### F. SQL-Level Filtering & Robust Phone Matching (`api/db/campaign_client.py`)
To ensure high scalability and robustness against user input formatting:
- Candidate matching is filtered at the database level using `QueuedRunModel.context_variables["phone_number"]` and suffix ILIKE conditions.
- In-memory validation canonicalizes numbers using `normalize_telephony_address(num).canonical` and digit extraction, seamlessly matching numbers formatted with spaces, parentheses, or dashes (`+1 (555) 123-4567` matches `15551234567`).

---

## 5. WebRTC Voice Media Pipeline

When a call is initiated or received:
1. Signaling takes place via Meta Graph API `/calls` endpoints using SDP offer and answer.
2. Media session connects to Dograh's voice engine using `WhatsAppTransport` (`transport.py`).
3. Audio is decoded from OPUS (48 kHz) into standard PCM frames for the Pipecat pipeline (STT -> LLM -> TTS).
4. Audio recording is synchronized to MinIO / S3 storage.
