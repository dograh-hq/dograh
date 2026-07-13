# Dograh-LiveKit Bridge — Design Spec

**Date:** 2026-07-13
**Status:** Approved
**Author:** Andrea Batazzi

## 1. Overview

Add a **LiveKit + Agno multi-agent runtime** as a parallel pathway to the existing Pipecat runtime in Dograh. A new microservice `dograh-livekit` consumes Dograh's configuration and data via internal HTTP APIs, orchestrates multi-agent conversations using Agno Workflows on LiveKit Cloud, and coexists with the existing Pipecat pathway without modifying it.

### 1.1 Motivation

- **SOTA models**: Leverage Gemini 3.1 Flash Live, GPT-4o Realtime, Claude via Bedrock — natively supported through LiveKit plugins
- **Multi-agent orchestration**: From single-agent linear pipelines to multi-step, multi-agent workflows with conditional routing (Agno `Workflow` + `Router`)
- **LiveKit-native transport**: SIP trunks, room management, WebRTC — managed by LiveKit Cloud, reducing operational burden
- **Zero risk to existing system**: New service, no changes to Pipecat runtime or Dograh core

### 1.2 Non-Goals

- Does NOT replace or modify Pipecat runtime
- Does NOT change Dograh's UI, workflow editor, or data model
- Does NOT depend on Luminai infrastructure (SurrealDB, NATS, GCP)
- Does NOT copy Luminai code — reuses patterns, not source

## 2. Architecture

```
                         Dograh (nessuna modifica)
                    ┌─────────────────────────────┐
                    │  API FastAPI (PostgreSQL)    │
                    │  • workflow graph JSON       │
                    │  • model config (STT/TTS/LLM)│
                    │  • knowledge base docs       │
                    │  • tools/integrations        │
                    │  • campaigns & leads         │
                    │                              │
                    │  PipecatEngine (Twilio/...)  │◄── trasporti Pipecat esistenti
                    └──────────────┬──────────────┘
                                   │ HTTP API (internal)
                    ┌──────────────▼──────────────┐
                    │     🆕 dograh-livekit       │
                    │     (nuovo microservizio)    │
                    │                              │
                    │  • LiveKit AgentServer       │
                    │  • Translator Layer          │
                    │    Dograh JSON → Agno Steps  │
                    │  • Agno WorkflowFactory      │
                    │  • Stage Agents              │
                    │  • Tool Dispatcher           │
                    │  • KB search via Dograh API  │
                    └──────────────┬──────────────┘
                                   │ LiveKit Agents SDK
                    ┌──────────────▼──────────────┐
                    │     LiveKit Cloud            │
                    │  • SIP trunks/rules          │
                    │  • Room management           │
                    │  • WebRTC                    │
                    └─────────────────────────────┘
```

### 2.1 Routing

The transport determines the pathway:
- **LiveKit SIP/WebRTC** → `dograh-livekit` → Agno Workflow
- **Twilio, Vonage, SmallWebRTC** → PipecatEngine (unchanged)

### 2.2 Data Sharing

`dograh-livekit` reads ALL configuration and data from Dograh via internal HTTP APIs:
- Workflow graph (ReactFlow JSON)
- Model configuration (STT/TTS/LLM provider, model, temperature, voice)
- Knowledge base documents
- Tool and integration definitions
- Campaign and lead data for outbound calls

## 3. Component Design

### 3.1 Service Structure

```
dograh-livekit/
├── pyproject.toml
├── Dockerfile
├── app/
│   ├── main.py                  # LiveKit AgentServer entrypoint
│   ├── config.py                # Settings (LiveKit URL, Dograh API URL)
│   ├── entrypoint.py            # RTC session handler
│   ├── translator/
│   │   ├── __init__.py
│   │   ├── workflow.py          # Dograh JSON → Agno Workflow
│   │   └── nodes.py             # Node type mappers
│   ├── session/
│   │   ├── __init__.py
│   │   ├── voice.py             # Realtime + Fallback session builder
│   │   ├── stages.py            # Stage agents
│   │   ├── flow.py              # Flow variables & template rendering
│   │   └── lifecycle.py         # Session record, context persistence
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py          # Tool registry (from Dograh integrations)
│   │   ├── dispatcher.py        # Tool dispatch with Agno
│   │   └── kb_search.py         # Knowledge base search via Dograh API
│   ├── dograh_client.py         # HTTP client for Dograh internal API
│   └── models.py                # Pydantic models for Dograh config
└── tests/
```

### 3.2 Translator Layer

Maps Dograh ReactFlow JSON nodes to Agno `Workflow` with `Step` + `Router`.

#### Node Mapping

| Dograh Node | type | Agno Equivalent |
|---|---|---|
| `startCall` | Entry point | Agno `Step` with greeting instructions |
| `agentNode` | LLM conversation step | Agno `Step(agent=CustomStage)` |
| `endCall` | Terminal node | Agno `Step(agent=CloseStage)` — no outgoing edges |
| `globalNode` | Shared persona/tone | Prepended to every Step's `instructions` |
| `trigger` | API/Telephony trigger | Handled at `entrypoint.py` level (room metadata) |
| `webhook` | HTTP callout | `function_tool` on the Agno Agent, calls Dograh API |
| `qa` | Post-call analysis | Post-processing after session close |
| Integration nodes | Custom plugins | Tool registered via `ToolRegistry` |

#### Edge Mapping

Dograh edges carry `{label, condition, transition_speech}`. The translator:

1. Builds an edge index: `source → {condition → target}`
2. For nodes with multiple outgoing edges (non-trivial routing), creates an Agno `Router` with a `selector` function that matches the LLM output content against `condition` values
3. The `*` condition becomes the default fallback target
4. `transition_speech` is injected as instructions to the next agent Step

```python
# Simplified translator logic
def translate_workflow(dograh_graph: dict) -> agno.Workflow:
    nodes = dograh_graph["nodes"]
    edges = dograh_graph["edges"]

    # Filter to agent-executable nodes
    agent_nodes = [n for n in nodes if n["type"] in AGENT_NODE_TYPES]

    # Build edge index: source → {condition → target}
    edge_index = {}
    for e in edges:
        edge_index.setdefault(e["source"], {})[e["data"]["condition"]] = e["target"]

    steps = []
    for node in agent_nodes:
        agent = build_stage_agent(node)
        steps.append(Step(name=node["id"], agent=agent))

        # If node has multiple outgoing edges, add Router
        conditions = list(edge_index.get(node["id"], {}).keys())
        if len(conditions) > 1:
            router = build_router(node["id"], conditions, edge_index)
            steps.append(router)

    return Workflow(name=f"dograh_{graph.get('id', 'wf')}", steps=steps)
```

### 3.3 Voice Session

Two pathways depending on model provider:

#### Realtime (native audio)
For providers that handle STT+LLM+TTS in a single streaming API:
- **Google Realtime** (Gemini 3.1 Flash Live, Gemini 2.5 Flash Native Audio)
- **OpenAI Realtime** (GPT-4o Realtime)
- **AWS Realtime** (Nova Sonic)

Uses LiveKit's `AgentSession(llm=realtime_model)` with native audio frames.

#### Fallback (STT + LLM + TTS)
For text-based LLM providers (Claude, non-realtime Gemini):
- Deepgram STT → Google/Anthropic LLM → Cartesia/ElevenLabs TTS
- Uses LiveKit's `AgentSession(stt=..., llm=..., tts=...)` with VAD

### 3.4 Stage Agents

Six built-in stage types, matching common conversation patterns:

| Stage | Purpose | Completion Tool |
|---|---|---|
| `IdentifyIntentStage` | Classify caller intent, route to next stage | `record_intent(intent, description, urgency)` |
| `CollectContactStage` | Gather name, email, phone | `record_contact(name, email, phone)` |
| `CollectDataStage` | Collect arbitrary structured data | `record_data(data: JSON)` |
| `QualifyLeadStage` | Qualify budget, timeline, decision-maker | `record_qualification(budget, timeline, ...)` |
| `PresentOfferStage` | Present offer, handle response | `record_offer_response(interested, feedback, ...)` |
| `HandleObjectionStage` | Address objections | `record_objection_outcome(objection, resolution, ...)` |
| `CloseStage` | Summarize and end call | `close_call(summary, outcome)` |
| `CustomStage` | Generic stage following node instructions | `complete_custom_stage(result, route_key?)` |

Each stage:
- Has `_base_instructions()` that describes its purpose
- Has an `on_enter()` hook for initial behavior (greeting, first question)
- Has a `function_tool` method that persists results and returns the next agent for handoff
- Supports extra tools from the Dograh node config (`tool_uuids`, `document_uuids`)

### 3.5 Tool Dispatcher

Tools from Dograh are loaded at agent instantiation:

1. Read `tool_uuids` and `document_uuids` from node data
2. Call Dograh API to resolve UUIDs to tool definitions
3. Register tools with the Agno Agent as `function_tool` methods
4. At runtime, LLM generates tool calls → Agno executes → result fed back to LLM

Tool categories:
- **Knowledge Base search** (`search_knowledge`): Vector/semantic search over Dograh documents
- **Webhook/HTTP calls**: Execute Dograh webhook nodes (POST to external URLs)
- **Integration tools**: Custom plugins registered in Dograh's integration system
- **MCP tools**: External tools via Model Context Protocol (if configured in Dograh)

### 3.6 Session Lifecycle

```
1. SIP call arrives → LiveKit Cloud creates room
2. AgentServer dispatches job → entrypoint.lumina_session()
3. Read room metadata: {deploy_id, org_id, channel, campaign_id, lead_id, ...}
4. Call Dograh API: GET /api/internal/deploy/{deploy_id}/runtime-config
   → Returns {workflow_graph, llm_config, stt_config, tts_config,
              kb_refs, tool_refs, system_prompt, stages, ...}
5. Create session record via Dograh API
6. Translator: workflow_graph → Agno Workflow
7. Build session (realtime or fallback)
8. Session active: audio ↔ STT ↔ Agno Agent(s) ↔ TTS ↔ audio
9. Hangup detected → cleanup:
   a. Stop egress / save recording
   b. Delete LiveKit room
   c. POST /api/internal/sessions/hangup → Dograh
   d. Generate summary, run QA if configured
```

## 4. Dograh API Endpoints Required

All endpoints are internal (`/api/internal/...`), protected by `X-Internal-Token` header, accessible only within the Docker network.

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/internal/deploy/{id}/runtime-config` | GET | Full runtime config: workflow graph, model config, tools, KB refs, system prompt, stages |
| `/api/internal/kb/{org_id}/search` | POST | Knowledge base semantic search |
| `/api/internal/sessions/hangup` | POST | Notify session end with duration, outcome, recording URL |
| `/api/internal/sessions/{id}/events` | POST | Log conversation events (messages, tool calls, stage transitions) |
| `/api/internal/sessions/{id}/context` | PUT | Persist session context (extracted variables, stage results) |
| `/api/internal/tools/{org_id}` | GET | Resolve tool UUIDs to tool definitions |

These are **new endpoints** to be added to Dograh's API. They are read-only or write-only internal endpoints — they do NOT modify existing route handlers or DB schema.

## 5. Models & Providers

### 5.1 Realtime Models (native audio)

| Provider | Model | LiveKit Plugin |
|---|---|---|
| Google | `gemini-3.1-flash-live-preview` | `livekit.plugins.google.realtime` |
| Google | `gemini-2.5-flash-native-audio` | `livekit.plugins.google.realtime` |
| OpenAI | `gpt-4o-realtime-preview` | `livekit.plugins.openai.realtime` |
| AWS | `amazon.nova-sonic-2` | `livekit.plugins.aws.realtime` |

### 5.2 Fallback Models (STT + LLM + TTS)

| Component | Provider Options |
|---|---|
| STT | Deepgram (Nova-3), Google STT |
| LLM | Google Gemini (Flash/Pro), Anthropic Claude (via Bedrock), OpenAI GPT-4o |
| TTS | Cartesia Sonic, ElevenLabs, Google TTS, Deepgram Aura |

### 5.3 Verification Model (SOTA)

For post-call QA analysis and verification:
- **Claude Sonnet 4** (via Anthropic API) — best-in-class for nuanced evaluation
- **GPT-4o** (via OpenAI API) — fallback for structured JSON extraction
- Selected per workflow via `qa_provider` / `qa_model` in Dograh QA node config

## 6. Docker Deployment

```yaml
# docker-compose.yml addition
dograh-livekit:
  build: ./dograh-livekit
  environment:
    - LIVEKIT_URL=${LIVEKIT_URL}
    - LIVEKIT_API_KEY=${LIVEKIT_API_KEY}
    - LIVEKIT_API_SECRET=${LIVEKIT_API_SECRET}
    - DOGRAH_API_URL=http://api:8000
    - DOGRAH_INTERNAL_TOKEN=${DOGRAH_INTERNAL_TOKEN}
    - GOOGLE_API_KEY=${GOOGLE_API_KEY}
    - OPENAI_API_KEY=${OPENAI_API_KEY}
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    - DEEPGRAM_API_KEY=${DEEPGRAM_API_KEY}
    - CARTESIA_API_KEY=${CARTESIA_API_KEY}
  networks:
    - dograh_network
  depends_on:
    - api
  restart: unless-stopped
```

## 7. Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| Dograh API latency impacts realtime audio | Medium | Cache config per deploy_id (TTL 60s); single HTTP call at session start |
| Agno/PostgreSQL compatibility issues | Low | Agno supports pluggable DB backends; PostgreSQL adapter exists in agno.db |
| LiveKit Cloud costs | Low | Same as Luminai — per-minute pricing, shared across tenants |
| Translator misses edge cases in Dograh workflows | Medium | Start with core node types (startCall, agentNode, endCall, globalNode); add integration nodes incrementally |
| Two runtimes diverge in behavior | Low | Both consume same Dograh API → same config → same behavior. Translator is deterministic. |

## 8. Success Criteria

1. A Dograh workflow runs end-to-end via LiveKit SIP, producing the same conversational outcome as via Pipecat
2. Multi-agent routing works: agent transitions between stages based on extracted intent/data
3. Knowledge base search works via Dograh API from within an Agno Agent
4. Webhook nodes fire correctly at session end
5. Session records, usage metrics, and recordings are persisted in Dograh's PostgreSQL
6. The existing Pipecat pathway continues to work unchanged

## 9. Open Questions

- **Tool UUID resolution**: Dograh's tool system uses UUIDs. Does the internal API need to resolve these to executable tool definitions, or does `dograh-livekit` handle resolution itself?
- **Campaign outbound**: For campaign calls, does the outbound trigger flow through Dograh's campaign orchestrator, or directly through `dograh-livekit`'s SIP outbound trunk?
- **WhatsApp channel**: Should WhatsApp also route through the LiveKit pathway (as Luminai does), or stay on Pipecat?
