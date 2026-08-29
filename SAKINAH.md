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

## Logs and shutdown

```bash
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml logs -f api ui
docker compose -f docker-compose.yaml -f docker-compose.local-build.yaml down
```

