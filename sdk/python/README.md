# dograh-sdk

Typed builder for Dograh voice-AI workflows. Fetches the node-spec catalog from
the Dograh backend at session start, validates every call against it at the
call site, and produces `ReactFlowDTO`-compatible JSON.

## Install

```bash
pip install dograh-sdk
```

For local development against a checked-out monorepo:

```bash
pip install -e sdk/python/
```

## Usage

```python
from dograh_sdk import DograhClient, Workflow

with DograhClient(base_url="http://localhost:8000", api_key="...") as client:
    wf = Workflow(client=client, name="loan_qualification")

    start = wf.add(
        type="startCall",
        name="greeting",
        prompt="You are Sarah from Acme Loans. Greet the caller warmly.",
        greeting_type="text",
        greeting="Hi {{first_name}}, this is Sarah.",
    )
    qualify = wf.add(
        type="agentNode",
        name="qualify",
        prompt="Ask about loan amount and timeline.",
    )
    done = wf.add(type="endCall", name="done", prompt="Thank the caller.")

    wf.edge(start, qualify, label="interested", condition="Caller expressed interest.")
    wf.edge(qualify, done, label="done", condition="Qualification complete.")

    client.save_workflow(workflow_id=123, workflow=wf)
```

## What gets validated at the call site

The SDK fetches the spec for each node type via `get_node_type` and raises
`ValidationError` immediately when:

- an unknown field is passed (catches typos)
- a required field is missing or empty
- a scalar type is wrong (e.g., string for a boolean)
- an `options` value isn't in the allowed list

When a spec carries an `llm_hint`, the hint is appended to the error message so
an LLM agent can self-correct on retry:

```
tool_uuids: expected tool_refs, got str
  Hint: List of tool UUIDs from `list_tools`.
```

Server-side Pydantic validators run on save and surface anything the SDK lets
through (compound invariants, cross-field rules).

## Environment

```bash
DOGRAH_API_URL=http://localhost:8000   # default
DOGRAH_API_KEY=sk-...                  # sent as X-API-Key
```

## License

BSD 2-Clause — see `LICENSE`.
