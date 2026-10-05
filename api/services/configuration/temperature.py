"""Temperature limits shared by configuration validation, the UI, and runtime.

Provider documentation checked 2026-10-04. Gateway limits are not necessarily
the same as the model vendor's limits. Keep source links beside each policy.
"""

import math
import re

from pydantic import Field

TEMPERATURE_DESCRIPTION = (
    "Sampling temperature. Lower values give more predictable responses. "
    "Leave blank to use the provider default."
)

# The factory currently enables reasoning for the GPT-5 family. Temperature
# is unsupported in that mode, as well as on o-series models.
# https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.4
OPENAI_REASONING_RULE = {
    "pattern": r"(^|/)(gpt-5|o[134](?:-|$))",
    "supported": False,
}
# Azure uses deployment names. Recognizable original GPT-5/o-series names can
# be handled automatically; for opaque names users can leave temperature blank.
# https://learn.microsoft.com/azure/foundry/openai/how-to/reasoning
AZURE_REASONING_RULE = {
    "pattern": r"^(gpt-5(?:-(?:mini|nano|pro))?(?:-\d{4}-\d{2}-\d{2})?$|o[134](?:-|$))",
    "supported": False,
}
# Claude's documented range is 0..1; post-4.6 models no longer support sampling.
# https://platform.claude.com/docs/en/api/typescript/messages/create
CLAUDE_RULES = [
    {
        "pattern": r"claude-(?:(?:opus|sonnet|haiku)-(?:4[.-](?:[7-9]|[1-9][0-9])|[5-9](?:[.-]|$))|mythos)",
        "supported": False,
    },
    {
        "pattern": r"claude-",
        "maximum": 1.0,
        "docs_url": "https://platform.claude.com/docs/en/api/typescript/messages/create",
    },
]
# https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.5
GEMINI_RULE = {
    "pattern": r"gemini-3[.-]",
    "description": (
        TEMPERATURE_DESCRIPTION
        + " Google recommends leaving sampling parameters unset for Gemini 3.x."
    ),
}
# https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference
GEMINI_RULES = [
    {
        "pattern": r"gemini-3[.-](?:[6-9]|[1-9][0-9])(?:-|$)",
        "supported": False,
    },
    GEMINI_RULE,
]

TEMPERATURE_POLICIES = {
    "openai": {
        "maximum": 2.0,
        "docs_url": "https://developers.openai.com/api/reference/resources/chat",
        "model_constraints": [OPENAI_REASONING_RULE],
    },
    "atlascloud": {
        "maximum": 2.0,
        "docs_url": "https://www.atlascloud.ai/models/deepseek-ai/deepseek-v4-pro",
        "model_constraints": [OPENAI_REASONING_RULE, *CLAUDE_RULES],
    },
    "hopper": {
        # Public docs omit bounds. Use the OpenAI-compatible 0..2 fallback;
        # this is an application policy, not a verified Hopper-specific limit.
        "maximum": 2.0,
        "docs_url": "https://docs.withhopper.com/llm",
        "description": TEMPERATURE_DESCRIPTION + " Limits depend on the hosted model.",
    },
    "google": {
        "maximum": 2.0,
        "docs_url": "https://ai.google.dev/api/generate-content#v1beta.GenerationConfig",
        "model_constraints": GEMINI_RULES,
    },
    "google_vertex": {
        "maximum": 2.0,
        "docs_url": "https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference",
        "model_constraints": GEMINI_RULES,
    },
    "groq": {
        "maximum": 2.0,
        "docs_url": "https://console.groq.com/docs/api-reference",
    },
    "openrouter": {
        "maximum": 2.0,
        "docs_url": "https://openrouter.ai/docs/api/reference/parameters",
        # OpenRouter forwards explicit sampling settings to the selected model.
        "model_constraints": [
            AZURE_REASONING_RULE
            | {
                "pattern": r"^openai/(gpt-5(?:-(?:mini|nano|pro))?(?:-\d{4}-\d{2}-\d{2})?$|o[134](?:-|$))"
            },
            *CLAUDE_RULES,
            *GEMINI_RULES,
        ],
    },
    "azure": {
        "maximum": 2.0,
        "docs_url": "https://learn.microsoft.com/azure/foundry/openai/how-to/reasoning",
        "model_constraints": [AZURE_REASONING_RULE],
    },
    "dograh": {
        "description": TEMPERATURE_DESCRIPTION + " Limits depend on the hosted model.",
    },
    "aws_bedrock": {
        "maximum": 1.0,
        "docs_url": "https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InferenceConfiguration.html",
        "model_constraints": CLAUDE_RULES,
    },
    "speaches": {
        # Ollama/vLLM/custom OpenAI endpoints decide their own upper bound.
        "docs_url": "https://docs.ollama.com/openai",
        "description": TEMPERATURE_DESCRIPTION
        + " Limits depend on your server and model.",
    },
    "huggingface": {
        "maximum": 2.0,
        "docs_url": "https://huggingface.co/docs/inference-providers/tasks/chat-completion",
    },
    "minimax": {
        "maximum": 2.0,
        "docs_url": "https://platform.minimax.io/docs/api-reference/text-chat-openai",
    },
    "sarvam": {
        "maximum": 2.0,
        "docs_url": "https://docs.sarvam.ai/api-reference/chat/chat-completions-v1",
    },
}


def temperature_field(provider: str, default: float | None):
    policy = TEMPERATURE_POLICIES[provider]
    return Field(
        default=default,
        ge=0.0,
        le=policy.get("maximum"),
        allow_inf_nan=False,
        description=policy.get("description", TEMPERATURE_DESCRIPTION),
        json_schema_extra={
            key: value
            for key, value in {
                **policy,
                "docs_label": "Temperature documentation",
            }.items()
            if key not in ("maximum", "description")
        },
    )


def resolve_temperature(provider: str, model: str, value: float | None) -> float | None:
    """Apply model restrictions, including after model_copy-based overrides."""
    policy = TEMPERATURE_POLICIES[provider]
    for rule in policy.get("model_constraints", []):
        if re.search(rule["pattern"], model):
            policy = {**policy, **rule}
            break
    if policy.get("supported") is False or value is None:
        return None
    maximum = policy.get("maximum")
    if (
        not math.isfinite(value)
        or value < 0
        or (maximum is not None and value > maximum)
    ):
        bounds = f"between 0 and {maximum}" if maximum is not None else "0 or greater"
        raise ValueError(
            f"Temperature for {provider}/{model} must be finite and {bounds}"
        )
    return value
