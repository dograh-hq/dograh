"""Thin wrapper around the Anthropic Claude SDK for the agent builder.

Builds the CONTRACT system prompt (node/tool/model-config schema + a trimmed,
structurally-accurate GPC reference workflow as a few-shot), calls Claude with
adaptive thinking + high effort + streaming, and returns the parsed JSON object
``{name, workflow_definition, tools, model_config}``.

The Anthropic client is constructed LAZILY inside ``generate`` so a missing
``ANTHROPIC_API_KEY`` never crashes module import — the route turns that into a
clean 503 instead of an unhandled 500.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from loguru import logger

from api.services.agent_builder.templates.gpc_retail import (
    DEFAULT_MODEL_CONFIG,
    GPC_REFERENCE,
)

CLAUDE_MODEL = "claude-opus-4-8"
MAX_TOKENS = 32000


class AgentBuilderConfigError(RuntimeError):
    """Raised when the agent builder is not configured (no API key)."""


class AgentBuilderClaudeError(RuntimeError):
    """Raised when Claude fails to produce a usable JSON object."""


def _contract() -> str:
    """The CONTRACT system prompt: schema + rules + GPC few-shot example."""
    reference = json.dumps(GPC_REFERENCE, indent=2)
    return f"""You are an expert architect for the Dograh voice-AI platform. \
Given a business description and an optional questionnaire, you design a \
complete, valid Dograh `workflow_definition` (a ReactFlow graph), the http_api \
tools it needs, and a default model configuration.

Output ONLY a single JSON object — no prose, no markdown, no code fences. The \
object MUST have exactly these top-level keys:

  - "name": string. A short, descriptive workflow name.
  - "workflow_definition": {{"nodes": [...], "edges": [...], "viewport": {{"x":0,"y":0,"zoom":1}}}}
  - "tools": array of http_api tool specs (may be empty).
  - "model_config": a workflow model-configuration v2 object.

NODE SCHEMA. Every node is {{"id": <unique string>, "type": <type>, \
"position": {{"x": <num>, "y": <num>}}, "data": {{...}}}}. Supported types and \
their `data` fields:

  - "startCall" (EXACTLY ONE required; it is the entry point; no incoming edges):
      data: name, prompt (REQUIRED, non-empty), greeting_type ("text"),
             greeting (spoken opener, supports {{{{first_name}}}} etc.),
             allow_interrupt (bool), add_global_prompt (bool), is_start: true,
             optional tool_uuids: [], extraction_enabled, extraction_prompt,
             extraction_variables.
  - "agentNode" (the conversational steps; at least one incoming edge):
      data: name, prompt (REQUIRED), allow_interrupt, add_global_prompt,
             optional tool_uuids: [], extraction_enabled, extraction_prompt,
             extraction_variables.
  - "endCall" (terminal; at least one incoming edge; no outgoing edges):
      data: name, prompt (REQUIRED), add_global_prompt: false, is_end: true,
             optional extraction_*.
  - "globalNode" (AT MOST ONE; persona/tone; no edges):
      data: name, prompt (REQUIRED).
  - "webhook" (fires after the call; no edges):
      data: name, enabled (bool), http_method ("POST"/"GET"/...),
             endpoint_url, payload_template (a JSON object whose values are
             Jinja templates like "{{{{workflow_run_id}}}}",
             "{{{{gathered_context.foo}}}}").

extraction_variables: [{{"name": <snake_case>, "type": "string"|"number"|"boolean", \
"prompt": <hint>}}].

EDGE SCHEMA. Every edge is {{"id": <unique string>, "source": <node id>, \
"target": <node id>, "data": {{"label": <non-empty>, "condition": <non-empty \
description of when to take this transition>}}}}. Both label and condition are \
REQUIRED and non-empty. Every source/target MUST reference an existing node id.

TOOL SCHEMA (http_api). Each tool is:
  {{"name": <snake_case>, "description": <when to call it & what it returns>,
    "node_ids": [<ids of the agentNode/startCall nodes that may call it>],
    "config": {{"method": "POST"|"GET"|"PUT"|"PATCH"|"DELETE", "url": <https url>,
       "parameters": [{{"name", "type", "description", "required"}}],
       "preset_parameters": [{{"name", "type", "value_template", "required"}}],
       "headers": {{}} }} }}
`parameters` are values the agent supplies at call time; `preset_parameters` are \
injected by Dograh from call context (value_template like \
"{{{{initial_context.phone_number}}}}" or "{{{{gathered_context.budget}}}}"). \
The nodes in node_ids ship with "tool_uuids": [] — the platform fills the real \
uuids in after creating the tool.

MODEL CONFIG. Provide a sensible default v2 object. Default to BYOK realtime \
(google_realtime speech-to-speech + a google LLM):
{json.dumps(DEFAULT_MODEL_CONFIG)}

RULES:
  - Exactly one startCall; at most one globalNode.
  - Design a focused conversational flow: startCall -> probing/agent steps -> \
endCall, branching to additional endCall/agent nodes via edge conditions where \
useful.
  - Put persona/tone in a globalNode and set add_global_prompt: true on nodes \
that should inherit it.
  - Keep prompts voice-friendly: short sentences, one question at a time, no \
characters that cannot be spoken aloud.
  - Only create tools the business clearly needs (e.g. place_order, \
customer_lookup). If the user provided an order webhook URL, include a \
webhook node and/or a place_order http_api tool pointed at it.
  - Output ONLY the JSON object.

Here is a STRUCTURALLY ACCURATE reference (a GPC retail order agent). Copy the \
key shapes exactly; write your own prompts and flow for the user's business:

{reference}
"""


def _build_user_prompt(prompt: str, business: Optional[Dict[str, Any]]) -> str:
    lines = ["Business description:", prompt.strip(), ""]
    if business:
        lines.append("Questionnaire answers (only the provided fields):")
        for key, value in business.items():
            if value is None or value == "":
                continue
            lines.append(f"- {key}: {value}")
        lines.append("")
    lines.append(
        "Design the workflow_definition, tools, and model_config now. "
        "Respond with ONLY the JSON object."
    )
    return "\n".join(lines)


def _strip_to_json(text: str) -> str:
    """Strip ```json fences / surrounding prose and isolate the JSON object."""
    s = (text or "").strip()
    if s.startswith("```"):
        # Drop the opening fence line (``` or ```json) and the closing fence.
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    # If there is still leading/trailing prose, slice to the outermost braces.
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        s = s[start : end + 1]
    return s


def _extract_text(message: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def generate(prompt: str, business: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Call Claude and return the parsed {name, workflow_definition, tools, model_config}.

    Raises AgentBuilderConfigError if ANTHROPIC_API_KEY is unset, and
    AgentBuilderClaudeError on any Claude/parse failure.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AgentBuilderConfigError(
            "Agent builder is not configured: set ANTHROPIC_API_KEY"
        )

    try:
        import anthropic  # Imported lazily so a missing dep/key never breaks import.
    except ImportError as e:  # pragma: no cover - dependency wiring
        raise AgentBuilderConfigError(
            "Agent builder is not configured: the 'anthropic' package is not installed"
        ) from e

    client = anthropic.Anthropic()
    user_prompt = _build_user_prompt(prompt, business)

    try:
        with client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=[
                {
                    "type": "text",
                    "text": _contract(),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            message = stream.get_final_message()
    except Exception as e:  # noqa: BLE001 - any SDK/transport failure -> 502
        logger.error(f"Agent builder Claude request failed: {e}")
        raise AgentBuilderClaudeError(f"Claude request failed: {e}") from e

    raw = _extract_text(message)
    if not raw.strip():
        raise AgentBuilderClaudeError("Claude returned an empty response")

    try:
        data = json.loads(_strip_to_json(raw))
    except (ValueError, TypeError) as e:
        logger.error(f"Agent builder could not parse Claude JSON: {e}; raw={raw[:500]}")
        raise AgentBuilderClaudeError(
            "Could not parse a JSON workflow from Claude's response"
        ) from e

    if not isinstance(data, dict) or "workflow_definition" not in data:
        raise AgentBuilderClaudeError(
            "Claude response did not contain a workflow_definition"
        )
    return data
