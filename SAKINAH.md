# Sakinah Scenario Console

The Scenario Console uses Dograh's existing browser WebRTC, speech-to-text,
runtime LLM, and text-to-speech pipeline. Saved sessions are JSON files under
`data/sakinah-sessions/` on the host.

## Build and start

Create the normal local `.env` first (including `OSS_JWT_SECRET` and the model,
STT, and TTS credentials required by your workflow runtime), then run:

```bash
mkdir -p data/sakinah-sessions
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml build api ui
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml --profile local-turn up -d
```

The `local-turn` profile runs a coturn TURN server, which is **required for
the browser voice console**: under Docker Desktop the browser and the API
container cannot exchange WebRTC media directly, so audio is relayed through
coturn. Configure it in `.env` before starting (the AI-to-AI simulation works
without it, since its audio never leaves the API process):

```bash
ENABLE_COTURN=true
TURN_HOST=<your Mac's LAN IP, e.g. from: ipconfig getifaddr en0>
TURN_SECRET=<random secret, e.g. from: openssl rand -hex 24>
```

If your machine's LAN IP changes, update `TURN_HOST` and restart the stack.

Open [http://localhost:3010/sakinah](http://localhost:3010/sakinah), sign in,
enter a scenario, and select **Start Scenario**. Allow microphone access, speak
with Sakinah, confirm that interim USER text becomes final text, then select
**End Session**. The page displays the saved session ID; its JSON is available
at `data/sakinah-sessions/<session_id>.json`.

## AI-to-AI simulation (Service User ↔ Sakinah)

Open [http://localhost:3010/sakinah/sim](http://localhost:3010/sakinah/sim),
sign in, enter a scenario describing the service user, and select **Start
Simulation**. Two real Dograh workflows start on the server — **Sakinah
Scenario Console** and **Sakinah Service User Simulator** — wired together
through an in-memory audio transport, so the two AIs speak to each other with
no microphone involved. Sakinah always opens the conversation; the service
user is started as a listener (`suppress_initial_greeting`) and only replies
once it hears her greeting, so turns alternate cleanly from the start. The
live transcript labels each turn SERVICE USER or SAKINAH, and the panel below
the Start button shows the workflow and run IDs for both agents.

**Stop Simulation** ends both pipelines. A simulation also stops on its own
when either agent ends the call, when either pipeline fails, or after the
maximum duration (5 minutes by default, `max_duration_seconds` in the start
request, capped at 15 minutes). The merged transcript is saved to
`data/sakinah-sessions/<simulation_id>.json`.

The authenticated API smoke script is not a Simulation pass. A Simulation
pass requires a normal browser session on the canonical localhost origin and
all of the following: the Scenario Library and both Sakinah workflows are
visible, the live transcript reaches at least five alternating turns, the
audio WebSocket opens, and both SERVICE USER and SAKINAH speech is audible.
Do not use injected tokens, localStorage auth, or a hidden smoke account with
different organisation data to claim this result. If a local user has no
scenarios, create or import them for that user through the Scenario Library;
Sakinah scenarios are user-owned records in the current model.

## Logs and shutdown

```bash
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml logs -f api ui
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml --profile local-turn down
```

## Deployment process

The local Docker stack is the current staging environment (see `ROADMAP.md`);
the AWS host serving `voice.calmos.io` runs the same compose stack with the
`remote` profile. Changes land on `main` through pull requests and are
deployed as follows:

> **Single-worker requirement**: every deployment must run the API with
> `FASTAPI_WORKERS=1` (the default). Simulations keep in-process state, so
> with multiple workers behind nginx the simulation status/stop endpoints
> and the transcript/audio WebSockets intermittently hit a worker that does
> not own the simulation (transcript appears dead while audio works, or
> vice versa). Tracked as
> [issue #4](https://github.com/applied-biosciences/dograh/issues/4); do not
> raise the worker count until it is resolved.

1. **Merge to `main`** — every change goes through a PR (tests +
   review), then merge on GitHub.
2. **Sync and rebuild** from the repo root:

   ```bash
   git checkout main && git pull
   docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml --profile local-turn stop
   docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml build ui
   docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml build api
   docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml --profile local-turn up -d
   ```

   Build `ui` and `api` sequentially with the stack stopped: the Next.js
   build needs most of the Docker VM's memory and gets OOM-killed when run
   concurrently with the running containers.
3. **Verify**:

   ```bash
   curl -s http://localhost:8000/api/v1/health   # expect status ok, turn_enabled true
   ```

   Then smoke-test http://localhost:3010/sakinah/sim (Start → labelled
   transcript → Stop) and, for voice, http://localhost:3010/sakinah.
4. **Run the integration tests** (no host Python needed — they run in a
   one-off container against an isolated `_test` database):

   ```bash
   docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml run --rm --no-deps \
     --user root --entrypoint "" -v ./api:/app/api -v ./sdk:/app/sdk -w /app/api api \
     sh -c 'pip install -q --target /tmp/tdeps pytest pytest-asyncio && \
            PYTHONPATH=/tmp/tdeps:/app python -m pytest tests/test_sakinah_simulation.py'
   ```

Notes:

- Always include `--profile local-turn` in `up`/`down`/`stop` commands so
  coturn is managed with the rest of the stack; without it the browser
  voice console cannot exchange audio (see the TURN section above).
- The UI regenerates its API client from a running backend: after changing
  backend routes, start the stack and run `npm run generate-client` in
  `ui/` (or the containerized equivalent), and commit `ui/src/client/`.
