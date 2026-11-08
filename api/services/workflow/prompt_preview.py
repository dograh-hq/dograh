"""Compose editor previews and publication prewarms using the runtime composer."""

import asyncio
import json
import math
import re

from loguru import logger
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema

from api.db import db_client
from api.schemas.prompt_preview import NodePromptPreview, PromptPreviewResponse
from api.schemas.workflow_configurations import PromptVariablesConfiguration
from api.services.configuration.model_connections import resolve_model_configuration
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.pipecat_engine_context_composer import (
    compose_functions_for_node,
    compose_system_prompt_for_node,
)
from api.services.workflow.pipecat_engine_custom_tools import load_tool_schemas
from api.services.workflow.prompt_variables import (
    DEFAULT_CALL_CONTEXT_NOTE,
    ITALIAN_CALL_CONTEXT_NOTE,
    CallContextMessage,
    call_variable_context,
    prompt_templates,
    resolve_call_variables,
)
from api.services.workflow.workflow_graph import WorkflowGraph
from api.utils.template_renderer import (
    TEMPLATE_VAR_PATTERN,
    is_builtin_variable,
    render_template,
)


async def preview_node_functions(node, organization_id):
    schemas = await load_tool_schemas(
        organization_id, node.tool_uuids or [], mcp_tool_filters=node.mcp_tool_filters
    )
    return await compose_functions_for_node(node=node, custom_tool_schemas=schemas)


def token_estimate(text: str) -> int:
    # Deliberately labelled an estimate. Provider tokenization and overhead vary;
    # only a successful cache creation decides whether a node is eligible.
    return math.ceil(len(text.encode("utf-8")) / 4)


async def build_prompt_preview(
    *,
    definition,
    organization_id,
    config,
    sample_values=None,
    gathered_values=None,
    workflow_definition=None,
):
    graph = WorkflowGraph(
        ReactFlowDTO.model_validate(workflow_definition or definition.workflow_json)
    )
    initial = (
        sample_values
        if sample_values is not None
        else definition.template_context_variables or {}
    )
    context = call_variable_context(initial, gathered_values or {})
    values = resolve_call_variables(
        prompt_templates(graph), context, include_weekday=config.include_weekday
    )
    call_context = (
        CallContextMessage(values)["content"] if config.mode == "call_context" else None
    )
    has_recordings = await db_client.has_active_recordings(organization_id)
    notices = [
        "Token counts are approximate UTF-8 size estimates; the provider decides cache eligibility."
    ]
    provider, model = None, None
    try:
        resolved = await resolve_model_configuration(
            organization_id,
            workflow_override=(definition.workflow_configurations or {}).get(
                "model_configuration_override"
            ),
        )
        if resolved.effective.llm and not resolved.effective.is_realtime:
            provider, model = (
                resolved.effective.llm.provider,
                resolved.effective.llm.model,
            )
    except Exception:
        notices.append("Configure a model connection to check cache availability.")
    available = provider in {"google", "google_vertex"}
    minimum = None
    if available:
        minimum = (
            4096
            if model
            and (model.startswith("gemini-3") or model.startswith("gemini-2.5-pro"))
            else (2048 if provider == "google_vertex" else 1024)
        )
        notices.append(
            "Explicit caches store your static instructions and tool schemas with your Google connection for up to one hour after their last refresh. Storage is billed by Google."
        )
        if model and model.startswith("gemini-3-flash"):
            notices.append(
                "Gemini 3 Flash's documented implicit-cache threshold is 6,144 tokens; explicit-cache eligibility is shown separately."
            )
    elif provider == "openai":
        minimum = 1024
    cache_service = None
    if available and config.mode == "call_context":
        from api.services.pipecat.service_factory import create_llm_service

        cache_service = create_llm_service(
            resolved.effective.model_copy(update={"llm_fallback": None})
        )
    extraction_sources = {
        v.name for node in graph.nodes.values() for v in node.extraction_variables or []
    }
    nodes = []
    try:
        for node in graph.nodes.values():
            if node.id == graph.global_node_id or node.prompt is None:
                continue
            prompt = compose_system_prompt_for_node(
                node=node,
                workflow=graph,
                format_prompt=lambda text: render_template(text, initial),
                has_recordings=has_recordings,
                prompt_variables=config,
            )
            functions = await preview_node_functions(node, organization_id)
            tools = [function.to_default_dict() for function in functions]
            template = node.prompt or ""
            if node.add_global_prompt and graph.global_node_id:
                template = (
                    (graph.nodes[graph.global_node_id].prompt or "") + "\n" + template
                )
            refs = list(re.finditer(TEMPLATE_VAR_PATTERN, template))
            unresolved = sorted(
                {
                    match.group(1).strip()
                    for match in refs
                    if not match.group(2)
                    and not is_builtin_variable(match.group(1).strip())
                    and match.group(1).strip().split(".")[-1] not in extraction_sources
                    and not render_template(match.group(0), context)
                }
            )
            nodes.append(
                NodePromptPreview(
                    node_id=node.id,
                    node_name=node.name,
                    system_prompt=prompt.text,
                    tools=tools,
                    variables=list(dict.fromkeys(match.group(0) for match in refs)),
                    unresolved_variables=unresolved,
                    static_tokens_estimate=token_estimate(
                        prompt.text + json.dumps(tools)
                    )
                    if config.mode == "call_context"
                    else 0,
                    per_call_tokens_estimate=token_estimate(
                        call_context or prompt.text + json.dumps(tools)
                    ),
                )
            )
            if cache_service:
                from api.services.pipecat.prompt_cache import get_prompt_cache_registry

                cache_service.configure_prompt_cache(
                    organization_id=organization_id,
                    workflow_id=definition.workflow_id,
                    node_id=node.id,
                    enabled=True,
                )
                spec = cache_service.prompt_cache_spec_for_node(
                    node_id=node.id,
                    system_instruction=prompt.text,
                    tools=ToolsSchema(standard_tools=functions),
                )
                if spec:
                    from google.genai.types import CreateCachedContentConfig

                    nodes[-1].tools = (
                        CreateCachedContentConfig(tools=spec.tools)
                        .model_dump(mode="json", exclude_none=True)
                        .get("tools", [])
                    )
                    nodes[-1].static_tokens_estimate = token_estimate(
                        prompt.text + json.dumps(nodes[-1].tools)
                    )
                    status, tokens = await get_prompt_cache_registry().status(spec)
                    nodes[-1].cache_status = status
                    if tokens:
                        nodes[-1].static_tokens_estimate = tokens
    finally:
        if cache_service:
            await cache_service.cleanup()
    if any(node.tool_uuids for node in graph.nodes.values()):
        notices.append(
            "If you use MCP tools, their live declarations are resolved at call startup and may add tokens to this preview."
        )
    return PromptPreviewResponse(
        nodes=nodes,
        call_context=call_context,
        provider=provider,
        model=model,
        explicit_cache_available=available,
        minimum_tokens=minimum,
        notices=notices,
        default_note=DEFAULT_CALL_CONTEXT_NOTE,
        italian_note=ITALIAN_CALL_CONTEXT_NOTE,
    )


async def prewarm_published_prompts(*, organization_id, workflow_id, definition):
    configs = definition.workflow_configurations or {}
    config = PromptVariablesConfiguration.model_validate(
        configs.get("prompt_variables") or {}
    )
    if config.mode != "call_context":
        return
    from api.services.pipecat.prompt_cache import (
        cache_disabled,
        get_prompt_cache_registry,
    )
    from api.services.pipecat.service_factory import create_llm_service

    if cache_disabled():
        return
    llm = None
    try:
        resolved = await resolve_model_configuration(
            organization_id,
            workflow_override=configs.get("model_configuration_override"),
        )
        if resolved.effective.is_realtime:
            return
        llm = create_llm_service(resolved.effective)
        if not hasattr(llm, "configure_prompt_cache"):
            return
        graph = WorkflowGraph(ReactFlowDTO.model_validate(definition.workflow_json))
        has_recordings = await db_client.has_active_recordings(organization_id)
        for node in graph.nodes.values():
            if node.id == graph.global_node_id or node.prompt is None:
                continue
            prompt = compose_system_prompt_for_node(
                node=node,
                workflow=graph,
                format_prompt=lambda text: text,
                has_recordings=has_recordings,
                prompt_variables=config,
            )
            functions = await preview_node_functions(node, organization_id)
            llm.configure_prompt_cache(
                organization_id=organization_id,
                workflow_id=workflow_id,
                node_id=node.id,
                enabled=True,
            )
            llm.prewarm_prompt_cache(
                node_id=node.id,
                system_instruction=prompt.text,
                tools=ToolsSchema(standard_tools=functions),
            )
        await asyncio.gather(
            *list(get_prompt_cache_registry().tasks.values()), return_exceptions=True
        )
    except Exception:
        logger.bind(workflow_id=workflow_id, organization_id=organization_id).warning(
            "Published prompt prewarm unavailable; calls will use inline requests"
        )
    finally:
        if llm:
            # Automatic policy can also encounter non-Google primaries or
            # fallback services, whose SDK clients need explicit cleanup.
            from api.services.workflow.final_extraction import _close_llm

            await _close_llm(llm)
