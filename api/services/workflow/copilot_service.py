"""
Voice Agent Copilot Service.
Enables interactive, conversational creation of Voice AI workflows using structured LLM outputs.
Follows LLM resolution hierarchy: User BYOK -> Master Platform Key -> Server Environment Key.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

from api.db import db_client
from api.db.models import UserModel
from api.schemas.copilot import (
    AgentCopilotResponse,
    ChatMessage,
    CopilotChatRequest,
    InCanvasCopilotRequest,
    InCanvasCopilotResponse,
    WorkflowDraft,
)
from api.services.configuration.ai_model_configuration import (
    get_resolved_ai_model_configuration,
)
from api.services.platform_keys import get_default_platform_provider_and_model
from api.services.workflow.canvas_mcp import (
    CANVAS_MCP_TOOLS_SCHEMA,
    execute_canvas_mcp_tool,
)

COPILOT_SYSTEM_PROMPT = """You are the Senior Voice AI Telephony Architect for the Dograh Calling SaaS Platform.
Your mission is to generate production-ready, enterprise-grade Voice AI workflows matching Dograh's canonical 4-node architecture.

CANONICAL DOGRAH TOPOLOGY:
Every complete workflow MUST contain exactly 4 nodes and 3 custom animated edges:
1. `globalNode` (ID "0", position {"x": -325, "y": 480}):
   Contains:
   - `# OVERALL GOAL`: Agent persona, name, company, use case summary, response length (2-3 sentences, 10-25 words max).
   - `## Response Language`: Conversational language (English, Hindi, Hinglish), no special characters, TTS friendly.
   - `## HANDLING ASR / TRANSCRIPTION ISSUES`: Handling noisy phone audio, clarification phrases ("sorry, did not catch that", "hey sorry, some noise there - could you repeat?"), never say caller's name if misheard.
   - `## SUMMARY OF KEY BEHAVIOR`: 10-25 words, relaxed tone, wait after questions, handle objections, clean tool syntax.

2. `startCall` (ID "1", position {"x": 175, "y": 60}):
   Contains:
   - `# MAIN ACTION POINT AT THIS STAGE`: Greet the caller, state name and company, ask name and how to help in a single natural opening statement.
   - `## Call flow`: Stay in this node for the opening 1 to 3 turns until context is clear. Move to Main Agenda only when context is established.
   - `## Critical rules`: End with question or tool call, never mix text and tool calls.
   - Data flags: `"add_global_prompt": true`, `"is_start": true`, `"allow_interrupt": true`, `"greeting_type": "text"`.

3. `agentNode` (ID "2", position {"x": 615.5, "y": 476}):
   Contains:
   - `# MAIN ACTION POINT AT THIS STEP:`
   - `## Usable details and Main Agenda`
   - `<FORMAT>` block with:
     - `## USABLE DETAILS and GOALS AT THIS STAGE:` (Key facts, pricing, duration, curriculum, features).
     - Specific Qualification Questions (1, 2, 3...)
     - Lead Scoring rules (Hot: immediate intent, Warm: interested, Cold: minimal intent).
     - Callback / Demo / WhatsApp scheduling actions.
     - Wrap up instructions.
   - `</FORMAT>`
   - `## Flow of call`: Owns the working part of the conversation, asks questions one by one, handles objections, light wrap up.
   - `## Constraints`: Never ask answered questions, no false promises.
   - Data flags: `"add_global_prompt": true`, `"allow_interrupt": true`, `"tool_uuids": []`.

4. `endCall` (ID "4", position {"x": 175, "y": 900}):
   Contains:
   - `# Main Action Point for This Stage`: Brief closing of 6-8 words (e.g. "Thank you for the call. Have a wonderful day"), do not ask questions, end call.
   - Data flags: `"add_global_prompt": false`, `"is_end": true`.

5. EDGES (3 custom animated edges):
   - ID "1-2": Source "1" -> Target "2", Label: "Move to Main Agenda", Condition: "Choose this pathway when you have greeted the caller, learned their name, and understood enough of their request to start handling the main task."
   - ID "1-4": Source "1" -> Target "4", Label: "End call", Condition: "Choose this pathway whenever you are supposed to end the call from the opening stage, such as a wrong number, wrong company, or the caller does not want to continue."
   - ID "2-4": Source "2" -> Target "4", Label: "End call", Condition: "Choose this pathway whenever the issue is fully handled and the caller has nothing else to discuss."

OUTPUT FORMAT:
You must strictly respond with a single JSON object matching this schema:
{
  "reply_message": "Summary of generated voice workflow...",
  "suggested_quick_replies": ["Looks good!", "Run Test Call", "Adjust Questions"],
  "is_ready_to_test": true,
  "workflow_draft": {
    "name": "Concise Workflow Name",
    "call_type": "inbound" or "outbound",
    "language": "en" or "hi",
    "first_message": "Opening greeting line spoken when call connects",
    "system_prompt": "Main Agenda prompt for agentNode...",
    "questions_to_ask": ["Question 1", "Question 2"],
    "workflow_definition": {
      "nodes": [
        {
          "id": "0",
          "type": "globalNode",
          "position": {"x": -325, "y": 480},
          "data": {
            "name": "Global Node",
            "prompt": "Global prompt with OVERALL GOAL, Response Language, HANDLING ASR / TRANSCRIPTION ISSUES, and SUMMARY OF KEY BEHAVIOR...",
            "allow_interrupt": true,
            "is_static": false
          }
        },
        {
          "id": "1",
          "type": "startCall",
          "position": {"x": 175, "y": 60},
          "data": {
            "name": "start call",
            "prompt": "Start call prompt with MAIN ACTION POINT AT THIS STAGE, Call flow, and Critical rules...",
            "greeting": "Opening greeting line",
            "greeting_type": "text",
            "allow_interrupt": true,
            "add_global_prompt": true,
            "is_start": true
          }
        },
        {
          "id": "2",
          "type": "agentNode",
          "position": {"x": 615.5, "y": 476},
          "data": {
            "name": "Main Agenda and Questions",
            "prompt": "Detailed agent prompt with MAIN ACTION POINT, <FORMAT> Usable details, FAQ, Qualification Questions, Lead Scoring, Scheduling, Wrap up </FORMAT>, Flow of call, and Constraints...",
            "allow_interrupt": true,
            "add_global_prompt": true,
            "tool_uuids": []
          }
        },
        {
          "id": "4",
          "type": "endCall",
          "position": {"x": 175, "y": 900},
          "data": {
            "name": "End Call",
            "prompt": "Brief 6-8 words closing response, end call immediately.",
            "allow_interrupt": true,
            "add_global_prompt": false,
            "is_end": true
          }
        }
      ],
      "edges": [
        {
          "id": "1-2",
          "source": "1",
          "target": "2",
          "type": "custom",
          "animated": true,
          "data": {
            "label": "Move to Main Agenda",
            "condition": "Choose this pathway when you have greeted the caller, learned their name, and understood enough of their request to start handling the main task."
          }
        },
        {
          "id": "1-4",
          "source": "1",
          "target": "4",
          "type": "custom",
          "animated": true,
          "data": {
            "label": "End call",
            "condition": "Choose this pathway whenever you are supposed to end the call from the opening stage, such as a wrong number, wrong company, or the caller does not want to continue."
          }
        },
        {
          "id": "2-4",
          "source": "2",
          "target": "4",
          "type": "custom",
          "animated": true,
          "data": {
            "label": "End call",
            "condition": "Choose this pathway whenever the issue is fully handled and the caller has nothing else to discuss."
          }
        }
      ]
    }
  }
}
"""


def _clean_and_parse_json(raw_text: str) -> dict:
    """Robustly extract and parse JSON from LLM output, handling markdown code blocks."""
    text = raw_text.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        return json.loads(text)
    except Exception:
        # Fallback: search for outermost JSON object braces
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _ensure_valid_reactflow_graph(draft: WorkflowDraft) -> Dict[str, Any]:
    """Ensure the workflow definition strictly satisfies Dograh ReactFlowDTO requirements:

    1. startCall, agentNode, endCall, and optional globalNode must all have valid fields.
    2. Every edge MUST have data with non-empty 'label' and 'condition'.
    """
    wf_def = draft.workflow_definition or {}
    raw_nodes: List[Dict[str, Any]] = wf_def.get("nodes", [])
    raw_edges: List[Dict[str, Any]] = wf_def.get("edges", [])

    has_start = any(n.get("type") == "startCall" for n in raw_nodes)
    has_agent = any(n.get("type") == "agentNode" for n in raw_nodes)
    has_end = any(n.get("type") == "endCall" for n in raw_nodes)

    default_greeting = (
        draft.first_message
        or "Hello! Thank you for calling. How can I assist you today?"
    )
    default_prompt = (
        draft.system_prompt
        or "You are a helpful and polite voice assistant for Dograh."
    )

    if not (has_start and has_agent and has_end):
        # Canonical Dograh 4-node topology
        nodes = [
            {
                "id": "0",
                "type": "globalNode",
                "position": {"x": -325, "y": 480},
                "data": {
                    "name": "Global Node",
                    "prompt": (
                        f"# OVERALL GOAL\n\nYou are a professional Voice Assistant. We are handling calls for **{draft.name}**. "
                        "Keep responses short, 2-3 sentences. 10 - 25 words total.\n\n"
                        "## Response Language\nYou are a Voice AI Agent who can speak in multiple languages (English, Hindi, Hinglish). "
                        "Your output is played over TTS, so do not generate markdown asterisks or special characters.\n\n"
                        "## HANDLING ASR / TRANSCRIPTION ISSUES\nHandle background noise gracefully. If unclear, ask: 'sorry, did not catch that'. "
                        "Never mention ASR errors.\n\n"
                        "## SUMMARY OF KEY BEHAVIOR\n- Keep responses short, 10-25 words\n- Speak in relaxed tone\n- Handle objections politely"
                    ),
                    "allow_interrupt": True,
                    "is_static": False,
                },
            },
            {
                "id": "1",
                "type": "startCall",
                "position": {"x": 175, "y": 60},
                "data": {
                    "name": "start call",
                    "prompt": (
                        "# MAIN ACTION POINT AT THIS STAGE\n\nGreet the user, introduce yourself and company, and ask how you can help.\n\n"
                        "## Call flow\nStay in this node for the opening 1 to 3 turns until intent is clear. "
                        "Move to Main Agenda once intent is understood. If wrong number or spam, choose End Call."
                    ),
                    "greeting": default_greeting,
                    "greeting_type": "text",
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "is_start": True,
                },
            },
            {
                "id": "2",
                "type": "agentNode",
                "position": {"x": 615.5, "y": 476},
                "data": {
                    "name": "Main Agenda and Questions",
                    "prompt": default_prompt,
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "tool_uuids": [],
                },
            },
            {
                "id": "4",
                "type": "endCall",
                "position": {"x": 175, "y": 900},
                "data": {
                    "name": "End Call",
                    "prompt": (
                        "# Main Action Point for This Stage\n\nGenerate a brief response (6-8 words) like: "
                        "'Thank you for the call. Have a wonderful day.' Then end call."
                    ),
                    "allow_interrupt": True,
                    "add_global_prompt": False,
                    "is_end": True,
                },
            },
        ]
        edges = [
            {
                "id": "1-2",
                "source": "1",
                "target": "2",
                "type": "custom",
                "animated": True,
                "data": {
                    "label": "Move to Main Agenda",
                    "condition": "Choose this pathway when you have greeted the caller, learned their name, and understood enough of their request to start handling the main task.",
                },
            },
            {
                "id": "1-4",
                "source": "1",
                "target": "4",
                "type": "custom",
                "animated": True,
                "data": {
                    "label": "End call",
                    "condition": "Choose this pathway whenever you are supposed to end the call from the opening stage, such as a wrong number, wrong company, or the caller does not want to continue.",
                },
            },
            {
                "id": "2-4",
                "source": "2",
                "target": "4",
                "type": "custom",
                "animated": True,
                "data": {
                    "label": "End call",
                    "condition": "Choose this pathway whenever the issue is fully handled and the caller has nothing else to discuss.",
                },
            },
        ]
    else:
        # Sanitize and ensure required fields on every existing node & edge
        nodes = []
        for idx, n in enumerate(raw_nodes):
            node_copy = dict(n)
            n_type = node_copy.get("type", "agentNode")
            data = dict(node_copy.get("data", {}))

            # Ensure position exists
            if not isinstance(node_copy.get("position"), dict):
                node_copy["position"] = {"x": 250, "y": 60 + idx * 180}

            # Enforce required prompt for each node type
            if n_type == "startCall":
                data["name"] = data.get("name") or "Start Call"
                data["prompt"] = (
                    data.get("prompt") or "Call initiated. Greet caller warmly."
                )
                data["greeting"] = data.get("greeting") or default_greeting
                data["greeting_type"] = "text"
                data["allow_interrupt"] = True
            elif n_type == "agentNode":
                data["name"] = data.get("name") or draft.name or "Voice Agent"
                existing_p = data.get("prompt", "")
                agent_count = sum(1 for x in raw_nodes if x.get("type") == "agentNode")
                if agent_count <= 1:
                    if draft.system_prompt and (
                        len(draft.system_prompt) > len(existing_p)
                        or "system_prompt" in existing_p.lower()
                        or len(existing_p) < 200
                    ):
                        data["prompt"] = draft.system_prompt
                    else:
                        data["prompt"] = existing_p or default_prompt
                else:
                    # Multi-node workflow: preserve specialized prompt if already substantive
                    if not existing_p or len(existing_p) < 60 or "system_prompt" in existing_p.lower():
                        data["prompt"] = draft.system_prompt or default_prompt
                    else:
                        data["prompt"] = existing_p
                data["allow_interrupt"] = True
            elif n_type == "endCall":
                data["name"] = data.get("name") or "End Call"
                data["prompt"] = (
                    data.get("prompt")
                    or "Politely thank the caller and say goodbye."
                )
            elif n_type == "globalNode":
                data["name"] = data.get("name") or "Global Persona"
                data["prompt"] = data.get("prompt") or "Speak clearly and politely."

            node_copy["data"] = data
            nodes.append(node_copy)

        edges = []
        for idx, e in enumerate(raw_edges):
            edge_copy = dict(e)
            data = dict(edge_copy.get("data", {})) if isinstance(edge_copy.get("data"), dict) else {}

            if not data.get("label"):
                data["label"] = "Start Conversation" if idx == 0 else "Call Concluded"
            if not data.get("condition"):
                data["condition"] = (
                    "After greeting finishes, proceed to main conversation."
                    if idx == 0
                    else "Conversation is concluded or caller wishes to hang up."
                )

            edge_copy["data"] = data
            edge_copy["type"] = "custom"
            edges.append(edge_copy)

    # Validate against Dograh's ReactFlowDTO to guarantee zero validation errors
    try:
        from api.services.workflow.dto import ReactFlowDTO
        ReactFlowDTO.model_validate({"nodes": nodes, "edges": edges})
    except Exception as exc:
        logger.warning("Graph normalization fallback triggered due to: {}", exc)
        # Safe guaranteed fallback
        nodes = [
            {
                "id": "start-1",
                "type": "startCall",
                "position": {"x": 250, "y": 50},
                "data": {
                    "name": "Start Call",
                    "prompt": "Call initiated. Greet caller warmly.",
                    "greeting": default_greeting,
                    "greeting_type": "text",
                    "allow_interrupt": True,
                },
            },
            {
                "id": "agent-1",
                "type": "agentNode",
                "position": {"x": 250, "y": 240},
                "data": {
                    "name": draft.name or "Voice Agent",
                    "prompt": default_prompt,
                    "allow_interrupt": True,
                },
            },
            {
                "id": "end-1",
                "type": "endCall",
                "position": {"x": 250, "y": 460},
                "data": {
                    "name": "End Call",
                    "prompt": "Politely thank the caller and say goodbye.",
                },
            },
        ]
        edges = [
            {
                "id": "e-start-agent",
                "source": "start-1",
                "target": "agent-1",
                "type": "custom",
                "data": {
                    "label": "Start Conversation",
                    "condition": "After greeting finishes, proceed to main conversation.",
                },
            },
            {
                "id": "e-agent-end",
                "source": "agent-1",
                "target": "end-1",
                "type": "custom",
                "data": {
                    "label": "Call Concluded",
                    "condition": "Conversation is concluded or caller wishes to hang up.",
                },
            },
        ]

    return {"nodes": nodes, "edges": edges}


async def resolve_llm_credentials(
    organization_id: Optional[int],
) -> Tuple[str, str, str, str]:
    """Resolve (provider, api_key, model, base_url) following:

    User BYOK -> Platform Master Key -> Server .env Fallback.
    """
    # 1. Check User/Org BYOK
    if organization_id:
        try:
            resolved = await get_resolved_ai_model_configuration(
                organization_id=organization_id
            )
            if resolved.effective.llm and resolved.effective.llm.api_key:
                provider = str(resolved.effective.llm.provider).lower()
                api_key = (
                    resolved.effective.llm.api_key
                    if isinstance(resolved.effective.llm.api_key, str)
                    else resolved.effective.llm.api_key[0]
                )
                model = getattr(resolved.effective.llm, "model", None)
                if provider == "groq":
                    return (
                        "groq",
                        api_key,
                        model or "llama-3.3-70b-versatile",
                        "https://api.groq.com/openai/v1",
                    )
                elif provider in ("openai", "dograh"):
                    return (
                        "openai",
                        api_key,
                        model or "gpt-4o-mini",
                        "https://api.openai.com/v1",
                    )
        except Exception as e:
            logger.warning(
                "Could not resolve organization model config for copilot: {}", e
            )

    # 2. Check Platform Master LLM Key
    try:
        from api.services.platform_keys import _MASTER_KEYS_CACHE, refresh_master_keys_cache
        if not _MASTER_KEYS_CACHE.get("llm"):
            await refresh_master_keys_cache()

        master_def = get_default_platform_provider_and_model("llm")
        if master_def:
            prov, key, def_model = master_def
            prov = prov.lower()
            if prov == "groq":
                return (
                    "groq",
                    key,
                    def_model or "llama-3.3-70b-versatile",
                    "https://api.groq.com/openai/v1",
                )
            elif prov == "openai":
                return (
                    "openai",
                    key,
                    def_model or "gpt-4o-mini",
                    "https://api.openai.com/v1",
                )
    except Exception as e:
        logger.warning("Could not resolve platform master key for copilot: {}", e)

    # 3. Check Server Environment Variables
    env_openai_key = os.getenv("OPENAI_API_KEY")
    if env_openai_key and not env_openai_key.startswith("YOUR_ACTUAL"):
        return ("openai", env_openai_key, "gpt-4o-mini", "https://api.openai.com/v1")

    env_groq_key = os.getenv("GROQ_API_KEY")
    if env_groq_key and not env_groq_key.startswith("YOUR_ACTUAL"):
        return (
            "groq",
            env_groq_key,
            "llama-3.3-70b-versatile",
            "https://api.groq.com/openai/v1",
        )

    # Fallback placeholder
    return (
        "openai",
        env_openai_key or "",
        "gpt-4o-mini",
        "https://api.openai.com/v1",
    )


async def process_copilot_turn(
    request: CopilotChatRequest,
    user: UserModel,
) -> AgentCopilotResponse:
    """Process a single turn of conversation between the user and Copilot."""
    provider, api_key, model, base_url = await resolve_llm_credentials(
        user.selected_organization_id
    )

    if not api_key or api_key.startswith("YOUR_ACTUAL"):
        fallback_draft = request.current_workflow_draft or WorkflowDraft(
            name="New Voice Agent",
            call_type="inbound",
            language="en",
            first_message="Hello! How can I assist you today?",
            system_prompt="You are a helpful customer support agent.",
            questions_to_ask=["Caller's Name", "Reason for calling"],
            workflow_definition={},
        )
        fallback_draft.workflow_definition = _ensure_valid_reactflow_graph(
            fallback_draft
        )
        return AgentCopilotResponse(
            reply_message=(
                "Please configure an OpenAI or Groq API key in your Model Settings or "
                "server environment (OPENAI_API_KEY) to enable full AI Copilot generation."
            ),
            suggested_quick_replies=[
                "Customer Support Agent",
                "Lead Qualification",
                "Appointment Booking",
            ],
            is_ready_to_test=False,
            workflow_draft=fallback_draft,
            workflow_id=request.current_workflow_id,
        )

    # Prepare LLM messages with sliding window of last 10 messages
    formatted_messages = [{"role": "system", "content": COPILOT_SYSTEM_PROMPT}]

    # Provide context of existing draft if any
    if request.current_workflow_draft:
        draft_context = (
            f"CURRENT WORKFLOW DRAFT:\n"
            f"Name: {request.current_workflow_draft.name}\n"
            f"Call Type: {request.current_workflow_draft.call_type}\n"
            f"First Message: {request.current_workflow_draft.first_message}\n"
            f"System Prompt: {request.current_workflow_draft.system_prompt}\n"
            f"Questions to ask: {json.dumps(request.current_workflow_draft.questions_to_ask)}"
        )
        formatted_messages.append({"role": "system", "content": draft_context})

    # Sliding window: keep last 10 turns to avoid token limits / timeout
    recent_messages = request.messages[-10:] if len(request.messages) > 10 else request.messages
    for msg in recent_messages:
        formatted_messages.append({"role": msg.role, "content": msg.content})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": formatted_messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.7,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            json=payload,
            headers=headers,
        )

        if resp.status_code != 200:
            logger.error(
                "Copilot LLM call failed with {}: {}", resp.status_code, resp.text
            )
            raise RuntimeError(
                f"LLM API error ({resp.status_code}): {resp.text[:200]}"
            )

        resp_data = resp.json()
        content = resp_data["choices"][0]["message"]["content"]
        parsed = _clean_and_parse_json(content)

    # Validate and normalize draft
    draft_data = parsed.get("workflow_draft", {})
    draft = WorkflowDraft(
        name=draft_data.get("name") or "New Voice Agent",
        call_type=draft_data.get("call_type") or "inbound",
        language=draft_data.get("language") or "en",
        first_message=draft_data.get("first_message")
        or "Hello! How can I assist you?",
        system_prompt=draft_data.get("system_prompt")
        or "You are a professional voice assistant.",
        questions_to_ask=draft_data.get("questions_to_ask") or [],
        workflow_definition=draft_data.get("workflow_definition") or {},
    )
    draft.workflow_definition = _ensure_valid_reactflow_graph(draft)

    saved_workflow_id = request.current_workflow_id

    # Auto-save draft to database if requested
    if request.save_draft and user.selected_organization_id:
        try:
            if not saved_workflow_id:
                new_wf = await db_client.create_workflow(
                    name=draft.name,
                    workflow_definition=draft.workflow_definition,
                    user_id=user.id,
                    organization_id=user.selected_organization_id,
                )
                saved_workflow_id = new_wf.id
                logger.info(
                    "Copilot auto-created draft workflow id={}", saved_workflow_id
                )
            else:
                # Update existing workflow definition
                existing_wf = await db_client.get_workflow(
                    saved_workflow_id, organization_id=user.selected_organization_id
                )
                if existing_wf:
                    existing_draft = await db_client.get_draft_version(saved_workflow_id)
                    target_def_id = (
                        existing_draft.id
                        if existing_draft
                        else existing_wf.released_definition_id
                    )
                    if target_def_id:
                        await db_client.update_workflow_definition(
                            definition_id=target_def_id,
                            workflow_json=draft.workflow_definition,
                        )
                        logger.info(
                            "Copilot auto-updated draft workflow id={}", saved_workflow_id
                        )
        except Exception as e:
            logger.warning("Could not auto-save copilot draft workflow: {}", e)

    return AgentCopilotResponse(
        reply_message=parsed.get("reply_message")
        or "I've updated your voice agent blueprint.",
        suggested_quick_replies=parsed.get("suggested_quick_replies")
        or ["Looks good!", "Run Test Call", "Edit Questions"],
        is_ready_to_test=bool(parsed.get("is_ready_to_test", False)),
        workflow_draft=draft,
        workflow_id=saved_workflow_id,
    )


async def generate_workflow_from_template_llm(
    call_type: str,
    use_case: str,
    activity_description: str,
    user: UserModel,
) -> dict:
    """Generate workflow definition directly using local LLM instead of services.dograh.com."""
    prompt_text = (
        f"Generate a production-ready, enterprise-grade Voice AI workflow for real-time telephony calling:\n"
        f"Call Direction: {call_type}\n"
        f"Use Case / Industry: {use_case}\n"
        f"Activity Description / Custom Rules:\n{activity_description}\n\n"
        f"MANDATORY TELEPHONY INSTRUCTIONS:\n"
        f"1. Generate a natural, spoken first_message (opening greeting) for voice calling.\n"
        f"2. Generate a comprehensive Master Voice Prompt embodying all 6 voice telephony pillars: Identity & Persona, "
        f"strict TTS rules (no markdown, no asterisks, no bullets, 1-2 spoken sentences per turn, one question rule), "
        f"step-by-step conversational ladder, ASR speech resilience, and objection handling.\n"
        f"3. Generate complete workflow definition with startCall, agentNode (or multiple specialized department nodes if multi-department routing is requested), and endCall connected with custom labeled condition edges."
    )
    chat_req = CopilotChatRequest(
        messages=[ChatMessage(role="user", content=prompt_text)],
        save_draft=False,
    )
    resp = await process_copilot_turn(chat_req, user)
    wf_def = resp.workflow_draft.workflow_definition or {}

    user_text = activity_description.strip()
    # If the user pasted an already-crafted full prompt with explicit section headings and guidelines (>= 500 chars)
    is_preformatted_system_prompt = (
        len(user_text) >= 500
        and any(kw in user_text.lower() for kw in ["# ", "identity:", "role:", "system prompt:", "rules:", "use case:"])
    )

    for node in wf_def.get("nodes", []):
        if node.get("type") == "agentNode":
            if is_preformatted_system_prompt:
                node.setdefault("data", {})["prompt"] = user_text
            elif not node.get("data", {}).get("prompt") or len(node.get("data", {}).get("prompt", "")) < 100:
                if resp.workflow_draft.system_prompt:
                    node.setdefault("data", {})["prompt"] = resp.workflow_draft.system_prompt
            # Clear arbitrary tools unless explicitly mentioned
            node.setdefault("data", {})["tool_uuids"] = []

    return {
        "name": resp.workflow_draft.name or f"{use_case} - {call_type.capitalize()}",
        "workflow_definition": wf_def,
    }


async def process_in_canvas_copilot_turn(
    workflow_id: int,
    request: InCanvasCopilotRequest,
    user: UserModel,
) -> InCanvasCopilotResponse:
    """Process an In-Canvas Copilot turn using MCP tools to directly query or mutate the canvas."""
    provider, api_key, model, base_url = await resolve_llm_credentials(
        user.selected_organization_id
    )

    # 1. Fetch current workflow definition
    current_wf_def: Dict[str, Any] = {}
    if request.current_nodes and request.current_edges is not None:
        current_wf_def = {
            "nodes": request.current_nodes,
            "edges": request.current_edges,
        }
    else:
        # Load from DB
        wf_model = await db_client.get_workflow_by_id(workflow_id)
        if wf_model and wf_model.workflow_definition:
            current_wf_def = wf_model.workflow_definition
        else:
            current_wf_def = {"nodes": [], "edges": []}

    # 2. Prepare Context for the LLM
    nodes = current_wf_def.get("nodes", [])
    edges = current_wf_def.get("edges", [])
    graph_context = (
        f"CURRENT WORKFLOW GRAPH (ID: {workflow_id}):\n"
        f"Nodes ({len(nodes)} total):\n"
    )
    for n in nodes:
        graph_context += f"  - [{n.get('id')}] ({n.get('type')}) '{n.get('data', {}).get('name')}': prompt snippet: {repr(n.get('data', {}).get('prompt', '')[:120])}\n"
    graph_context += f"Edges ({len(edges)} total):\n"
    for e in edges:
        graph_context += f"  - [{e.get('id')}] {e.get('source')} ──[{e.get('data', {}).get('label')}]──► {e.get('target')} (Condition: {e.get('data', {}).get('condition')})\n"

    in_canvas_system = (
        "You are the In-Canvas Voice AI Copilot for the Dograh Calling Studio.\n"
        "You assist the user by directly modifying, editing, improving, explaining, and testing their visual workflow canvas using your tools.\n\n"
        "COMMUNICATION & LANGUAGE RULES:\n"
        "- ALWAYS match the user's language: If the user speaks in Hindi or Hinglish, answer in natural, friendly Hindi/Hinglish. If in English, answer in English.\n"
        "- If the user asks a question (such as 'ye kyon hua?', 'ye connected kyon nahi dikh raha?', 'agenda ke baad end call kyon chala gaya?'), CLEARLY explain the reason first, and then take the right action if needed.\n"
        "- Never just execute tools silently; always provide a clear, helpful explanation of what was changed and why.\n\n"
        "AVAILABLE ACTIONS VIA TOOLS:\n"
        "- `regenerate_full_workflow`: Call this when the user asks to regenerate/rebuild the complete workflow, create an agent from scratch, or gives a comprehensive prompt/use-case specification. NEVER delete nodes one-by-one!\n"
        "- `add_canvas_node`: Create a new node. SUPPORTS auto-connecting (`connect_from_node_id`, `connect_to_node_id`) AND variable extraction configuration (`extraction_enabled`, `extraction_prompt`, `extraction_variables`).\n"
        "- `configure_variable_extraction`: Configure or enable structured variable extraction on any node (e.g. capturing name, email, phone, demo time).\n"
        "- `update_node_field`: modify prompt, greeting, name, or settings on an existing node.\n"
        "- `connect_nodes`: link two nodes with custom edge label and condition (automatically updates existing connection if one already exists between these nodes).\n"
        "- `update_edge`: update an existing edge's label or condition between two nodes.\n"
        "- `remove_canvas_node`: remove a node and attached edges (NEVER remove startCall or endCall).\n"
        "- `remove_edge`: delete an edge by edge_id or by source_node_id and target_node_id.\n"
        "- `trigger_test_call`: start a live WebRTC test call.\n\n"
        "GRAPH TOPOLOGY & EDGE INTEGRITY RULES:\n"
        "1. PREVENT DUPLICATE EDGES: Never create multiple edges between the exact same source and target node (e.g. two edges from '2' to '4'). If a connection already exists, use `connect_nodes` or `update_edge` to update its label and condition, or use `remove_edge` to clean up the obsolete edge first.\n"
        "2. QUALIFICATION VS DISQUALIFICATION ROUTING:\n"
        "   - The agenda node (e.g. '2') should have ONE positive path to the detail collection/demo node (e.g. 'node-3a450d') triggered when user shows interest, mentions any budget (even 1500, etc.), or agrees to proceed.\n"
        "   - It should have ONE negative path to '4' (endCall) triggered ONLY when user explicitly has zero budget, firmly refuses, or is completely uninterested.\n"
        "3. When adding a details collection node (e.g. name, email, phone, demo date):\n"
        "   - Enable variable extraction (`extraction_enabled: true`) with clear variable definitions.\n"
        "   - Connect it from the qualification node (e.g. '2') and connect it to the endCall node ('4') so the workflow is fully continuous.\n"
        "4. OFFICIAL 'API Trigger' (trigger) NODE:\n"
        "   - Dograh has an official canvas node for API Trigger with type 'trigger'.\n"
        "   - It exposes public HTTP POST endpoints (`/api/v1/public/agent/<trigger_path>`) allowing external CRMs/systems to trigger phone calls.\n"
        "   - When the user asks to add an 'API Trigger' node, call `add_canvas_node` with `node_type: 'trigger'`, `name: 'API Trigger'`. DO NOT connect edges to the trigger node (it has min_incoming=0, max_incoming=0 and is a standalone configuration node on the canvas).\n"
        "   - When the public API trigger URL is hit, the phone call automatically dials and enters at Node '1' (startCall).\n\n"
        f"{graph_context}"
    )

    llm_messages = [{"role": "system", "content": in_canvas_system}]
    for msg in request.messages[-8:]:
        llm_messages.append({"role": msg.role, "content": msg.content})

    if not api_key or api_key.startswith("YOUR_ACTUAL"):
        return InCanvasCopilotResponse(
            assistant_message="AI Copilot is offline. Please configure your Groq or OpenAI key in Platform Settings.",
            workflow_definition=current_wf_def,
            modified_node_ids=[],
            suggested_quick_replies=["Add new node", "Run Test Call"],
            trigger_test_call=False,
        )

    modified_node_ids: List[str] = []
    should_test_call = False
    action_summaries: List[str] = []

    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            payload = {
                "model": model,
                "messages": llm_messages,
                "tools": CANVAS_MCP_TOOLS_SCHEMA,
                "tool_choice": "auto",
                "temperature": 0.2,
            }
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]["message"]
            assistant_text = choice.get("content") or ""
            tool_calls = choice.get("tool_calls") or []

            # Execute all returned tool calls
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name")
                fn_args_raw = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
                except Exception:
                    fn_args = {}

                if fn_name == "regenerate_full_workflow":
                    use_case = fn_args.get("use_case", "Voice Agent")
                    prompt_instructions = fn_args.get("prompt_instructions", "")
                    call_type = fn_args.get("call_type", "INBOUND")
                    try:
                        gen_result = await generate_workflow_from_template_llm(
                            call_type=call_type,
                            use_case=use_case,
                            activity_description=prompt_instructions,
                            user=user,
                        )
                        current_wf_def = gen_result.get("workflow_definition", current_wf_def)
                        modified_node_ids = [n.get("id") for n in current_wf_def.get("nodes", [])]
                        summary = f"Regenerated complete {len(modified_node_ids)}-node workflow matching your prompt."
                        action_summaries.append(summary)
                    except Exception as gen_err:
                        logger.error("regenerate_full_workflow error: {}", gen_err)
                        action_summaries.append(f"Failed to regenerate workflow: {str(gen_err)}")
                else:
                    updated_def, summary, mod_ids, test_flag = execute_canvas_mcp_tool(
                        tool_name=fn_name,
                        tool_args=fn_args,
                        workflow_definition=current_wf_def,
                    )
                    current_wf_def = updated_def
                    if summary:
                        action_summaries.append(summary)
                    if mod_ids:
                        modified_node_ids.extend(mod_ids)
                    if test_flag:
                        should_test_call = True

            # If tool calls were executed, run a second follow-up completion
            # so Copilot generates a natural, conversational response explaining the changes in the user's language
            if tool_calls:
                followup_messages = list(llm_messages)
                followup_messages.append({
                    "role": "assistant",
                    "content": assistant_text or None,
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    tc_id = tc.get("id") or f"call_{uuid.uuid4().hex[:6]}"
                    followup_messages.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps({"status": "success", "summary": " ".join(action_summaries)}),
                    })
                try:
                    followup_resp = await client.post(
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": model,
                            "messages": followup_messages,
                            "temperature": 0.4,
                        },
                    )
                    if followup_resp.status_code == 200:
                        followup_choice = followup_resp.json()["choices"][0]["message"]
                        resp_text = followup_choice.get("content")
                        if resp_text and resp_text.strip():
                            assistant_text = resp_text.strip()
                except Exception as follow_err:
                    logger.warning("Copilot follow-up text completion error: {}", follow_err)

            # Save draft to database if any mutations occurred
            if modified_node_ids or action_summaries:
                try:
                    await db_client.save_workflow_draft(
                        workflow_id=workflow_id,
                        workflow_definition=current_wf_def,
                    )
                except Exception as save_err:
                    logger.warning("Could not auto-persist in-canvas draft: {}", save_err)

            if not assistant_text and action_summaries:
                assistant_text = " ".join(action_summaries)

            return InCanvasCopilotResponse(
                assistant_message=assistant_text or "Updated your workflow canvas.",
                workflow_definition=current_wf_def,
                modified_node_ids=list(set(modified_node_ids)),
                suggested_quick_replies=[
                    "Add verification step",
                    "Run Test Call",
                    "Update prompt tone",
                ],
                trigger_test_call=should_test_call,
            )

    except Exception as err:
        logger.error("Error in process_in_canvas_copilot_turn: {}", err)
        return InCanvasCopilotResponse(
            assistant_message=f"I couldn't complete that action: {str(err)}",
            workflow_definition=current_wf_def,
            modified_node_ids=[],
            suggested_quick_replies=["Try again", "Run Test Call"],
            trigger_test_call=False,
        )

