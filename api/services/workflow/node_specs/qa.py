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

DEFAULT_QA_SYSTEM_PROMPT = """You are a QA analyst evaluating a specific segment of a voice AI conversation.

## Node Purpose
{{node_summary}}

## Previous Conversation Context (For start of conversation, previous conversation summary can be empty.)
{{previous_conversation_summary}}

## Tags to evaluate

Examine the conversation carefully and identify which of the following tags apply:

- UNCLEAR_CONVERSATION - The conversation is not coherent or clear, messages don't connect logically
- ASSISTANT_IN_LOOP - The assistant asks the same question multiple times or gets stuck repeating itself
- ASSISTANT_REPLY_IMPROPER - The assistant did not reply properly to the user's question/query or seems confused by what the user said
- USER_FRUSTRATED - The user seems angry, frustrated, or is complaining about something in the call
- USER_NOT_UNDERSTANDING - The user explicitly says they don't understand or repeatedly asks for clarification
- HEARING_ISSUES - Either party can't hear the other ("hello?", "are you there?", "can you hear me?")
- DEAD_AIR - Unusually long silences in the conversation (use the timestamps to judge)
- USER_REQUESTING_FEATURE - The user asks for something the assistant can't fulfill
- ASSISTANT_LACKS_EMPATHY - The assistant ignores the user's personal situation or emotional state and continues pitching or pushing the agenda.
- USER_DETECTS_AI - The user suspects or identifies that they are talking to an AI/robot/bot rather than a real human.

## Call metrics (pre-computed)

Use these alongside the transcript for your analysis:
{{metrics}}

## Output format

Return ONLY a valid JSON object (no markdown):
{
    "tags": [
        {
            "tag": "TAG_NAME",
            "reason": "Short reason with evidence from the transcript"
        }
    ],
    "overall_sentiment": "positive|neutral|negative",
    "call_quality_score": <1-10>,
    "summary": "1-2 sentence summary of this segment"
}

If no tags apply, return an empty tags list. Always provide sentiment, score, and summary."""


SPEC = NodeSpec(
    name="qa",
    display_name="QA Analysis",
    description="Run LLM quality analysis on the call transcript.",
    llm_hint=(
        "Runs an LLM quality review on the call transcript after completion. "
        "Per-node analysis splits the conversation by node and evaluates each "
        "segment against the configured system prompt. Sampling, minimum "
        "duration, and voicemail filters are supported."
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
            default="QA Analysis",
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
            default=DEFAULT_QA_SYSTEM_PROMPT,
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
            default="default",
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
