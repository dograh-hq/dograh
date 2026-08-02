"""LLM configuration resolution and token usage accumulation."""

import random

from api.db.models import WorkflowRunModel
from api.services.workflow.dto import QANodeData

QA_USAGE_CONTEXT = "qa_analysis"


async def resolve_llm_config(
    qa_data: QANodeData, workflow_run: WorkflowRunModel
) -> tuple[str, str, str, dict]:
    """Resolve the LLM provider, model, API key, and extra kwargs for QA analysis.

    If the QA node has its own LLM configuration (qa_use_workflow_llm=False),
    use those settings directly. Otherwise, fall back to the workflow/org LLM.

    Returns:
        (provider, model, api_key, service_kwargs) tuple — service_kwargs can be
        passed directly to create_llm_service_from_provider as keyword arguments.
    """
    if not qa_data.qa_use_workflow_llm:
        provider = qa_data.qa_provider or "openai"
        kwargs = {}
        if provider == "azure":
            kwargs["endpoint"] = qa_data.qa_endpoint or ""
        # NOTE: the QA node's own-LLM path has no OpenAI-compatible base-URL
        # field today (qa_endpoint is Azure-only in the UI), so a custom base
        # URL can't be forwarded here yet. Supporting it needs a dedicated
        # qa_base_url field + UI; tracked as a follow-up. This PR fixes the
        # reported case, which is the shared workflow-LLM path below.
        return (
            provider,
            qa_data.qa_model,
            qa_data.qa_api_key,
            kwargs,
        )

    # Fall back to the workflow/org configured LLM
    provider, model, api_key, kwargs = await resolve_user_llm_config(workflow_run)

    if qa_data.qa_model and qa_data.qa_model != "default":
        model = qa_data.qa_model

    return provider, model, api_key, kwargs


async def resolve_user_llm_config(
    workflow_run: WorkflowRunModel,
) -> tuple[str, str, str, dict]:
    """Resolve the workflow/org configured LLM.

    Returns:
        (provider, model, api_key, service_kwargs) tuple
    """
    llm_config: dict = {}
    if workflow_run.workflow:
        from api.services.configuration.ai_model_configuration import (
            get_effective_ai_model_configuration_for_workflow,
        )

        workflow_configurations = {}
        if workflow_run.definition:
            workflow_configurations = (
                workflow_run.definition.workflow_configurations or {}
            )
        elif workflow_run.workflow:
            workflow_configurations = (
                workflow_run.workflow.workflow_configurations or {}
            )

        user_configuration = await get_effective_ai_model_configuration_for_workflow(
            organization_id=workflow_run.workflow.organization_id
            if workflow_run.workflow
            else None,
            workflow_configurations=workflow_configurations,
        )
        llm_config = user_configuration.model_dump(exclude_none=True).get("llm", {})

    provider = llm_config.get("provider", "openai")
    api_key = llm_config.get("api_key", "")
    if isinstance(api_key, list):
        api_key = random.choice(api_key)
    model = llm_config.get("model", "gpt-4.1")

    kwargs = {}
    if provider == "azure":
        kwargs["endpoint"] = llm_config.get("endpoint", "")
    elif llm_config.get("base_url"):
        # Forward a configured base_url for any non-azure provider so QA reaches
        # the same OpenAI-compatible endpoint the conversation path uses.
        # Previously only openrouter forwarded it, so an openai-provider config
        # pointing at xAI/grok, a local LLM, or a proxy silently fell back to
        # OpenAI's default endpoint and 401'd (#527).
        kwargs["base_url"] = llm_config["base_url"]

    return provider, model, api_key, kwargs


def accumulate_token_usage(total: dict, response) -> None:
    """Add token counts from an LLM response to the running total dict."""
    if not response.usage:
        return
    total["prompt_tokens"] = total.get("prompt_tokens", 0) + (
        response.usage.prompt_tokens or 0
    )
    total["completion_tokens"] = total.get("completion_tokens", 0) + (
        response.usage.completion_tokens or 0
    )
    total["total_tokens"] = total.get("total_tokens", 0) + (
        response.usage.total_tokens or 0
    )
    total["cache_read_input_tokens"] = total.get("cache_read_input_tokens", 0) + (
        getattr(response.usage, "cache_read_input_tokens", 0) or 0
    )
    cache_creation = getattr(response.usage, "cache_creation_input_tokens", None)
    if cache_creation is not None:
        total["cache_creation_input_tokens"] = (
            total.get("cache_creation_input_tokens") or 0
        ) + cache_creation
