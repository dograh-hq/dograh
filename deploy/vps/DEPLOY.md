# VPS deployment runbook — Auto4You voice engine

Self-hosted white-labeled Dograh fork (with the VoiceLink telephony provider)
on the Ubuntu VPS at `165.22.216.189`, behind Caddy with automatic TLS.

| What | Value |
| --- | --- |
| UI | https://app.auto4you.in (Caddy → `ui:3010`) |
| API | https://api.auto4you.in (Caddy → `api:8000`, WebSockets included) |
| Recordings | https://api.auto4you.in/voice-audio/* (Caddy → `minio:9000`) |
| Images | `ghcr.io/harddiikk/voice-engine-api` / `voice-engine-ui`, tags `:saas` + `:<short-sha>` |
| Built by | `.github/workflows/saas-images.yml` (push to `feat/voicelink-saas` or manual dispatch) |

**Never build images on the VPS** — the 2GB box cannot build Next.js. Always
pull from GHCR.

## 1. One-time VPS setup

```bash
# On the VPS (Docker + compose v2 already installed)
sudo mkdir -p /opt/voiceengine
sudo chown $USER /opt/voiceengine
```

From your workstation, copy the kit:

```bash
scp deploy/vps/docker-compose.yml deploy/vps/Caddyfile \
    deploy/vps/.env.api.example deploy/vps/.env.ui.example \
    root@165.22.216.189:/opt/voiceengine/
```

On the VPS, create the real env files and set secrets:

```bash
cd /opt/voiceengine
cp .env.api.example .env.api
cp .env.ui.example .env.ui

# REQUIRED: set a real JWT secret in .env.api
sed -i "s/^OSS_JWT_SECRET=.*/OSS_JWT_SECRET=$(openssl rand -hex 32)/" .env.api
```

Review `.env.api` — the defaults already point at the compose-internal
postgres/redis/minio and the public auto4you.in domains; nothing else is
mandatory.

If the images are private on GHCR, log in once:

```bash
docker login ghcr.io -u harddiikk   # password: a GitHub PAT with read:packages
```

Optional: the old `voiceplatform_caddy_data` volume holds previous certs, but
fresh issuance is automatic — no action needed. Just make sure ports 80/443
are open in the firewall (`ufw allow 80,443/tcp && ufw allow 443/udp`).

## 2. Start / update the stack

```bash
cd /opt/voiceengine

# First start or update to the latest :saas build:
docker compose pull && docker compose up -d

# Pin a specific CI build (recommended for reproducibility):
TAG=<short-sha> docker compose pull && TAG=<short-sha> docker compose up -d
```

`TAG` defaults to `saas` and `GH_OWNER` to `harddiikk`
(see `docker-compose.yml`).

## 3. Verify migrations + health

DB migrations run automatically: the api entrypoint executes
`alembic upgrade head` before starting uvicorn. Check:

```bash
docker compose logs api | grep -i -E "alembic|upgrade"   # migration output
docker compose ps                                        # api/ui should be "healthy"
curl -s https://api.auto4you.in/api/v1/health             # api through Caddy
```

First boot takes ~1-2 minutes (migrations + healthcheck start_period).
The `voice-audio` MinIO bucket is auto-created by the api — no manual step.

## 4. First user = admin

Open https://app.auto4you.in and **sign up**. The first user to register on
an OSS deployment becomes the organization admin — do this before sharing the
URL with anyone. Subsequent signups are regular users.

## 5. Configure VoiceLink + Gemini (per-organization, in the UI)

As the admin user:

1. **Telephony (VoiceLink)** — go to **Settings → Telephony / Phone numbers**
   (organization settings) and add a VoiceLink configuration: API base URL,
   API key/credentials, and DID number(s) from the VoiceLink reseller panel.
   Outbound call audio flows over
   `wss://api.auto4you.in/api/v1/telephony/ws/...` and call events hit
   `https://api.auto4you.in/api/v1/telephony/voicelink/events/...` — both
   derived from `BACKEND_API_ENDPOINT` in `.env.api`, so that var must stay
   correct and publicly reachable.
2. **Model/API keys (incl. Gemini)** — go to **Settings → API keys / model
   configuration** and add the Gemini key (and any other provider keys) for
   the organization. Keys are stored per-org in the DB, not in env files.

Note: the ui image is built with `NEXT_PUBLIC_CLIENT_MODE=true`, which hides
model/provider/API-key/engine settings from the client-facing UI. Configure
provider keys via the admin account / API as needed.

## 6. Memory caveat (2–4GB RAM)

- `FASTAPI_WORKERS=1` and `ARQ_WORKERS=1` in `.env.api` — do not raise them
  until the box is resized to 4GB+; each uvicorn worker is a full Python
  process loading the entire pipeline stack.
- One worker handles a small number of concurrent calls. Scale workers (and
  RAM/CPU) before any real call volume.
- If the box has no swap, add a little headroom:
  `fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`.

## 7. Useful commands

```bash
docker compose logs -f api          # live api logs (calls, migrations, arq)
docker compose logs -f caddy        # TLS issuance problems show up here
docker compose restart api          # restart after editing .env.api
docker compose down                 # stop (volumes/data are preserved)
docker volume ls | grep voiceengine # postgres/minio/caddy data volumes
```

## Known quirks

- **NEXT_PUBLIC_* are build-time**: backend URL and branding are baked into
  the ui image by the GitHub workflow. Changing `.env.ui` does not affect
  them — edit `.github/workflows/saas-images.yml` build-args and rebuild.
- **Recordings URL path**: the api emits recording links as
  `https://api.auto4you.in/voice-audio/<file>` (`MINIO_PUBLIC_ENDPOINT` +
  bucket). The Caddyfile's `/voice-audio/*` handle is what makes those links
  resolve — don't remove it.
- **No TURN server**: in-browser WebRTC test calls may fail behind strict
  NAT (no coturn in this stack). VoiceLink telephony calls are unaffected —
  they use server-to-server WebSockets.
- **ENVIRONMENT=production** (not "oss"): valid values are
  `local|production|test`; `local` enables localhost-only WebRTC behavior.
