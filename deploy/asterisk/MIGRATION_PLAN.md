# Dograh Migration Master Plan — Self-Hosted Asterisk + Verimor SIP Trunk

Status: READ-ONLY planning artifact. No code in this document is executable for production.
Worker: Fable (planning). Children: t_194c2559 (Sonnet, implementer), t_bcb4b510 (Opus, reviewer).

## 0. Verified ground truth (read-only inspection)

All "FACT" bullets below were directly observed in the repo at
/home/ubuntu/workspace/dograh on branch feat/dograh-asterisk-verimor-overlay,
commit 66ab884d. "ASSUMPTION" marks things that must be confirmed against
Verimor / the operator before a billed call.

### 0.1 What already exists in the repo
- FACT: `deploy/asterisk/` contains a STANDALONE overlay (templates + compose + entrypoint + .env.example). Git status shows it is **untracked** (`?? deploy/asterisk/`).
- FACT: `deploy/asterisk/docker-compose.asterisk.yaml` references `dockerfile: Dockerfile` and `build: context: .` — **but no Dockerfile exists** in `deploy/asterisk/`. This is a build-blocker. (verified via `find deploy -name "Dockerfile*"` → empty)
- FACT: `deploy/asterisk/entrypoint.sh` is a complete, executable-rendered config bootstrap:
  - Validates REQUIRED_VARS (VERIMOR_SIP_USERNAME, VERIMOR_SIP_PASSWORD, VERIMOR_SIP_HOST, VERIMOR_DID, ARI_APP_NAME, ARI_PASSWORD, WS_CLIENT_NAME, EXTERNAL_SIGNALING_ADDRESS, EXTERNAL_MEDIA_ADDRESS).
  - Uses envsubst with an EXPLICIT allowlist so Asterisk dialplan vars ($EXTEN, $CALLERID(num), etc.) are preserved in extensions.conf.
  - Renders *.template into /etc/asterisk at startup. Fail-closed on missing vars.
- FACT: three rendered templates exist:
  - `ari.conf.template` — section name = ${ARI_APP_NAME}, password = ${ARI_PASSWORD}, read_only=no.
  - `pjsip.conf.template` — Verimor registration endpoint `[verimor_reg]`, auth `[verimor_auth]`, AOR `[verimor_aor]`, endpoint `[verimor]` with `disallow=all / allow=ulaw / allow=alaw`, `direct_media=no`, `rtp_symmetric=yes`, `force_rport=yes`, `rewrite_contact=yes`. Inbound context = `from-verimor`.
  - `extensions.conf.template` — `[from-verimor] exten => _X.,1,Stasis(${ARI_APP_NAME})`; `[outbound-verimor]` + `[dial-verimor]` with Turkish normalization and `Dial(PJSIP/${EXTEN}@verimor,60)`.
  - NOTE: no `http.conf`, `rtp.conf`, `modules.conf`, or `websocket_client.conf.template` are present. The overlay relies on the Dockerfile/entrypoint to provide them (or for them to be added).
- FACT: `deploy/asterisk/.env.example` declares all placeholders; VERIMOR_SIP_USERNAME/PASSWORD are `your_verimor_sip_username`/`your_verimor_sip_password` (placeholders, no real secrets).
- FACT: The overlay attaches to an **external** Docker network `dograh_app-network` (default; overridable via DOGRAH_NETWORK_NAME) and does NOT publish ARI 8088 to the host — ARI is internal-only.
- FACT: SIP 5060/udp and RTP 10000-10100/udp are published to the host for the carrier.

### 0.2 What Dograh's own stack already supports (verified in source)
- FACT: `api/services/telephony/providers/ari/` ALREADY has a working ARI provider
  (provider.py, transport.py, config.py, strategies.py, serializers.py, __init__.py).
  This is NOT a Verimor-specific fork — it's the upstream ARI provider.
- FACT: The Stasis app name + ARI password live in Dograh as a persisted
  telephony configuration (ProviderSpec `ari`, config_loader maps
  `ari_endpoint`, `app_name`, `app_password`, `ws_client_name`,
  `from_numbers`). Dograh reads them from the DB — they are NOT in the
  overlay .env. So the overlay's `ARI_APP_NAME`/`ARI_PASSWORD` must match
  what the operator ALSO enters in Dograh's telephony-config UI.
- FACT: `ARIProvider.initiate_call` (provider.py:68) builds the outbound endpoint:
  - If `to_number` starts with `SIP/` or `PJSIP/` → used verbatim.
  - ELSE → `sip_endpoint = f"PJSIP/{to_number}"` (provider.py:94).
  - It does NOT append `@verimor` (the PJSIP endpoint name / AOR). It just does
    `PJSIP/<to_number>`. So the number is dialed as a PJSIP device named
    `<to_number>`, not via the `verimor` outbound trunk endpoint.
- ASSUMPTION to confirm: `to_number` reaching `initiate_call` is the **raw
  user-entered** phone number (campaign_call_dispatcher.py:250,370 pass
  `phone_number` from `queued_run.context_variables["phone_number"]` with no
  normalization). The `normalize_telephony_address` util exists
  (api/utils/telephony_address.py) but is NOT applied on the ARI outbound path.
  So a bare E.164 or national number will be sent as `PJSIP/+905XXXX...`
  or `PJSIP/05XXXX...`, which will NOT match the `verimor` outbound endpoint.
  This is the core outbound-endpoint-format bug the overlay must solve (see
  gate G5 / section 3).
- FACT: Dograh's media WebSocket is `/api/v1/telephony/ws/ari` (provider
  accepts query params workflow_id/organization_id/workflow_run_id;
  telephony.py:559). The four-segment tokenless shape
  `/api/v1/telephony/ws/{w}/{o}/{r}` also exists. The ARI externalMedia
  channel dials `/api/v1/telephony/ws/ari?...&token=...` when
  `TELEPHONY_WS_TOKEN_SECRET` is set (ws_auth.py mints the HMAC; the token
  appears in uvicorn+nginx access logs — noted as a logging hazard).
- FACT: `ws_client_name` is the `websocket_client.conf` section name on the
  Asterisk side AND a field in Dograh's ARI config UI (config.py:80, __init__.py:53).
  Both sides must match exactly.
- FACT: the ari_manager process (`python -m api.services.telephony.ari_manager`)
  is the standalone Stasis WebSocket listener. It is started by
  scripts/start_services_docker.sh when `ENABLE_ARI_MANAGER=true` (default) and
  by start_services.sh / start_services_dev.sh. It is a sibling of the api
  container, NOT a separate container in the overlay compose. In the overlay
  design, the ari_manager runs in/behind the `api` container and connects to
  Asterisk's ARI WebSocket at `ws(s)://<asterisk>:8088/ari/events?...`.
- FACT: `res_websocket_client` is the module Dograh's externalMedia path
  depends on (docker-compose.asterisk.yaml healthcheck:
  `module show like res_websocket_client`). The overlay assumes it is loaded.
- FACT: audio format is **G.711 ulaw** (externalMedia `format=ulaw`;
  ari_manager.py:761). The Verimor endpoint must `allow=ulaw` (already true
  in pjsip.conf.template).
- FACT: the overlay's pjsip `identify` only matches `${VERIMOR_SIP_HOST}` for
  inbound INVITE routing to the `verimor` endpoint. If Verimor sends from
  multiple signaling IPs, additional `match=` lines are needed (documented in
  the template comment).

### 0.3 What the overlay is MISSING (verified gaps)
- GAP (BLOCKER-G1): no Dockerfile. The compose build will fail.
- GAP (BLOCKER-G2): no `modules.conf`. Asterisk image must include
  `chan_websocket`, `res_websocket_client`, `res_ari`, `res_ari_websocket`,
  `res_http_websocket`, `chan_pjsip`, `res_pjsip_*` — not guaranteed by a
  stock `asterisk:22` image (Debian builds often split modules into
  asterisk-core-sounds / asterisk-modules packages).
- GAP (BLOCKER-G3): no `http.conf` rendered. ARI/HTTP on 8088 must be enabled.
- GAP (BLOCKER-G4): no `rtp.conf` rendered. RTP_START/RTP_END in .env are
  not wired to a rendered rtp.conf (the compose publishes the port range but
  Asterisk needs `rtp.conf` to bind that range).
- GAP (BLOCKER-G5): no `websocket_client.conf.template`. Dograh's externalMedia
  needs `res_websocket_client` to connect to Dograh. The Dograh-side config
  (ws_client_name) is in Dograh, but the Asterisk-side connection stanza
  pointing at Dograh's `/api/v1/telephony/ws/ari` is absent from the overlay.
- GAP (BLOCKER-G6): outbound endpoint `PJSIP/<number>` does NOT route through
  the `verimor` trunk. For a trunk-dialed call it must be
  `PJSIP/<normalized-number>@verimor` (pjsip endpoint name `verimor`).
  Dograh currently sends `PJSIP/{to_number}` with no `@context`. This must be
  patched in ARIProvider.initiate_call (or pre-normalized into the from_numbers
  pool as full PJSIP URIs). See section 3.

---

## 1. Repository files / deployment overlays to add or modify

These are the exact touch points. The Sonnet worker (t_194c2559) owns these.

### 1.1 Add (new files)
1. `deploy/asterisk/Dockerfile` — multi-stage build guaranteeing modules.
   - Base: `asterisk:22` (Alpine variant is smaller; Debian is more forgiving
     for module availability). MUST install/confirm the module set in section 4.2.
   - Copy conf templates into image build context; the runtime renders them
     via entrypoint.sh (no build-time secrets).
   - Sets `ASTERISK_VERSION` build arg from env (compose already passes it).
   - CRITICAL: verify `asterisk -rx "module show like res_websocket_client"`
     reports Running *in the built image* (the healthcheck depends on it).
2. `deploy/asterisk/conf/modules.conf` — explicit load list (fail-fast if a
   required module is missing) so a stock image can't silently drop ARI.
3. `deploy/asterisk/conf/http.conf` — `enabled=yes`, `bindaddr=0.0.0.0`,
   `bindport=${ARI_HTTP_PORT}` (8088), `password`/`asterisk` not needed (ARI
   auth is in ari.conf). NOTE: 8088 must NOT be published (already enforced
   by compose; document for reviewer).
4. `deploy/asterisk/conf/rtp.conf` — `rtpstart=${RTP_START}`,
   `rtpend=${RTP_END}`, plus `rtpenable=yes`. Binds the published UDP range.
5. `deploy/asterisk/conf/websocket_client.conf.template` — a `[${WS_CLIENT_NAME}]`
   stanza: `type=client`, `uri=${DOGRAH_WS_SCHEME}://${DOGRAH_API_HOST}:${DOGRAH_API_PORT}/api/v1/telephony/ws/ari`,
   `protocols=media`. (The uri carries no query string; v() is appended at
   externalMedia time per the docs mdx:109-111.)
6. `deploy/asterisk/README.md` — the missing operator doc (currently
   `.env.example` points at README.md which doesn't exist).

### 1.2 Modify (existing files)
7. `api/services/telephony/providers/ari/provider.py` — fix outbound endpoint
   formation (section 3 / GAP-G6). Minimal change: build
   `PJSIP/{normalized_digits}@verimor` instead of `PJSIP/{to_number}`.
   Must normalize the to_number through `normalize_telephony_address` first
   so the PJSIP device name is digits only. Guard: only append `@verimor`
   when the number is a bare PSTN number (not already a SIP/PJSIP URI).
8. `api/services/telephony/ari_manager.py` — none required for the slice,
   but the Sonnet worker should add a debug log line at the externalMedia
   creation (already logs ext_channel_id). No code change unless G5 surfaces.

### 1.3 Do NOT touch
- `api/services/telephony/providers/ari/transport.py` — the WebSocket transport
  factory is correct (loads credentials via load_credentials_for_transport,
  builds ulaw FastAPIWebsocketTransport, calls run_pipeline_telephony).
- `api/routes/telephony.py` `/ws/ari` handler — correct subprotocol="media"
  acceptance and query param parsing.
- The base compose / `api` service definition in docker-compose.yaml — the
  overlay must stay standalone and only attach to the external network.

---

## 2. Dograh ARI / chan_websocket / PJSIP architecture

This is a verified, already-shipped architecture (docs/integrations/telephony/asterisk-ari.mdx + ari_manager.py). The overlay only provides the Asterisk-side half.

```
[Verimor SIP trunk]
       |  UDP 5060  (sip.verimor.com.tr)
       |  UDP 10000-10100 RTP (G.711 ulaw)
       v
[Asterisk 22 container]   <-- deploy/asterisk overlay
  PJSIP verimor endpoint  (pjsip.conf: registration + outbound + identify)
  Stasis(dograh) dialplan  (extensions.conf: from-verimor -> Stasis)
  ARI app "dograh"         (ari.conf user = Stasis App Name)
  HTTP 8088/internal      (http.conf; NOT published to host)
  res_websocket_client    (websocket_client.conf -> Dograh ws client stanza)
       | ARI WebSocket events  (ws://<asterisk>:8088/ari/events?app=dograh&...)
       v
[ari_manager process]     <-- runs in/behind the `api` container
  (python -m api.services.telephony.ari_manager, gated by ENABLE_ARI_MANAGER)
  listens for StasisStart/StasisEnd, creates externalMedia channel
       | externalMedia POST /channels/externalMedia  (format=ulaw, transport=websocket)
       | v() appends ?workflow_id=X&organization_id=Y&workflow_run_id=Z[&token=...]
       v
[Dograh API ws /api/v1/telephony/ws/ari]  (routes/telephony.py:559)
  accepts subprotocol="media", delegates to ARIProvider.handle_websocket
       |
       v
[pipecat media pipeline]  (ulaw 8kHz in/out over the WebSocket)
  -> OpenAI voice (STT + LLM + TTS)  (runtime-only credential, see section 5)
```

Key contracts (all verified in source):
- Stasis App Name in ari.conf == ARI_APP_NAME in .env == `app_name` stored in
  Dograh's telephony config == passed as `app` param on every ARI REST/WebSocket
  call (ari_manager.py ws_url, provider.py initiate_call).
- WS_CLIENT_NAME in .env == `websocket_client.conf` section name == Dograh's
  `ws_client_name` config field (used as `external_host` on the externalMedia
  POST, ari_manager.py:739). Mismatch -> externalMedia fails.
- ARI Endpoint in Dograh == `http://<asterisk_host>:8088` (the container's
  network-local address when api and asterisk share app-network). The overlay
  .env has no ARI_ENDPOINT; the operator must enter it in the Dograh UI.
  ASSUMPTION: operator enters `http://dograh_asterisk:8088` (the overlay's
  container_name) — or the api needs `--add-host`/network alias. Must confirm.
- externalMedia `format=ulaw` (verified ari_manager.py:761). Verimor endpoint
  must allow ulaw (verified in pjsip.conf.template: `allow=ulaw`).

---

## 3. Outbound endpoint format (the core fix)

### 3.1 Current behavior (verified)
`ARIProvider.initiate_call` (provider.py:90-94):
```
if to_number.startswith("SIP/") or to_number.startswith("PJSIP/"):
    sip_endpoint = to_number
else:
    sip_endpoint = f"PJSIP/{to_number}"
```
`to_number` is the raw user-entered number (campaign_call_dispatcher.py:250,
370 — no normalization on the ARI outbound path).

### 3.2 Required behavior
Verimor is a PJSIP **endpoint named `verimor`** (pjsip.conf `[verimor]`).
To dial *through* that trunk from ARI you must write the device as
`PJSIP/<number>@<endpoint_name>` => `PJSIP/<digits>@verimor`. The bare
`PJSIP/<number>` form tries to dial a device named `<number>`, which does not
exist, so outbound calls fail with "device not found".

### 3.3 Fix (Sonnet, minimal)
In `ARIProvider.initiate_call`, replace the else-branch:
```
else:
    from api.utils.telephony_address import normalize_telephony_address
    norm = normalize_telephony_address(to_number)
    if norm.address_type == "pstn":
        # Turkey national -> strip +90 -> bare 10/11 digits for PJSIP device name
        digits = norm.canonical.lstrip("+")
        sip_endpoint = f"PJSIP/{digits}@verimor"
    elif norm.address_type == "sip_uri":
        # already a sip: URI — let the caller supply the full target
        sip_endpoint = to_number   # or parse to PJSIP/user@host
    else:
        sip_endpoint = f"PJSIP/{to_number}"
```
- `transfer_call` (provider.py:469-472) needs the SAME fix (duplicate logic —
  apply the same normalization so transfers through Verimor work).
- The `verimor` literal should come from config, not be hardcoded. ASSUMPTION:
  the PJSIP endpoint name is stable per-tenant. The overlay uses `verimor`
  as the section name; the Dograh config should expose `pjsip_outbound_endpoint`
  (default `verimor`) so an org using a different trunk name still routes.
  The Sonnet worker should add this as an optional config field rather than
  hardcoding.

### 3.4 Test for the fix
A unit test in the ARI provider test dir (or extend
api/tests/telephony/ari/... if present) asserting:
- `initiate_call(to_number="+90 5XX XXX XX XX")` -> endpoint param
  `PJSIP/05XXXXXXXXX@verimor` (national) or `PJSIP/905XXXXXXXXX@verimor`
  — decide and document the expected canonical.
- `initiate_call(to_number="PJSIP/6001@verimor")` -> unchanged.
- `initiate_call(to_number="8000")` -> `PJSIP/8000@verimor` (bare extension).

---

## 4. Docker build path — guaranteeing required modules

### 4.1 Base image choice
- ASSUMPTION (operator decision, no cost): `asterisk:22-alpine` is ~150MB and
  requires `apk add` of `asterisk-modules` + `asterisk-channel-*`. The Debian
  `asterisk:22` (~600MB) ships more modules but ALSO not necessarily
  `res_websocket_client`. Either way the image MUST be checked.

### 4.2 Required module set (for the healthcheck + media path)
At minimum (verified from the overlay healthcheck + Dograh's externalMedia):
```
res_ari.so
res_ari_websocket.so
res_http_websocket.so   (chan_websocket transport, not the ARI ws)
chan_websocket.so       (externalMedia transport=websocket)
res_websocket_client.so (outbound ws client -> Dograh)
chan_pjsip.so
res_pjsip.so
res_pjsip_endpoint_base.so
res_pjsip_auth.so
res_pjsip_registrar.so
res_pjsip_outbound_registration.so
res_xmpp.so             (not needed; omit)
```
The Dockerfile MUST `RUN asterisk -rx "module show like ..." ` post-install
and FAIL the build (not just warn) if any required module is `Unloaded`/`missing`.

### 4.3 Dockerfile shape (Sonnet writes this)
```dockerfile
ARG ASTERISK_VERSION=22
FROM asterisk:${ASTERISK_VERSION}
COPY conf/*.template /etc/asterisk/templates/
COPY conf/modules.conf /etc/asterisk/
COPY entrypoint.sh /docker-entrypoint.d/00-render-asterisk
RUN chmod +x /docker-entrypoint.d/00-render-asterisk \
 && (verify each required module via `asterisk -rx` at runtime is the
    healthcheck; build-time check is best-effort)
EXPOSE 5060/udp 10000-10100/udp
ENTRYPOINT ["/usr/bin/entrypoint"]   # stock asterisk entrypoint + render
CMD ["asterisk","-f"]
```
The official `asterisk:22` image already runs `ENTRYPOINT ["/docker-entrypoint.sh"]`
+ `CMD ["asterisk","-D"]` and executes `/docker-entrypoint.d/*.sh` in order.
So `00-render-asterisk.sh` (the renamed entrypoint.sh) runs before Asterisk
starts. CRITICAL: entrypoint.sh currently ends with `exec "$@"` — that is
correct for the stock entrypoint contract; keep it.

### 4.4 Build-time validation command (gate G3)
```
docker compose -f deploy/asterisk/docker-compose.asterisk.yaml \
  --env-file deploy/asterisk/.env build
# then inside the built image:
docker run --rm <image> asterisk -rx 'module show like res_websocket_client'
docker run --rm <image> asterisk -rx 'module show like chan_websocket'
docker run --rm <image> asterisk -rx 'module show like res_ari_websocket'
```
Pass = each prints `Running` with a non-empty `Use Count`.

---

## 5. Credential discovery and injection (no secrets in git/Kanban)

### 5.1 Verimor SIP creds (VERIMOR_SIP_USERNAME / VERIMOR_SIP_PASSWORD)
- Already in `deploy/asterisk/.env.example` as placeholders.
- Discovery: operator edits `deploy/asterisk/.env` (gitignored — must be added
  to `.gitignore` if not already). CRITICAL: the overlay must NOT be committed
  with a filled .env.
- Injection: `docker-compose.asterisk.yaml` loads `.env` via `env_file` (required:false
  so `docker compose config` works pre-fill). entrypoint.sh substitutes them into
  pjsip.conf via the envsubst allowlist (VERIFIED — they are in the allowlist).
- No change to Dograh source needed — Asterisk reads them locally.

### 5.2 ARI creds (ARI_APP_NAME / ARI_PASSWORD)
- These must be entered in BOTH:
  1. `deploy/asterisk/.env` (for ari.conf rendering), AND
  2. Dograh UI Telephony Configuration (ari_endpoint, app_name, app_password,
     ws_client_name).
- They are NOT stored in the repo. Dograh stores them in the
  `telephony_configurations.credentials` JSONB (encrypted at rest per the
  migration a2355fc6bdc1) — the ari_manager reads them via
  `load_credentials_for_transport` and never logs them.

### 5.3 OpenAI voice credential
- This is consumed inside the running pipecat pipeline (services/pipecat/).
  It is an env var on the `api` container (e.g. `OPENAI_API_KEY`), already
  part of the base Dograh deployment. The overlay does NOT touch it.
- Verification: the credential must be present in the `api` container's env
  at runtime; the overlay's only responsibility is to ensure the Asterisk
  container can reach the `api` container's WebSocket. No secret handling
  change required.

### 5.4 TELEPHONY_WS_TOKEN_SECRET (capability token for the media socket)
- OPTIONAL hardening on the Dograh side (ws_auth.py:33). If set, the
  externalMedia v() appends `token=<hmac>` and the `/ws/ari` handler
  verifies it. ASSUMPTION: for the first live call, leave this unset
  (tokenless, the legacy behavior) to reduce moving parts — but the
  operator should enable it for any non-test deployment. Document the
  access-log exposure hazard (token appears in uvicorn + nginx access logs).

### 5.5 .gitignore
- VERIFIED: `deploy/asterisk/.env` must be ignored. Check `.gitignore`
  (root) — add `deploy/asterisk/.env` if absent. This is a Sonnet edit to
  root `.gitignore`.

---

## 6. Staged gates — test commands and observable evidence

All gates run against the overlay + the existing base stack (api/redis/postgres/minio/nginx/coturn).

### G1 — Static checks (read-only, no container)
- `bash -n deploy/asterisk/entrypoint.sh` → exit 0 (syntax).
- `docker compose -f deploy/asterisk/docker-compose.asterisk.yaml config -q`
  → exit 0 (compose parses; works even without .env because env_file
  required:false and all interpolations have defaults).
- `docker run --rm -i node:20-alpine -- sh -c 'cat | envsubst' ...` — too heavy;
  instead: verify each template renders against .env.example with a dry run:
  ```
  env $(cat deploy/asterisk/.env.example | grep -v '^#' | xargs) \
    envsubst '${VERIMOR_SIP_USERNAME} ${ARI_APP_NAME} ...' < \
    deploy/asterisk/conf/pjsip.conf.template > /tmp/pjsip.conf && \
    grep -q 'server_uri = sip:sip.verimor.com.tr:5060' /tmp/pjsip.conf
  ```
  PASS = no unrendered ${VAR} remain in the output for allowlisted vars.
- Evidence: shell exit codes + `diff <(grep -o '\${[A-Z_]*}' rendered) /dev/null`.

### G2 — Container build (no secrets, dummy .env)
- `cp deploy/asterisk/.env.example deploy/asterisk/.env` (placeholders only).
- `docker compose -f deploy/asterisk/docker-compose.asterisk.yaml --env-file
  deploy/asterisk/.env build`
- PASS = build completes; `docker inspect --format '{{.Config.Healthcheck.Test}}'
  dograh-asterisk:local` shows the res_websocket_client check.
- Evidence: `docker compose ... build` exit 0 + healthcheck test present.

### G3 — Module availability inside the built image
- `docker create dograh-asterisk:local` then `docker cp` + `asterisk -rx` OR
  use the healthcheck semantics:
  ```
  docker run --rm --entrypoint '' dograh-asterisk:local \
    asterisk -rx 'module show like res_websocket_client' | grep -q Running
  ```
  (repeat for `chan_websocket`, `res_ari`, `res_ari_websocket`, `chan_pjsip`.)
- PASS = every required module reports Running.
- BLOCKER if any module is missing → add `RUN install asterisk-mod-...` in Dockerfile.

### G4 — Container start + config render (dummy .env, base stack up)
- Requires base Dograh stack running and `dograh_app-network` existing.
  (Use docker compose up for the base stack first; the overlay is standalone.)
- `docker compose -f deploy/asterisk/docker-compose.asterisk.yaml --env-file
  deploy/asterisk/.env up -d`
- PASS = container is `healthy` (healthcheck passes) AND entrypoint log line
  `rendered 4 file(s)` appears (renders ari/pjsip/extensions + new http/rtp/ws_client).
- Evidence: `docker compose ps` → `healthy`; `docker logs dograh_asterisk
  | grep -q 'rendered'`.

### G5 — SIP registration to Verimor (REAL creds, carrier-billed risk = low)
- This is the first gate requiring real Verimor credentials. It does NOT place
  a call — only registers.
- Operator fills `deploy/asterisk/.env` with real VERIMOR_* + ARI_APP_NAME/
  ARI_PASSWORD (matching Dograh UI).
- Restart container: `docker compose ... up -d --build` (env_file reload).
- `docker exec dograh_asterisk asterisk -rx 'pjsip show registrations'`
  → `verimor_reg` row; `Registration: Registered` + `Expires:` counter.
- `docker exec dograh_asterisk asterisk -rx 'pjsip show endpoints'`
  → `verimor` endpoint; `Auth` = verimor_auth; `Allow: ulaw`.
- `docker exec dograh_asterisk asterisk -rx 'ari show apps'`
  → `dograh` listed (Stasis app registered).
- PASS = Registered + Stasis app listed + ulaw allowed.
- BLOCKER if not Registered after 60s → fail-closed (NAT address wrong?
  carrier IP allowlist? Verimor account locked?). STOP before outbound.

### G6 — ARI WebSocket + externalMedia wiring (no audio yet)
- In Dograh UI: create ARI telephony config with
  `ari_endpoint=http://dograh_asterisk:8088`, `app_name=dograh`,
  `app_password=<same as .env>`, `ws_client_name=dograh`.
- `ari_manager` (api container) connects to `ws://dograh_asterisk:8088/ari/events`.
- Evidence: api logs show
  `[ARI org=<id>] WebSocket connected to <ari_endpoint>` and
  `[ARI org=<id>] StasisStart:` on the next inbound event test.
- PASS = ari_manager achieves a stable connection (>30s, the
  `_STABLE_CONNECTION_SECONDS` threshold — ari_manager.py:74).
- BLOCKER if disconnects → wrong host/port, wrong app_name, wrong password,
  or 8088 published/blocked.

### G7 — ARI outbound to Verimor (one-digit test number, NOT Ozan yet)
- Use Dograh's "test call" (telephony routes POST /initiate-call) to a
  non-human target: a Verimor DID that rings voicemail, or a SIP echo, or
  `0` (Turkish operator test tone). Pick a number that does NOT bill Ozan
  or reach a human.
- PASS = Dograh API returns `Call initiated successfully`; ari_manager logs
  `StasisStart` for the created externalMedia channel + a `ChannelCreated`
  for the outbound `PJSIP/<digits>@verimor` leg; call ends cleanly
  (ChannelDestroyed) without media.
- Evidence: ari_manager log lines for StasisStart + externalMedia + bridge.
- This gate validates the outbound endpoint fix (section 3). If the leg shows
  `PJSIP/<digits>` WITHOUT `@verimor` in the channel name, the fix in 3.3
  did not land — STOP.

### G8 — Bidirectional media canary (G.711 ulaw loopback)
- Before the live human call, prove media flows both ways:
  Option A (preferred): use a local SIP softphone registered to the SAME
  `verimor` endpoint (add a second `[softphone]` endpoint in pjsip.conf)
  and place a Dograh outbound call to it; speak and confirm the agent
  hears + responds. The softphone is the "canary" — no carrier billing.
  Option B: use Asterisk's built-in `Echo()` — but that bypasses Dograh,
  so it only validates the SIP trunk, not the Dograh bridge.
- PASS = ulaw RTP packets observed on the RTP port
  (`docker exec ... asterisk -rx 'rtp set debug'` or a tcpdump on the
  published RTP range) AND the agent's TTS is heard at the softphone AND
  the agent's STT produces a transcript.
- Evidence: tcpudump capture + a log line showing the externalMedia channel
  bridged to the caller channel + an agent transcript in the run.

### G9 — Live consented call to Ozan (HUMAN-APPROVAL GATE)
- Pre-reqs:
  - Ozan's explicit consent to receive the test call (operator must obtain
    this BEFORE dialing).
  - Ozan's phone number registered in Dograh OR dialed as the to_number.
  - A short, bounded workflow (≤60s) so cost is contained.
- Execution:
  - Operator runs the test call from the Dograh UI / API; records timestamp.
  - Ozan is told: "this is an automated test call from Dograh migration, you
    may hang up at any time."
- PASS = Ozan reports a two-way conversation: they spoke, the agent
  responded with relevant audio, and the call ended cleanly. The Dograh run
    shows STT transcript + LLM response + TTS output.
- Evidence: screenshot of the completed run + (operator-collected) audio
    recording of Ozan's confirmation. NO recording is made by Dograh unless
    the workflow node enables it — don't auto-record.
- BLOCKER (human-approval): do NOT place the call until the operator
    confirms consent. This card must block on kanban if consent is pending.

### G10 — Rollback check
- `docker compose -f deploy/asterisk/docker-compose.asterisk.yaml down --volumes`
  (removes the asterisk container + its anonymous volumes; does NOT touch
  the base `dograh_app-network` or the api/postgres/etc.).
- PASS = `docker ps` shows no `dograh_asterisk`; base stack unaffected;
  ari_manager logs show `Stopping ARI connection` for the asterisk endpoint.
- Evidence: `docker compose ps` (only base services remain).

---

## 7. Firewall, NAT, port requirements (verified)

- UDP 5060 (SIP) — published in compose; must be open to `sip.verimor.com.tr`.
- UDP 10000-10100 (RTP) — published in compose; must be open inbound from
  Verimor's media IPs. ASSUMPTION: Verimor sends media to the public IP
  registered for the trunk — confirm the IP matches
  EXTERNAL_SIGNALING_ADDRESS / EXTERNAL_MEDIA_ADDRESS.
- TCP 8088 (ARI HTTP + WebSocket) — NOT published (internal only). Firewall
  rule: deny 8088 from anything except the `api`/`ari_manager` source on
  app-network.
- TCP 8000 (Dograh API) — already exposed by the base stack for the UI;
  the overlay's websocket_client.conf points the Asterisk container at
  `api:8000` (service name on app-network). No new host port.
- NAT: pjsip.conf already sets `external_signaling_address` /
  `external_media_address` + `local_net` for RFC1918. The overlay's .env
  requires these be the server's public IPv4. BLOCKER if the server has a
  dynamic IP — must pin a static IP or fail-closed.
- ARM64: VERIFIED unknown. `asterisk:22` official image is multi-arch
  (amd64/arm64). The module set in 4.2 includes `res_websocket_client`
  which historically had ARM64 build gaps in older Asterisk; Asterisk 22
  ships it. The Sonnet worker must run G3 on the actual target arch.
  BLOCKER if `res_websocket_client` is unavailable on arm64 → fall back to
  building Asterisk from source with menuselect (expensive) or a different
  image (`asterisk/asterisk` GHCR variants).

---

## 8. Safe rollback
- The overlay is a SEPARATE compose project (its own file + .env); `docker
  compose -f ... down` removes only the asterisk service. The base Dograh
  stack is untouched.
- Dograh-side ARI config is a DB row (`telephony_configurations`); deleting
  it from the UI reverts the integration. ari_manager auto-stops reconnecting
  once the config is inactive.
- No git changes are required to roll back the asterisk half — it's all
  untracked files under `deploy/asterisk/`. `rm -rf deploy/asterisk` reverts
  the entire overlay.
- ASSUMPTION: the operator confirms Verimor does NOT require a separate
  "disable SIP trunk" call — registration simply stops when the container is
  down. Confirm with Verimor ops.

---

## 9. Billing and human-approval gate (BLOCKERS list)
The operator must affirm each item marked BLOCKER before its gate runs. A
failure at any BLOCKER halts further progression and blocks the Kanban card.

| # | Gate | Blocker question | Who answers |
|---|------|------------------|-------------|
| B1 | G5/G7 (any carrier call) | Verimor credentials + SIP trunk authorized for this DID? | Ozan |
| B2 | G9 (live call) | Explicit consent obtained from Ozan before dialing a human? | Ozan |
| B3 | G9 | Caller-ID number Verimor will permit on outbound (VERIMOR_CALLERID)? | Ozan/Verimor |
| B4 | G3 | res_websocket_client + chan_websocket available on target arch (arm64)? | Sonnet verify |
| B5 | G4/G5 | Public IP for EXTERNAL_SIGNALING_ADDRESS / EXTERNAL_MEDIA_ADDRESS confirmed static + firewall open? | Ozan |
| B6 | G7 | Test target number chosen that does NOT bill a real person (voicemail/echo)? | Ozan |

---

## 10. Division of work between Opus and Sonnet (no concurrent writers)

This repo workspace is SHARED (not a worktree). To honor "no concurrent writers,"
the two child workers are sequenced, NOT parallel:

1. **Sonnet (t_194c2559)** — implements first, as the sole writer:
   - Add Dockerfile, modules.conf, http.conf, rtp.conf,
     websocket_client.conf.template, README.md.
   - Patch .gitignore for deploy/asterisk/.env.
   - Fix ARIProvider outbound endpoint (provider.py:90-94 + transfer_call 469-472).
   - Add/extend the unit test for outbound endpoint formation.
   - Run G1/G2/G3 static + build checks.
   - Hand off changed file list + results to the Kanban card.

2. **Opus (t_bcb4b510)** — reviews AFTER Sonnet signals done:
   - Read-only audit of Sonnet's changes against the plan.
   - Fail-closed review: module availability, NAT/rtp, outbound endpoint
     correctness, secret handling, ws_client_name contract, rollback.
   - Returns required fixes + acceptance tests for the integrator (t_e5138dd5).

3. After both are done, the integrator (t_e5138dd5, model unspecified) runs
   G4–G10 in sequence, blocking on B1–B6 as each gate is reached.

The parent (this) card does NOT write files — it ends with this plan handed off
to the children. Children are linked: t_194c2559 and t_bcb4b510 both have
`parents=[t_506c8f5b]`. t_bcb4b510 should add t_194c2559 as a parent dependency
once it exists (use kanban_link) so Opus waits for Sonnet's output.

### Ordering enforcement
- t_194c2559 is currently `todo`. It must be marked in-progress before t_bcb4b510
  can meaningfully review. The integrator t_e5138dd5 (child of both) is gated on
  BOTH parents being done.

---

## 11. Acceptance criteria (summary — what "done" means at each level)

### Planning card (this one, t_506c8f5b) — DONE when:
- This document exists at `deploy/asterisk/MIGRATION_PLAN.md` (this file).
- It names every file to add/modify (section 1).
- It states the architecture + outbound endpoint fix (sections 2,3).
- It lists credential handling with no secrets in git (section 5).
- It enumerates G1–G10 gates with pass/fail evidence (section 6).
- It lists firewall/NAT/rollback/billing/human gates (sections 7–9).
- It defines the Sonnet→Opus→integrator handoff ordering (section 10).
- Every unverified item is labeled ASSUMPTION or BLOCKER, not stated as fact.

### Sonnet (t_194c2559) — DONE when:
- All files in section 1.1/1.2 added/modified; `.gitignore` updated.
- Build gate G2 + module gate G3 pass (real `docker build` run, modules verified).
- Outbound endpoint unit test passes (section 3.4).
- Static gate G1 passes on all new shell/YAML/templates.
- Handoff comment with changed_files + command results.

### Opus (t_bcb4b510) — DONE when:
- A fail-closed review with file:line references is returned.
- Required fixes + acceptance tests for the integrator are listed.
- No unverified claim of SIP/media success.

### Integrator (t_e5138dd5) — DONE when:
- Gates G4–G8 pass (build/start, SIP reg, ARI ws, externalMedia, outbound, media).
- G9 passes ONLY after B1/B2/B3 human affirmation (block on kanban otherwise).
- G10 rollback verified.
- Evidence artifacts attached to the Kanban card (log excerpts, NOT secrets).

---

## 12. Things explicitly NOT in scope here (next cards / out of band)
- No changes to Dograh's base `docker-compose.yaml` api/postgres/redis services.
- No changes to the pipecat voice pipeline or OpenAI credential wireup (runtime
  env on the api container; pre-existing).
- No Terraform/infra-as-code (this is a manual overlay deploy).
- No permanent recording of live calls (assumed off by default; not enabled).
