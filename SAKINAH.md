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
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml up -d
```

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
no microphone involved. The live transcript labels each turn SERVICE USER or
SAKINAH, and the panel below the Start button shows the workflow and run IDs
for both agents.

**Stop Simulation** ends both pipelines. A simulation also stops on its own
when either agent ends the call, when either pipeline fails, or after the
maximum duration (5 minutes by default, `max_duration_seconds` in the start
request, capped at 15 minutes). The merged transcript is saved to
`data/sakinah-sessions/<simulation_id>.json`.

## Logs and shutdown

```bash
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml logs -f api ui
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml down
```

