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
