"""LLM configuration resolution and token usage accumulation."""

import random

from api.db import db_client
from api.db.models import WorkflowRunModel


async def resolve_llm_config(
    qa_node_data: dict, workflow_run: WorkflowRunModel
) -> tuple[str, str, str, dict]:
    """Resolve the LLM provider, model, API key, and extra kwargs for QA analysis.

    If the QA node has its own LLM configuration (qa_use_workflow_llm=False),
    use those settings directly. Otherwise, fall back to the user's configured LLM.

    Returns:
        (provider, model, api_key, service_kwargs) tuple — service_kwargs can be
        passed directly to create_llm_service_from_provider as keyword arguments.
    """
    if not qa_node_data.get("qa_use_workflow_llm", True):
        provider = qa_node_data.get("qa_provider", "openai")
        kwargs = {}
        if provider == "azure":
            kwargs["endpoint"] = qa_node_data.get("qa_endpoint", "")
        return (
            provider,
            qa_node_data.get("qa_model"),
            qa_node_data.get("qa_api_key"),
            kwargs,
        )

    # Fall back to user's configured LLM
    provider, model, api_key, kwargs = await resolve_user_llm_config(workflow_run)

    qa_model = qa_node_data.get("qa_model", "default")
    if qa_model and qa_model != "default":
        model = qa_model

    return provider, model, api_key, kwargs


async def resolve_user_llm_config(
    workflow_run: WorkflowRunModel,
) -> tuple[str, str, str, dict]:
    """Resolve the user's configured LLM (from UserConfiguration).

    Returns:
        (provider, model, api_key, service_kwargs) tuple
    """
    user_id = None
    if workflow_run.workflow and workflow_run.workflow.user:
        user_id = workflow_run.workflow.user.id

    llm_config: dict = {}
    if user_id:
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
