# Overlay — voice-platform additions to Dograh

Everything our voice platform adds on top of Dograh lives here. The directories outside `overlay/` are upstream (`dograh-hq/dograh`) and follow the sync procedure in [`../UPSTREAM_PULL.md`](../UPSTREAM_PULL.md).

## Layout

```
overlay/
├── mcp_server/          # MCP server that exposes Dograh flows + Pipecat as MCP tools
│   ├── server.py        # FastMCP entry point
│   └── tools/           # individual tool implementations (flow CRUD, exec, eval)
├── adapters/            # transports that talk back to the platform api
└── requirements.txt     # python deps unique to overlay/
```

The voice platform api (`Harddiikk/voice-platform` / `apps/api`) talks to the engine **only over MCP** — never imports overlay code directly.

## Local development

Engine bootstrap is currently a skeleton (Phase 0). Real flow execution + transports get filled in during Phase 1 Stream S5.

## Production image (P2-D)

The `.github/workflows/overlay-image.yml` workflow builds upstream's `api/Dockerfile` (which already exposes the MCP server at `/api/v1/mcp` over Streamable HTTP) and pushes to:

```
ghcr.io/<owner>/voice-engine:<short-sha>   # every build
ghcr.io/<owner>/voice-engine:main          # main only
ghcr.io/<owner>/voice-engine:latest        # main only
ghcr.io/<owner>/voice-engine:pr-<sha>      # phase-2/p2d-* PR builds
```

The `overlay/` directory is shipped alongside the image but the runtime entrypoint (`api/scripts/start_services_docker.sh`) runs upstream's FastAPI app — overlay-side tool customization is deferred until we actually need it. Today the platform api can point straight at upstream's MCP.

### Prod cutover (separate PR on voice-platform)

On VPS-1, after this image lands on GHCR:

1. Pull: `docker pull ghcr.io/<owner>/voice-engine:<sha>`
2. Stand up postgres + redis + minio on the voice-platform docker network (or share existing ones — Dograh expects pgvector pg17, redis 7, minio).
3. Run the engine with the env vars Dograh expects (`POSTGRES_*`, `REDIS_URL`, `MINIO_*`, plus its own `BACKEND_API_ENDPOINT`).
4. Wire it into `voice-platform/docker-compose.yml` under an `engine` profile so it only comes up on `--profile engine`.
5. Flip `DOGRAH_MCP_URL` on the platform-api service from the hosted URL to `http://voice-engine:8000/api/v1/mcp` and set `DOGRAH_MODE=self-hosted`.
6. Restart the api container; confirm `/flows` GET responds with the self-hosted workflow list.

Rollback: revert the env var change and restart — hosted Dograh stays as a fallback.
