"""LLM configuration resolution and token usage accumulation."""

from api.constants import MPS_API_URL
from api.db import db_client
from api.db.models import WorkflowRunModel


def _provider_base_url(provider: str | None, endpoint: str = "") -> str | None:
    """Return the base URL for a given LLM provider."""
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    if provider == "groq":
        return "https://api.groq.com/openai/v1"
    if provider == "google":
        return "https://generativelanguage.googleapis.com/v1beta/openai/"
    if provider == "azure":
        return endpoint or None
    if provider == "dograh":
        return f"{MPS_API_URL}/api/v1/llm"
    return None


async def resolve_llm_config(
    qa_node_data: dict, workflow_run: WorkflowRunModel
) -> tuple[str, str, str | None]:
    """Resolve the LLM model, API key, and base URL for QA analysis.

    If the QA node has its own LLM configuration (qa_use_workflow_llm=False),
    use those settings directly. Otherwise, fall back to the user's configured LLM.

    Returns:
        (model, api_key, base_url) tuple
    """
    if not qa_node_data.get("qa_use_workflow_llm", True):
        return (
            qa_node_data.get("qa_model"),
            qa_node_data.get("qa_api_key"),
            _provider_base_url(
                qa_node_data.get("qa_provider"),
                qa_node_data.get("qa_endpoint", ""),
            ),
        )

    # Fall back to user's configured LLM
    model, api_key, base_url = await resolve_user_llm_config(workflow_run)

    qa_model = qa_node_data.get("qa_model", "default")
    if qa_model and qa_model != "default":
        model = qa_model

    return model, api_key, base_url


async def resolve_user_llm_config(
    workflow_run: WorkflowRunModel,
) -> tuple[str, str, str | None]:
    """Resolve the user's configured LLM (from UserConfiguration).

    Returns:
        (model, api_key, base_url) tuple
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
    model = llm_config.get("model", "gpt-4.1")
    base_url = _provider_base_url(provider, llm_config.get("endpoint", ""))
    if provider == "openrouter" and llm_config.get("base_url"):
        base_url = llm_config["base_url"]

    return model, api_key, base_url


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
