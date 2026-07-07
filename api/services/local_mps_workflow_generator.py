"""
Local MPS Workflow Generator — drop-in replacement for Dograh MPS Agent Builder.

Run:
    python -m api.services.local_mps_workflow_generator

Or point MPS_API_URL at this service in .env:
    MPS_API_URL=http://localhost:9001

The service exposes:
    POST /api/v1/workflow/create-workflow

It uses the same LLM already configured in your Dograh instance to generate
a complete workflow definition from `call_type`, `use_case`,
`activity_description`, and `language`.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ────────────────────────────────────────────────────────────────────
# Node types & fields (mirrors api/services/workflow/dto.py node_specs)
# Keep this in sync with the node definitions in dto.py.
# ────────────────────────────────────────────────────────────────────
NODE_SPECS_PROMPT = """
You are a workflow generator for Dograh, a Voice AI platform. Given a user's requirements, generate a complete workflow definition in JSON format.

## Available Node Types

### 1. startCall (exactly one required — entry point)
Fields:
- name: string (default "Start Call") — short identifier
- greeting_type: "text" | "audio" (default "text")
- greeting: string — TTS text spoken at start. Supports {{template_variables}}.
- greeting_recording_id: string — pre-recorded audio ID (only when greeting_type="audio")
- prompt: string — system prompt for the opening turn. REQUIRED.
- allow_interrupt: boolean (default false) — user can interrupt agent
- add_global_prompt: boolean (default true) — prepend global prompt
- delayed_start: boolean (default false) — wait before speaking
- delayed_start_duration: number (default 2.0) — seconds to wait
- extraction_enabled: boolean (default false) — LLM extraction pass
- extraction_prompt: string — instructions for extraction
- extraction_variables: array of {name, type, prompt}
- tool_uuids: string[] — tool UUIDs
- document_uuids: string[] — document UUIDs
- pre_call_fetch_enabled: boolean (default false)
- pre_call_fetch_url: string — endpoint URL for pre-call data fetch
- pre_call_fetch_credential_uuid: string

### 2. agentNode (mid-call conversational step — can have many)
Fields:
- name: string (default "Agent") — step identifier
- prompt: string — system prompt for this step. REQUIRED.
- allow_interrupt: boolean (default true)
- add_global_prompt: boolean (default true)
- extraction_enabled: boolean (default false)
- extraction_prompt: string
- extraction_variables: array of {name, type, prompt}
- tool_uuids: string[]
- document_uuids: string[]

### 3. endCall (terminal node — can have many, reached via edges)
Fields:
- name: string (default "End Call")
- prompt: string — closing exchange. REQUIRED.
- add_global_prompt: boolean (default false)
- extraction_enabled: boolean (default false)
- extraction_prompt: string
- extraction_variables: array of {name, type, prompt}

### 4. globalNode (optional, max 1 — persona/tone prepended to all agents)
Fields:
- name: string (default "Global Node")
- prompt: string — global prompt prepended to agents with add_global_prompt=true. REQUIRED.

### 5. trigger (optional, max 1 — public HTTP endpoint)
Fields:
- name: string (default "API Trigger")
- enabled: boolean (default true)
- trigger_path: string — auto-generated UUID

## Edge format
Edges connect nodes:
- id: string — unique edge ID (uuid-like)
- source: string — source node ID
- target: string — target node ID
- sourceHandle and targetHandle are auto-detected — DO NOT include them in edges
- data: { label: string, condition: string (REQUIRED, same as label for simple edges) }

## Rules
- YOU MUST ONLY use the node types listed above: startCall, agentNode, endCall, globalNode, trigger.
- DO NOT invent new node types. Only startCall, agentNode, endCall, globalNode, and trigger are valid.
- Exactly one startCall node
- startCall connects to one or more agentNodes
- agentNodes connect to other agentNodes or endCall nodes
- Each endCall is a terminal leaf
- globalNode is standalone (no edges)
- trigger is standalone (no edges)
- Every node needs a unique ID (use short descriptive IDs like "greeting", "qualify", "close_good", etc.)
- Generate both nodes AND edges
- Position nodes in a logical vertical flow: startCall at top, agentNodes in middle, endCall at bottom
- For a simple workflow: startCall → agentNode → endCall
- For complex workflows: startCall → multiple agentNodes with edge labels describing transitions

## Language
Generate all prompts and greetings in the user's requested language. If language is "it" (Italian), write all text in Italian. If "en", write in English.

## Output format
Return ONLY valid JSON — no commentary, no markdown fences.

Here is a concrete example of a correct simple workflow:
{
  "name": "Lead Qualification",
  "workflow_definition": {
    "nodes": [
      {"id": "start", "type": "startCall", "position": {"x": 400, "y": 0}, "data": {"name": "Start Call", "prompt": "Greet the caller and ask how you can help.", "greeting_type": "text", "greeting": "Hi, how can I help you today?", "allow_interrupt": true, "add_global_prompt": true}},
      {"id": "qualify", "type": "agentNode", "position": {"x": 400, "y": 150}, "data": {"name": "Qualify Lead", "prompt": "Ask qualifying questions to determine if the caller is a good fit.", "allow_interrupt": true, "add_global_prompt": true}},
      {"id": "close", "type": "endCall", "position": {"x": 400, "y": 300}, "data": {"name": "End Call", "prompt": "Thank the caller and end the conversation."}}
    ],
    "edges": [
      {"id": "e1", "source": "start", "target": "qualify", "data": {"label": "Start", "condition": "Start"}},
      {"id": "e2", "source": "qualify", "target": "close", "data": {"label": "Done", "condition": "Done"}}
    ]
  }
}

Now generate YOUR workflow following the same structure, using ONLY startCall, agentNode, endCall, globalNode, and trigger node types:
"""


class CreateWorkflowRequest(BaseModel):
    call_type: str = "INBOUND"
    use_case: str = ""
    activity_description: str = ""
    language: str = "en"


app = FastAPI(title="Local MPS Workflow Generator")


def _generate_with_ollama(prompt: str, model: str = "llama3.2") -> str:
    """Call a local Ollama instance to generate the workflow."""
    import httpx

    api_url = os.getenv("OLLAMA_API_URL", "http://localhost:11434")
    response = httpx.post(
        f"{api_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=httpx.Timeout(120.0),
    )
    response.raise_for_status()
    return response.json()["response"]


def _generate_with_openai(prompt: str) -> str:
    """Call OpenAI-compatible API to generate the workflow."""
    import httpx

    api_key = os.getenv("OPENAI_API_KEY")
    api_url = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1")
    model = os.getenv("LOCAL_MPS_MODEL", "gpt-4o-mini")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = httpx.post(
        f"{api_url}/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": NODE_SPECS_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        headers=headers,
        timeout=httpx.Timeout(120.0),
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _generate_with_vertex(prompt: str) -> str:
    """Call Vertex AI (Gemini) to generate the workflow.

    Auth via GOOGLE_APPLICATION_CREDENTIALS service account.
    """
    import httpx
    import google.auth
    import google.auth.transport.requests

    project = os.getenv("VERTEX_PROJECT_ID", "")
    location = os.getenv("VERTEX_LOCATION", "us-central1")
    model = os.getenv("LOCAL_MPS_MODEL", "gemini-3.5-flash")

    if not project:
        raise ValueError("VERTEX_PROJECT_ID is required for Vertex AI workflow generation")

    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())

    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/"
        f"publishers/google/models/{model}:generateContent"
    )

    response = httpx.post(
        url,
        json={
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": NODE_SPECS_PROMPT + "\n\n" + prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 4096,
            },
        },
        headers={
            "Authorization": f"Bearer {creds.token}",
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(120.0),
    )
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from LLM response, handling markdown fences."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract from markdown code fences
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find a JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from response: {text[:200]}...")


def _ensure_trigger_paths(workflow_def: dict[str, Any]) -> dict[str, Any]:
    """Auto-generate trigger_path for any trigger node that lacks one."""
    for node in workflow_def.get("nodes", []):
        data = node.get("data", {})
        if node.get("type") == "trigger" and not data.get("trigger_path"):
            data["trigger_path"] = str(uuid.uuid4())
    return workflow_def


def _ensure_ids(workflow_def: dict[str, Any]) -> dict[str, Any]:
    """Ensure all nodes and edges have IDs."""
    seen_ids = set()
    for node in workflow_def.get("nodes", []):
        if "id" not in node:
            node["id"] = str(uuid.uuid4())[:8]
        if not node.get("type"):
            node["type"] = "agentNode"
        if "position" not in node:
            node["position"] = {"x": 400, "y": len(seen_ids) * 150}
        if "data" not in node:
            node["data"] = {}
        seen_ids.add(node["id"])

    for i, edge in enumerate(workflow_def.get("edges", [])):
        if "id" not in edge:
            edge["id"] = f"edge-{i}"
    return workflow_def


def _sanitize_workflow(workflow_def: dict[str, Any]) -> dict[str, Any]:
    """Sanitize and validate the generated workflow definition."""
    VALID_TYPES = {"startCall", "agentNode", "endCall", "globalNode", "trigger", "webhook"}

    # Remove any nodes with unknown types (LLM hallucination guard)
    original_count = len(workflow_def.get("nodes", []))
    workflow_def["nodes"] = [
        n for n in workflow_def.get("nodes", [])
        if n.get("type") in VALID_TYPES
    ]
    removed = original_count - len(workflow_def["nodes"])
    if removed:
        logger.warning(f"Removed {removed} node(s) with unknown types")

    # Remove edges that reference removed nodes
    valid_ids = {n["id"] for n in workflow_def["nodes"]}
    workflow_def["edges"] = [
        e for e in workflow_def.get("edges", [])
        if e.get("source") in valid_ids and e.get("target") in valid_ids
    ]

    # Auto-fill missing edge label and condition (validation safety net)
    for edge in workflow_def.get("edges", []):
        data = edge.get("data") or {}
        if not isinstance(data, dict):
            edge["data"] = data = {}
        fallback = f"Edge {edge.get('id', '?')}"
        if not data.get("label"):
            data["label"] = fallback
        if not data.get("condition"):
            data["condition"] = data.get("label") or fallback
        # Remove explicit handle IDs — React Flow auto-connects via position
        edge.pop("sourceHandle", None)
        edge.pop("targetHandle", None)

    _ensure_ids(workflow_def)
    _ensure_trigger_paths(workflow_def)

    # Ensure startCall exists
    has_start = any(n.get("type") == "startCall" for n in workflow_def.get("nodes", []))
    if not has_start:
        workflow_def.setdefault("nodes", []).insert(
            0,
            {
                "id": "start",
                "type": "startCall",
                "position": {"x": 400, "y": 0},
                "data": {
                    "name": "Start Call",
                    "prompt": "Greet the caller and ask how you can help.",
                    "greeting_type": "text",
                    "greeting": "Hi, how can I help you today?",
                    "allow_interrupt": True,
                },
            },
        )

    # Ensure labels are strings
    for edge in workflow_def.get("edges", []):
        if isinstance(edge.get("data"), dict) and edge["data"].get("label"):
            pass  # keep as-is
        else:
            edge.setdefault("data", {})["label"] = ""

    return workflow_def


def _build_user_prompt(call_type: str, use_case: str, activity_description: str, language: str) -> str:
    """Build the user prompt for the LLM."""
    lang_name = "Italian" if language == "it" else "English"
    return f"""Generate a voice agent workflow with these requirements:

Call Type: {call_type}
Use Case: {use_case}
Activity Description: {activity_description}
Language: {lang_name} ({language})

Generate an appropriate workflow with startCall, agentNodes, and endCall nodes.
For simple use cases, create: startCall → agentNode → endCall
For complex use cases, create multiple agentNodes with appropriate edge transitions.

Generate all prompts and greetings in {lang_name}.
The workflow name should be "{use_case} - {call_type}".
"""


def _select_generator():
    """Select which LLM backend to use based on environment variables."""
    provider = os.getenv("LOCAL_MPS_PROVIDER", "vertex")

    if provider == "ollama":
        return _generate_with_ollama
    elif provider == "vertex":
        return _generate_with_vertex
    return _generate_with_openai


def generate_workflow(
    call_type: str,
    use_case: str,
    activity_description: str,
    language: str,
) -> dict[str, Any]:
    """Generate a workflow definition using the configured LLM."""
    generator = _select_generator()
    user_prompt = _build_user_prompt(call_type, use_case, activity_description, language)

    raw_response = generator(user_prompt)
    workflow_data = _extract_json(raw_response)

    # Sanitize
    workflow_def = workflow_data.get("workflow_definition", workflow_data)
    workflow_def = _sanitize_workflow(workflow_def)
    name = workflow_data.get("name", f"{use_case} - {call_type}")

    return {"name": name, "workflow_definition": workflow_def}


# ────────────────────────────────────────────────────────────────────
# API Endpoint — mirrors Dograh MPS /api/v1/workflow/create-workflow
# ────────────────────────────────────────────────────────────────────
@app.post("/api/v1/workflow/create-workflow")
async def create_workflow(request: CreateWorkflowRequest):
    """Generate a workflow from a natural language description."""
    try:
        result = generate_workflow(
            call_type=request.call_type,
            use_case=request.use_case,
            activity_description=request.activity_description,
            language=request.language,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Workflow generation failed: {str(e)}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "local-mps-workflow-generator"}


if __name__ == "__main__":
    port = int(os.getenv("LOCAL_MPS_PORT", "9001"))
    print(f"Starting Local MPS Workflow Generator on port {port}")
    print(f"Configure your Dograh instance with: MPS_API_URL=http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
