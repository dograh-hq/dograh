"""Agent builder service — turns a description or a template into a workflow.

Creates a minimal, valid workflow graph (start → agent → end) of the same
shape the workflow editor produces, owned by the caller's organization.

Two modes:
- "template": fills one of the built-in TEMPLATES with the business details.
- "describe": generates the agent prompt and greeting from the client's
  free-form description using the user's configured LLM (same resolution as
  the QA pass). If no LLM is usable, falls back to a deterministic,
  well-engineered prompt composed from the description — agent creation
  never fails because of the LLM.
"""

from __future__ import annotations

import random
from typing import Optional

from loguru import logger
from pydantic import BaseModel

from api.db import db_client
from api.db.models import WorkflowModel
from api.services.agent_builder.templates import (
    DEFAULT_LANGUAGE,
    END_PROMPT_TEMPLATE,
    START_PROMPT_TEMPLATE,
    TEMPLATES,
    VOICE_STYLE_RULES,
    fill_template_string,
)
from api.services.gen_ai.json_parser import parse_llm_json
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.workflow_graph import WorkflowGraph


class AgentBuilderError(ValueError):
    """Raised when the agent builder receives an invalid request."""


class BusinessInfo(BaseModel):
    name: str
    industry: Optional[str] = None
    details: Optional[str] = None
    language: Optional[str] = None


# ---------------------------------------------------------------------------
# Workflow graph construction
# ---------------------------------------------------------------------------


def build_workflow_definition(
    *,
    greeting: str,
    start_prompt: str,
    agent_prompt: str,
    end_prompt: str,
    agent_node_name: str = "Conversation",
) -> dict:
    """Build a minimal valid workflow graph: start → agent → end.

    Same nodes/edges JSON shape the workflow editor saves, so the created
    workflow opens and runs exactly like a hand-built one.
    """
    return {
        "nodes": [
            {
                "id": "start-1",
                "type": "startCall",
                "position": {"x": 0, "y": 0},
                "data": {
                    "name": "Start Call",
                    "prompt": start_prompt,
                    "greeting": greeting,
                    "greeting_type": "text",
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "is_start": True,
                    "is_end": False,
                },
            },
            {
                "id": "agent-1",
                "type": "agentNode",
                "position": {"x": 0, "y": 250},
                "data": {
                    "name": agent_node_name,
                    "prompt": agent_prompt,
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "is_start": False,
                    "is_end": False,
                },
            },
            {
                "id": "end-1",
                "type": "endCall",
                "position": {"x": 0, "y": 500},
                "data": {
                    "name": "End Call",
                    "prompt": end_prompt,
                    "add_global_prompt": False,
                    "is_start": False,
                    "is_end": True,
                },
            },
        ],
        "edges": [
            {
                "id": "edge-start-agent",
                "source": "start-1",
                "target": "agent-1",
                "data": {
                    "label": "Continue Conversation",
                    "condition": (
                        "The caller has responded to the greeting and the "
                        "conversation should continue."
                    ),
                },
            },
            {
                "id": "edge-agent-end",
                "source": "agent-1",
                "target": "end-1",
                "data": {
                    "label": "End Conversation",
                    "condition": (
                        "The goal of the conversation has been achieved, or the "
                        "caller is not interested or wants to end the call."
                    ),
                },
            },
        ],
    }


def _assert_definition_valid(definition: dict) -> None:
    """Run the same DTO + graph validation the editor's publish gate uses."""
    dto = ReactFlowDTO.model_validate(definition)
    WorkflowGraph(dto)


# ---------------------------------------------------------------------------
# Describe mode — LLM generation with deterministic fallback
# ---------------------------------------------------------------------------

_BUILDER_SYSTEM_PROMPT = (
    "You are an expert voice-agent prompt engineer for an Indian calling "
    "platform. Given a client's description of the voice agent they want and "
    "their business details, produce the agent's system prompt and an opening "
    "greeting.\n\n"
    "Requirements for the system prompt you write:\n"
    "- Second person ('You are ...'), describing persona, goal and a concrete "
    "conversation plan.\n"
    "- Voice-call friendly: instruct short sentences, one question at a time, "
    "no special characters or lists in speech.\n"
    "- The agent must speak in the requested language (default: a natural "
    "Hinglish mix of Hindi and English) and mirror the caller's language.\n"
    "- Ground the agent strictly in the provided business details; it must "
    "not invent facts.\n\n"
    "Respond with ONLY a JSON object, no markdown, in this exact shape:\n"
    '{"name": "<short agent name>", "system_prompt": "<the system prompt>", '
    '"greeting": "<one-sentence opening greeting in the requested language>"}'
)


async def _resolve_llm_config(user_id: int) -> tuple[str, str, str, dict]:
    """Resolve the user's configured LLM (provider, model, api_key, kwargs).

    Mirrors api.services.workflow.qa.llm_config.resolve_user_llm_config but
    works from a user id instead of a workflow run.
    """
    user_configuration = await db_client.get_user_configurations(user_id)
    llm_config = user_configuration.model_dump(exclude_none=True).get("llm", {})

    provider = llm_config.get("provider", "openai")
    api_key = llm_config.get("api_key", "")
    if isinstance(api_key, list):
        api_key = random.choice(api_key)
    model = llm_config.get("model", "gpt-4.1")

    kwargs = {}
    if provider == "azure":
        kwargs["endpoint"] = llm_config.get("endpoint", "")
    elif provider == "openrouter" and llm_config.get("base_url"):
        kwargs["base_url"] = llm_config["base_url"]

    return provider, model, api_key, kwargs


async def _generate_via_llm(
    user_id: int, description: str, business: BusinessInfo
) -> dict:
    """Generate {name, system_prompt, greeting} with the user's configured LLM.

    Raises on any failure — the caller falls back to deterministic composition.
    """
    # Lazy imports: pipecat services are heavy and only needed on this path.
    from pipecat.processors.aggregators.llm_context import LLMContext

    from api.services.pipecat.service_factory import (
        create_llm_service_from_provider,
    )

    provider, model, api_key, kwargs = await _resolve_llm_config(user_id)
    if not api_key:
        raise AgentBuilderError("No LLM API key configured for this user")

    llm = create_llm_service_from_provider(provider, model, api_key, **kwargs)

    language = business.language or DEFAULT_LANGUAGE
    user_message = (
        f"Business name: {business.name}\n"
        f"Industry: {business.industry or 'not specified'}\n"
        f"Business details: {business.details or 'not specified'}\n"
        f"Language the agent should speak: {language}\n\n"
        f"What the client wants the agent to do:\n{description}"
    )

    context = LLMContext()
    context.set_messages([{"role": "user", "content": user_message}])
    raw = await llm.run_inference(context, system_instruction=_BUILDER_SYSTEM_PROMPT)

    data = parse_llm_json(raw or "")
    if not data.get("system_prompt") or not data.get("greeting"):
        raise AgentBuilderError("LLM response missing system_prompt or greeting")
    return data


def _compose_prompts_deterministically(
    description: str, business: BusinessInfo
) -> dict:
    """Compose {name, system_prompt, greeting} from the description directly.

    Used when no LLM is configured or the LLM call fails, so the builder
    always produces a working agent.
    """
    language = business.language or DEFAULT_LANGUAGE
    industry = business.industry or "their industry"
    details = business.details or "No additional details were provided."

    system_prompt = (
        f"You are a friendly voice agent calling on behalf of {business.name}, "
        f"a {industry} business.\n\n"
        f"Your goal, as described by the business:\n{description}\n\n"
        f"About the business:\n{details}\n\n"
        "Conversation plan:\n"
        "Work towards the goal above step by step. Ask one question at a time, "
        "listen to the answer, and respond naturally before moving on. Use only "
        "the business details above — if you do not know something, say a team "
        "member will follow up with the answer. When the goal is achieved, or "
        "the caller is clearly not interested, summarise any next step and "
        "close the call politely.\n\n"
        + VOICE_STYLE_RULES.format(language=language)
    )
    greeting = (
        f"Namaste! Main {business.name} ki taraf se baat kar rahi hoon. "
        "Kya aapse ek minute baat ho sakti hai?"
    )
    return {
        "name": f"{business.name} Voice Agent",
        "system_prompt": system_prompt,
        "greeting": greeting,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def create_agent_workflow(
    *,
    mode: str,
    description: Optional[str],
    template_id: Optional[str],
    business: BusinessInfo,
    user_id: int,
    organization_id: Optional[int],
) -> WorkflowModel:
    """Create a real workflow for the caller's organization and return it."""
    language = business.language or DEFAULT_LANGUAGE

    if mode == "template":
        template = TEMPLATES.get(template_id or "")
        if template is None:
            raise AgentBuilderError(f"Unknown template_id: {template_id!r}")

        fill_kwargs = dict(
            business_name=business.name,
            industry=business.industry or template["default_industry"],
            details=business.details or "No additional details were provided.",
            language=language,
        )
        greeting = fill_template_string(template["greeting"], **fill_kwargs)
        agent_prompt = fill_template_string(template["agent_prompt"], **fill_kwargs)
        workflow_name = f"{template['name']} - {business.name}"
        agent_node_name = template["name"]
    elif mode == "describe":
        if not description or not description.strip():
            raise AgentBuilderError("description is required in describe mode")

        generated: Optional[dict] = None
        try:
            generated = await _generate_via_llm(user_id, description, business)
        except Exception as e:
            logger.warning(
                f"Agent builder LLM generation failed, falling back to "
                f"deterministic composition: {e}"
            )
        if not generated:
            generated = _compose_prompts_deterministically(description, business)

        greeting = generated["greeting"]
        agent_prompt = generated["system_prompt"]
        workflow_name = generated.get("name") or f"{business.name} Voice Agent"
        agent_node_name = "Conversation"
    else:
        raise AgentBuilderError(f"Unknown mode: {mode!r}")

    fill_kwargs = dict(
        business_name=business.name,
        industry=business.industry or "",
        details=business.details or "",
        language=language,
    )
    start_prompt = fill_template_string(START_PROMPT_TEMPLATE, **fill_kwargs)
    end_prompt = fill_template_string(END_PROMPT_TEMPLATE, **fill_kwargs)

    definition = build_workflow_definition(
        greeting=greeting,
        start_prompt=start_prompt,
        agent_prompt=agent_prompt,
        end_prompt=end_prompt,
        agent_node_name=agent_node_name,
    )
    # Guarantee the created workflow passes the editor's publish-gate checks.
    _assert_definition_valid(definition)

    workflow = await db_client.create_workflow(
        workflow_name,
        definition,
        user_id,
        organization_id,
    )
    return workflow


def list_templates() -> list[dict]:
    """Public template metadata: id, name, description, fields."""
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "description": t["description"],
            "fields": t["fields"],
        }
        for t in TEMPLATES.values()
    ]
