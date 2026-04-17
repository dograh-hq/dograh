"""Spec for the QA Analysis node — runs an LLM quality review on the call
transcript after completion."""

from api.services.workflow.node_specs._base import (
    DisplayOptions,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertyOption,
    PropertySpec,
    PropertyType,
)

SPEC = NodeSpec(
    name="qa",
    display_name="QA Analysis",
    description=(
        "Runs an LLM quality review on the call transcript after completion. "
        "Per-node analysis splits the conversation by node and evaluates each "
        "segment against the configured system prompt."
    ),
    category=NodeCategory.integration,
    icon="ClipboardCheck",
    properties=[
        PropertySpec(
            name="name",
            type=PropertyType.string,
            display_name="Name",
            description="Short identifier for this QA configuration.",
            required=True,
            min_length=1,
        ),
        PropertySpec(
            name="qa_enabled",
            type=PropertyType.boolean,
            display_name="Enabled",
            description="When false, the QA run is skipped.",
            default=True,
        ),
        PropertySpec(
            name="qa_system_prompt",
            type=PropertyType.string,
            display_name="System Prompt",
            description=(
                "Instructions to the QA reviewer LLM. Supports placeholders: "
                "`{node_summary}`, `{previous_conversation_summary}`, "
                "`{transcript}`, `{metrics}`."
            ),
            editor="textarea",
        ),
        PropertySpec(
            name="qa_min_call_duration",
            type=PropertyType.number,
            display_name="Minimum Call Duration (seconds)",
            description="Calls shorter than this are skipped.",
            default=15,
            min_value=0,
        ),
        PropertySpec(
            name="qa_voicemail_calls",
            type=PropertyType.boolean,
            display_name="Include Voicemail Calls",
            description="When false, calls flagged as voicemail are skipped.",
            default=False,
        ),
        PropertySpec(
            name="qa_sample_rate",
            type=PropertyType.number,
            display_name="Sample Rate (%)",
            description=(
                "Percent of eligible calls QA'd. 100 means every call; lower "
                "values use random sampling."
            ),
            default=100,
            min_value=1,
            max_value=100,
        ),
        # ---- LLM configuration ----
        PropertySpec(
            name="qa_use_workflow_llm",
            type=PropertyType.boolean,
            display_name="Use Workflow's LLM",
            description=(
                "When true, the QA pass uses the same LLM the workflow runs "
                "with. Set false to specify a separate provider/model."
            ),
            default=True,
        ),
        PropertySpec(
            name="qa_provider",
            type=PropertyType.options,
            display_name="QA LLM Provider",
            description="LLM provider used for the QA pass.",
            display_options=DisplayOptions(show={"qa_use_workflow_llm": [False]}),
            options=[
                PropertyOption(value="openai", label="OpenAI"),
                PropertyOption(value="azure", label="Azure OpenAI"),
                PropertyOption(value="openrouter", label="OpenRouter"),
                PropertyOption(value="anthropic", label="Anthropic"),
            ],
        ),
        PropertySpec(
            name="qa_model",
            type=PropertyType.string,
            display_name="QA Model",
            description=(
                "Model identifier (e.g., 'gpt-4o', 'claude-sonnet-4-6'). "
                "Provider-specific."
            ),
            display_options=DisplayOptions(show={"qa_use_workflow_llm": [False]}),
        ),
        PropertySpec(
            name="qa_api_key",
            type=PropertyType.string,
            display_name="API Key",
            description="API key for the chosen provider.",
            display_options=DisplayOptions(show={"qa_use_workflow_llm": [False]}),
        ),
        PropertySpec(
            name="qa_endpoint",
            type=PropertyType.url,
            display_name="Azure Endpoint",
            description="Required for the Azure provider.",
            display_options=DisplayOptions(
                show={"qa_use_workflow_llm": [False], "qa_provider": ["azure"]}
            ),
        ),
    ],
    examples=[
        NodeExample(
            name="basic_qa",
            data={
                "name": "Compliance Check",
                "qa_enabled": True,
                "qa_system_prompt": (
                    "You are a compliance reviewer. Review the transcript and "
                    "produce a JSON object with `tags`, `summary`, "
                    "`call_quality_score`, and `overall_sentiment`."
                ),
                "qa_min_call_duration": 30,
                "qa_sample_rate": 100,
            },
        ),
    ],
)
