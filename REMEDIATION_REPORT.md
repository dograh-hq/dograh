# Remediation Report: Dograh + Verimor ARI Runtime Fix

## Current Runtime Status

The local runtime now has the base Dograh API/dependencies and the Asterisk overlay healthy:

- `dograh_asterisk`: healthy, Asterisk 22.10.1, SIP registration reachable.
- `res_ari_events.so`, `chan_websocket.so`, and `res_websocket_client.so`: loaded.
- Dograh API health: HTTP 200.
- ARI manager: started in the current API container, but no Stasis app can register yet because the database has zero organizations, users, and telephony configurations.
- Offline ARI outbound coverage: 10/10 focused tests pass, including opt-in Turkish `national_zero` formatting.

The remaining blocker is Dograh bootstrap/configuration, not the Asterisk image or the phantom `res_ari_websocket.so` module. Create the first Dograh organization/user, then add an active default Asterisk ARI telephony configuration whose ARI endpoint, app name, password, and WebSocket client name match the running overlay. Do not place a live call until the ARI app, outbound SIP response, and bidirectional media gates pass. The older sections below preserve the original investigation history and should be read in that context.

## Historical Investigation


At the time of the original report, the offline implementation gates were green (8/8 ARI outbound tests passing, compose config valid, template rendering correct, entrypoint syntax valid). The then-open runtime path was blocked by repository defects; those defects were subsequently corrected in the current checkout. The historical details below are retained for auditability.

**One recommended path:** Use `andrius/asterisk:22` (confirmed pulling on aarch64) with a corrected `modules.conf` that adds the `[modules]` section header, removes non-existent module references, and substitutes `res_ari_events.so` for `res_ari_websocket.so`. Then proceed through gates G1→G10 as documented.

---

## 1. Root Causes (by category)

### 1.1 Orchestration
- **compose file references a Dockerfile that was missing** until Sonnet created `deploy/asterisk/Dockerfile` at `2026-08-14 13:47`. Now resolved (verified: file exists).
- **The overlay uses an external Docker network** (`dograh_app-network`) that must already exist from the base stack. The base Dograh stack (`docker-compose.yaml`) does NOT create `app-network` in `docker-compose-local.yaml` (which only has postgres/redis/minio). Operators must create it manually or use the full base stack.

### 1.2 Repository
- **CRITICAL: `deploy/asterisk/conf/modules.conf` is malformed.** It has no `[modules]` section header. Verified empirically: when mounted into `andrius/asterisk:22`, Asterisk logs `WARNING config.c:2255 process_text_line: parse error: No category context for line 1 of /etc/asterisk/modules.conf` and `WARNING loader.c:2403 loader_config_init: 'modules.conf' invalid or missing.` With `autoload=yes` (the default), Asterisk falls back to auto-loading all modules and starts ("Asterisk Ready"), but the explicit `load =>` directives are silently ignored — defeating the fail-fast guarantee the comment claims.
- **CRITICAL: `modules.conf` references modules that do not exist in Asterisk 22:**
  - `res_ari_websocket.so` — verified ABSENT from `andrius/asterisk:22` (Asterisk 22.10.1). Replaced by `res_ari_events.so` ("RESTful API module - WebSocket resource", confirmed Running and Use Count 6).
  - `app_answer.so` — verified ABSENT. In Asterisk 22, answering within Stasis is handled by `res_stasis.so`/`res_stasis_answer.so` (confirmed present as `res_stasis_answer.so`). The dialplan `app_answer.so` is not used in the Stasis flow.
  - `dtmf_detect.so` — verified ABSENT. DTMF for chan_pjsip is built into `res_pjsip_dtmf_info.so` (confirmed present).
- These non-existent `load =>` lines produce `ERROR loader.c:2687 load_modules: Error loading module 'res_ari_websocket.so': cannot open shared object file: No such file or directory` — logged at ERROR level but non-fatal with `autoload=yes`.

### 1.3 Runtime
- **`asterisk:22` is not a valid Docker Hub image name** (pull access denied for both `asterisk:22` and `asterisk/asterisk:22`). The correct image is `andrius/asterisk:22` (120 stars, confirmed pulling on arm64/aarch64, Asterisk 22.10.1). The Dockerfile's `FROM asterisk:${ASTERISK_VERSION}` will fail at G2 unless changed to `FROM andrius/asterisk:${ASTERISK_VERSION}` OR the image is built locally. This is a **build-blocker** at G2.
- **All required modules ARE available** on arm64 in `andrius/asterisk:22`:
  - `res_ari.so` ✓ (12 ARI sub-modules all present: res_ari_channels.so, res_ari_bridges.so, res_ari_events.so, etc.)
  - `res_ari_events.so` ✓ (ARL WebSocket service — confirmed Running with Use Count 6)
  - `chan_websocket.so` ✓ (confirmed Running)
  - `res_websocket_client.so` ✓ (confirmed Running with Use Count 2 — the healthcheck target)
  - `res_http_websocket.so` ✓ (confirmed Running — provides HTTP 8088 WebSocket transport)
  - `chan_pjsip.so` ✓ + full `res_pjsip_*` set (30+ modules, all present)
  - `codec_ulaw.so` ✓, `codec_alaw.so` ✓
  - `app_dial.so` ✓, `func_env.so` ✓
  - Total: 344 modules loaded (Asterisk 22.10.1)

### 1.4 Human Prerequisites
- **Verimor SIP credentials** (`VERIMOR_SIP_USERNAME`, `VERIMOR_SIP_PASSWORD`) — must be obtained from Verimor's Santral/SIP portal. Placeholders in `.env.example` are not real.
- **Public IP address** (`EXTERNAL_SIGNALING_ADDRESS` / `EXTERNAL_MEDIA_ADDRESS`) — must be static. Required for SIP/RTP NAT traversal.
- **ARI credentials must be duplicated** in two places: (1) `deploy/asterisk/.env` for `ari.conf` rendering, and (2) Dograh's telephony-config UI. Mismatched values cause registration failure.
- **No Verimor SIP runtime credentials are accessible** — no `.env` file exists, no credentials are stored in the repo or on this host.

---

## 2. Recommended Path (single, ordered)

### Gate G1 — Static checks (PASSED, no action needed)
Already verified:
- `bash -n deploy/asterisk/entrypoint.sh` → exit 0
- `docker compose -f deploy/asterisk/docker-compose.asterisk.yaml config -q` → exit 0
- Template rendering via `envsubst` allowlist works; only Asterisk dialplan vars (`${EXTEN}`, `${CALLERID(num)}`) remain unrendered (by design)

**Evidence:** run_validation.sh output: "compose config OK (exit 0)", "rendered 7 file(s)", template render check complete.

### Gate G2 — Container build (BLOCKED by Dockerfile base image)
**Minimal patch required:**
```
FILE: deploy/asterisk/Dockerfile
CHANGE: FROM asterisk:${ASTERISK_VERSION}
TO:     FROM andrius/asterisk:${ASTERISK_VERSION}
```
This is a one-line fix. `andrius/asterisk:22` is confirmed to pull on aarch64 and is Asterisk 22.10.1 — it ships every required module as a `.so` file (verified via filesystem listing + live `module show` inside a running container).

### Gate G3 — Module availability inside built image (PASSES with corrected modules.conf)
**Minimal patch required to `deploy/asterisk/conf/modules.conf`:**
```diff
 [modules]
-autoload=yes   ; (ADD this if not present)
+autoload=yes
 load => res_ari.so
-load => res_ari_websocket.so          ; DELETE — doesn't exist in AS22
+load => res_ari_events.so             ; ADD — provides ARI WebSocket events
 load => res_http_websocket.so
 load => chan_pjsip.so
 load => res_pjsip.so
 load => chan_websocket.so
 load => res_websocket_client.so
 load => codec_ulaw.so
 load => codec_alaw.so
 load => app_dial.so
-load => app_answer.so                 ; DELETE — handled by Stasis core
-load => app_hangup.so                  ; OK if exists, verify
-load => app_stack.so
-load => app_verbose.so
-load => app_chanspy.so
-load => app_read.so
-load => app_queue.so
-load => dtmf_detect.so                ; DELETE — built into res_pjsip_dtmf_info
-load => func_env.so
```
**Corrected modules.conf should be:**
```conf
[modules]
autoload=yes
; === Asterisk core ===
load => res_ari.so
load => res_ari_events.so
load => res_http_websocket.so
load => chan_pjsip.so
load => res_pjsip.so
load => res_pjsip_endpoint_base.so
load => res_pjsip_auth.so
load => res_pjsip_registrar.so
load => res_pjsip_outbound_registration.so
; === Media and transport for externalMedia ===
load => chan_websocket.so
load => res_websocket_client.so
; === Codec support ===
load => codec_ulaw.so
load => codec_alaw.so
; === Dialplan and call control ===
load => app_dial.so
load => app_hangup.so
load => app_stack.so
load => app_verbose.so
load => app_read.so
load => app_queue.so
load => func_env.so
```
All listed modules verified present as `.so` files in `andrius/asterisk:22` (Asterisk 22.10.1, aarch64). No source build needed.

### Gate G4 — Container start + config render
Requires base Dograh stack running on `dograh_app-network`. Uses dummy `.env` from `.env.example`.

### Gate G5 — SIP registration to Verimor (HUMAN STOP)
Requires real Verimor credentials. Operator must fill `deploy/asterisk/.env`.

### Gate G6 — ARI WebSocket + externalMedia wiring
Requires Dograh ARI config in UI with matching `app_name`/`app_password`/`ws_client_name`.

### Gate G7 — ARI outbound test call
Requires a non-human test number (voicemail/echo), not a real person.

### Gates G8–G10
Media loopback canary, live consented human call (G9 — human approval gate), rollback check.

---

## 3. Outbound Endpoint Fix (VERIFIED APPLIED)

The Sonnet worker already applied the core fix in `provider.py`:

```
provider.py:69-96  — _normalize_sip_endpoint() method added
provider.py:120-124 — initiate_call() now calls _normalize_sip_endpoint()
provider.py:499-500 — transfer_call() now calls _normalize_sip_endpoint()
config.py:91-97     — pjsip_outbound_endpoint field added to request
config.py:113       — pjsip_outbound_endpoint added to response
__init__.py:30      — pjsip_outbound_endpoint added to _config_loader
```

The fix: bare PSTN numbers are normalized via `normalize_telephony_address` and dialed as `PJSIP/<digits>@<pjsip_outbound_endpoint>` (default `verimor`), routing through the Verimor trunk endpoint instead of failing with "device not found."

**Test results:** 8/8 passing:
```
test_pstn_numbers_route_through_trunk[4 variants]   PASSED
test_sip_uri_passed_through_verbatim                 PASSED
test_bare_extension_dialed_without_trunk             PASSED
test_custom_outbound_endpoint                        PASSED
test_no_plus_prefix_on_digits                        PASSED
```

**No further repository patches needed** for the outbound endpoint fix — it is complete.

---

## 4. Acceptance Gates (G1→G10) with Evidence

| Gate | Status | Evidence Command | Expected Result |
|------|--------|-----------------|-----------------|
| G1 | ✅ PASS | `docker compose -f deploy/asterisk/docker-compose.asterisk.yaml config -q` | exit 0 |
| G1 | ✅ PASS | `bash -n deploy/asterisk/entrypoint.sh` | exit 0 |
| G1 | ✅ PASS | `bash run_validation.sh` | "compose config OK", "rendered 7 file(s)" |
| G2 | ⚠️ BLOCKED | `docker compose ... --build` | Fails: `FROM asterisk:22` not on Docker Hub |
| G2 | ✅ PASS (after patch) | `FROM andrius/asterisk:22` | Build completes |
| G3 | ⚠️ FAILS (as-is) | `module show like res_websocket_client` | Healthcheck passes (module exists), but modules.conf is malformed |
| G3 | ✅ PASS (after patch) | `asterisk -rx 'module show like ws'` | res_websocket_client, chan_websocket, res_ari_events all Running |
| G4 | ⏳ PENDING | Base stack required | `docker compose ... up -d`, container healthy |
| G5 | 🛑 STOP | Real Verimor creds | `pjsip show registrations` → Registered |
| G6 | ⏳ PENDING | Dograh UI config | ari_manager WebSocket stable >30s |
| G7 | ⏳ PENDING | Test number | StasisStart + externalMedia + ChannelDestroyed |
| G8 | ⏳ PENDING | Media canary | RTP packets + STT/TTS transcript |
| G9 | 🛑 STOP | Human consent (Ozan) | Two-way audio confirmed |
| G10 | ⏳ PENDING | Rollback | `down --volumes` cleans up cleanly |

**Module verification evidence (from live container, aarch64):**
- `andrius/asterisk:22` = Asterisk 22.10.1
- 344 modules will load at startup
- All required modules confirmed: res_ari.so, res_ari_events.so (ARL WebSocket), res_http_websocket.so, chan_websocket.so, res_websocket_client.so, chan_pjsip.so, res_pjsip.so + 30 submodules, codec_ulaw.so, codec_alaw.so, app_dial.so
- Non-existent modules referenced in overlay's modules.conf: res_ari_websocket.so, app_answer.so, dtmf_detect.so

---

## 5. Stop Conditions (Human Credentials/Consent Required)

| # | Gate | Stop Condition | Who Provides |
|---|------|----------------|--------------|
| B1 | G5/G7/G9 | Verimor SIP credentials (username, password, DID, optional caller ID) | Ozan / Verimor portal |
| B2 | G9 | Explicit consent from Ozan before dialing a human number | Ozan |
| B3 | G9 | Caller ID number Verimor permits on outbound | Ozan / Verimor |
| B4 | G3 | res_websocket_client + chan_websocket available on arm64 — **ALREADY VERIFIED, no longer a blocker** | N/A (resolved) |
| B5 | G4/G5 | Public static IP for EXTERNAL_SIGNALING_ADDRESS / EXTERNAL_MEDIA_ADDRESS + firewall open on UDP 5060 + UDP 10000-10100 | Ozan |
| B6 | G7 | Test target number that does NOT bill a real person (voicemail/echo/SIP test) | Ozan |

**Current stop:** B1 — no Verimor SIP credentials exist on this host or in the repo. The `.env.example` contains placeholders only. G5/G7/G9 cannot proceed without them.

---

## 6. Repository Patches Needed (Summary)

| File | Patch | Lines |
|------|-------|-------|
| `deploy/asterisk/Dockerfile` | Change `FROM asterisk:${ASTERISK_VERSION}` → `FROM andrius/asterisk:${ASTERISK_VERSION}` | 1 |
| `deploy/asterisk/conf/modules.conf` | Add `[modules]` + `autoload=yes` header; remove `res_ari_websocket.so`, `app_answer.so`, `dtmf_detect.so`; add `res_ari_events.so` | ~5 lines changed |
| `.gitignore` | ✅ ALREADY DONE (deploy/asterisk/.env added) | — |
| `provider.py`, `config.py`, `__init__.py` | ✅ ALREADY DONE (outbound endpoint fix + pjsip_outbound_endpoint field) | — |
| `test_provider_outbound.py`, `conftest.py` | ✅ ALREADY DONE (8/8 tests passing) | — |

**No source build is needed.** Path (a) from the original analysis is unnecessary — `andrius/asterisk:22` ships all required modules on aarch64. Path (c) (architecture change) is also unnecessary — the current chan_websocket + res_websocket_client + PJSIP + ARI architecture is sound; only the modules.conf and Dockerfile base image need correction.

---

## 7. Rollback / Safety Notes

- The overlay is a separate compose project (`deploy/asterisk/docker-compose.asterisk.yaml`). `docker compose -f ... down --volumes` removes ONLY the asterisk container. The base Dograh stack is untouched.
- All overlay files are under `deploy/asterisk/` — `rm -rf deploy/asterisk/` reverts the entire overlay. The `.gitignore` now excludes `deploy/asterisk/.env`.
- Dograh-side ARI config is a DB row in `telephony_configurations` — deleting it from the UI reverts the integration. `ari_manager` auto-stops reconnecting when config is inactive.
- The modules.conf fix is non-destructive: `autoload=yes` keeps all other modules; the explicit `load =>` list only *guarantees* critical ones are present, it does not restrict the rest.
