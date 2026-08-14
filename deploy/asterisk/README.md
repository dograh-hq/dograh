# Dograh — Self-Hosted Asterisk + Verimor SIP Trunk

This is a standalone Docker overlay for self-hosting Asterisk with a Verimor SIP trunk,
providing ARI call control and externalMedia audio bridging to the Dograh platform.

## Architecture

```
+-------------------+       UDP 5060        +-------------------+
|    Verimor SIP    | <--------------------> |   Asterisk 22     |
|     Trunk         |   IP: sip.verimor.com.tr|  (this overlay)  |
+-------------------+     UDP 10000-10100   +-------------------+
                                                 |
                                                 | ARI WebSocket (ws://<asterisk>:8088/ari/events)
                                                 v
                                          +---------------+
                                          |   ari_manager  |
                                          |   (api process)|
                                          +---------------+
                                                 |
                                                 | externalMedia POST /channels/externalMedia
                                                 v
                                          +---------------+
                                          | Dograh API    |
                                          | /api/v1/telephony/ws/ari|
                                          +---------------+
                                                 |
                                                 | G.711 ulaw audio (8kHz)
                                                 v
                                          +---------------+
                                          |   OpenAI       |
                                          |   Voice Runtime|
                                          +---------------+

## Prerequisites

- Dograh backend running on Docker
- External Docker network `dograh_app-network` (created by base stack)
- Verimor SIP trunk credentials (username, password, signaling host, DID, etc.)

## Quick Start

1. Copy the environment template and fill in your credentials:
   ```bash
   cp deploy/asterisk/.env.example deploy/asterisk/.env
   # Edit deploy/asterisk/.env with real values
   ```

2. Ensure Docker base stack is running and `dograh_app-network` exists:
   ```bash
   docker network ls | grep app-network
   # If not found: docker network create dograh_app-network
   ```

3. Start the overlay:
   ```bash
   docker compose -f deploy/asterisk/docker-compose.asterisk.yaml --env-file deploy/asterisk/.env up -d --build
   ```

4. View logs:
   ```bash
   docker compose -f deploy/asterisk/docker-compose.asterisk.yaml logs -f asterisk
   ```

## Configuration Fields

### Verimor SIP Trunk

| Variable | Description | Example |
|----------|-------------|---------|
| `VERIMOR_SIP_USERNAME` | Verimor SIP account user | `your_trunk_username` |
| `VERIMOR_SIP_PASSWORD` | Verimor SIP account password | `your_trunk_password` |
| `VERIMOR_SIP_HOST` | Verimor signaling host | `sip.verimor.com.tr` |
| `VERIMOR_SIP_PORT` | UDP SIP signaling port | `5060` |
| `VERIMOR_DID` | Inbound DID (digits only) | `908500000000` |
| `VERIMOR_CALLERID` | Outbound caller ID (optional) | `908500000000` |

### Asterisk ARI (internal only)

| Variable | Description | Example |
|----------|-------------|---------|
| `ARI_APP_NAME` | Stasis app name (must match Dograh UI) | `dograh` |
| `ARI_PASSWORD` | ARI user password | `CHANGE_ME` |
| `ARI_HTTP_PORT` | ARI local HTTP port | `8088` |
| `WS_CLIENT_NAME` | WebSocket client section name (shared across config) | `dograh` |

### Dograh Backend (external media target)

| Variable | Description | Example |
|----------|-------------|---------|
| `DOGRAH_WS_SCHEME` | WebSocket scheme (`ws` or `wss`) | `ws` |
| `DOGRAH_API_HOST` | Dograh API hostname inside `dograh_app-network` | `api` |
| `DOGRAH_API_PORT` | Dograh API port | `8000` |

### Networking / NAT

| Variable | Description | Example |
|----------|-------------|---------|
| `EXTERNAL_SIGNALING_ADDRESS` | Public IPv4 for Verimor signaling (NAT) | `203.0.113.10` |
| `EXTERNAL_MEDIA_ADDRESS` | Public IPv4 for Verimor RTP (NAT) | `203.0.113.10` |
| `LOCAL_NET` | Extra local subnets to skip NAT (comma-separated) | `` |
| `SIP_PORT` | Published SIP signaling port | `5060` |
| `RTP_START` | Bounded RTP range start | `10000` |
| `RTP_END` | Bounded RTP range end | `10100` |

### Compose wiring

| Variable | Description | Example |
|----------|-------------|---------|
| `DOGRAH_NETWORK_NAME` | Existing Docker network name | `dograh_app-network` |
| `ASTERISK_VERSION` | Base Asterisk image version | `22` |

## Integration with Dograh

### 1. ARI Configuration in Dograh UI (telephony config)

- **ARI Endpoint**: `http://dograh_asterisk:8088`
- **Stasis App Name**: Matches `ARI_APP_NAME` from this overlay (default: `dograh`)
- **App Password**: Matches `ARI_PASSWORD` from `.env`
- **websocket_client.conf Name**: Matches `WS_CLIENT_NAME` from `.env` (default: `dograh`)

### 2. Inbound Call Routing

- Verimor delivers inbound calls to the `verimor` PJSIP endpoint
- Calls are routed to the `from-verimor` dialplan context
- Every inbound call enters the Stasis app (matching `ARI_APP_NAME`)
- Dograh's inbound worker matches the DID/digits against registered numbers

### 3. Outbound Call Routing via ARI

- Dograh ARI provider originates SIP calls via `ari_manager` → Asterisk ARI REST API
- Expects a PJSIP endpoint that routes through the Verimor trunk
- `provider.py` builds endpoints as:
  - If already SIP/PJSIP: verbatim (`PJSIP/6001@external`)
  - Else: `PJSIP/<normalized_digits>@verimor` (the Verimor PJSIP endpoint name)

### 4. Audio Path (externalMedia)

- `res_websocket_client` connects OUT to `ws://${DOGRAH_WS_SCHEME}://${DOGRAH_API_HOST}:${DOGRAH_API_PORT}/api/v1/telephony/ws/ari`
- Uses `protocols=media`
- Call audio flows G.711 ulaw (8 kHz) in both directions
- externalMedia `format=ulaw` must be set on Dograh side

## Security Notes

- **ARI/HTTP (8088) is internal-only**: Not published. Do NOT expose to host.
- `.env` is gitignored. Edit locally.
- Credentials are environment variables passed via Docker env_file.
- WebSocket tokenless by default (`TELEPHONY_WS_TOKEN_SECRET` unset for first live call).

## Troubleshooting

### Container fails to start

Run logs and check Asterisk errors:
- Missing vars: `deploy/asterisk/entrypoint.sh` validates REQUIRED_VARS
- Module not loaded: `modules.conf` lists explicitly; check log
- Template render failed: `entrypoint.sh` logs each file

### SIP registration fails

Run `docker exec dograh_asterisk asterisk -rx 'pjsip show registrations'`.
- Check `EXTERNAL_SIGNALING_ADDRESS` matches your REAL public IP
- Verify `VERIMOR_SIP_HOST`/`VERIMOR_SIP_PORT` are correct
- Confirm credentials are correct

### ARI app not registered

Run `docker exec dograh_asterisk asterisk -rx 'ari show apps'`.
- Verify `ARI_APP_NAME` matches value entered in Dograh UI
- Confirm `ari.conf` section `${ARI_APP_NAME}` has correct password

### Audio not flowing

Verify loopback:
- TCP dump on port 10000-10100
- Dograh logs show `StasisStart:`
- Dograh run shows STT + TTS
- G.711 ulaw packets present

## Staging Gates

G1: Static checks (syntax, compose, template render)
G2: Container build
G3: Module availability inside built image
G4: Container start + config render (dummy .env)
G5: SIP registration to Verimor (requires real credentials)
G6: ARI WebSocket + externalMedia wiring (no audio)
G7: ARI outbound to Verimor (test number, not billing)
G8: Bidirectional media loopback canary
G9: Live consented call to target (human-approval)
G10: Rollback check (`down --volumes`)
