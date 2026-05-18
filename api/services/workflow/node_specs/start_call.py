"""Spec for the Start Call node — the single entry point of every workflow.
Carries greeting, pre-call data fetch, and the same prompt/extraction/tools
fields as agent nodes."""

from api.services.workflow.node_specs._base import (
    DisplayOptions,
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertyOption,
    PropertySpec,
    PropertyType,
)

SPEC = NodeSpec(
    name="startCall",
    display_name="Start Call",
    description="Entry point of the workflow — plays a greeting and opens the conversation.",
    llm_hint=(
        "The entry point of every workflow (exactly one required). Plays an "
        "optional greeting, can fetch context from an external API before "
        "the call begins, and executes the first conversational turn."
    ),
    category=NodeCategory.call_node,
    icon="Play",
    properties=[
        PropertySpec(
            name="name",
            type=PropertyType.string,
            display_name="Name",
            description="Short identifier shown in the canvas and call logs.",
            required=True,
            min_length=1,
            default="Start Call",
        ),
        # ---- Greeting (variant via greeting_type) ----
        PropertySpec(
            name="greeting_type",
            type=PropertyType.options,
            display_name="Greeting Type",
            description=(
                "Whether the optional greeting is spoken via TTS from text "
                "or played from a pre-recorded audio file."
            ),
            default="text",
            options=[
                PropertyOption(value="text", label="Text (TTS)"),
                PropertyOption(value="audio", label="Pre-recorded Audio"),
            ],
        ),
        PropertySpec(
            name="greeting",
            type=PropertyType.string,
            display_name="Greeting Text",
            description=(
                "Text spoken via TTS at the start of the call. Supports "
                "{{template_variables}}. Leave empty to skip the greeting."
            ),
            display_options=DisplayOptions(show={"greeting_type": ["text"]}),
            editor="textarea",
            placeholder="Hi {{first_name}}, this is Sarah from Acme.",
        ),
        PropertySpec(
            name="greeting_recording_id",
            type=PropertyType.recording_ref,
            display_name="Greeting Recording",
            description="Pre-recorded audio file played at the start of the call.",
            llm_hint=(
                "Value is the `recording_id` string. Use the `list_recordings` "
                "MCP tool to discover available recordings."
            ),
            display_options=DisplayOptions(show={"greeting_type": ["audio"]}),
        ),
        PropertySpec(
            name="prompt",
            type=PropertyType.mention_textarea,
            display_name="Prompt",
            description=(
                "Agent system prompt for the opening turn. Supports "
                "{{template_variables}} from pre-call fetch and the initial context."
            ),
            required=True,
            min_length=1,
            placeholder="Greet the caller warmly and ask how you can help today.",
        ),
        # ---- Behavior toggles ----
        PropertySpec(
            name="allow_interrupt",
            type=PropertyType.boolean,
            display_name="Allow Interruption",
            description=("When true, the user can interrupt the agent mid-utterance."),
            default=False,
        ),
        PropertySpec(
            name="add_global_prompt",
            type=PropertyType.boolean,
            display_name="Add Global Prompt",
            description=(
                "When true and a Global node exists, prepends the global "
                "prompt to this node's prompt at runtime."
            ),
            default=True,
        ),
        PropertySpec(
            name="delayed_start",
            type=PropertyType.boolean,
            display_name="Delayed Start",
            description=(
                "When true, the agent waits before speaking after pickup. "
                "Useful for outbound calls where the called party needs a "
                "moment to settle."
            ),
            default=False,
        ),
        PropertySpec(
            name="delayed_start_duration",
            type=PropertyType.number,
            display_name="Delay Duration (seconds)",
            description="Seconds to wait before the agent speaks. 0.1–10.",
            default=2.0,
            min_value=0.1,
            max_value=10.0,
            display_options=DisplayOptions(show={"delayed_start": [True]}),
        ),
        # ---- Variable extraction ----
        PropertySpec(
            name="extraction_enabled",
            type=PropertyType.boolean,
            display_name="Enable Variable Extraction",
            description=(
                "When true, runs an LLM extraction pass on transition out of "
                "this node to capture variables from the opening turn."
            ),
            default=False,
        ),
        PropertySpec(
            name="extraction_prompt",
            type=PropertyType.string,
            display_name="Extraction Prompt",
            description="Overall instructions guiding variable extraction.",
            display_options=DisplayOptions(show={"extraction_enabled": [True]}),
            editor="textarea",
        ),
        PropertySpec(
            name="extraction_variables",
            type=PropertyType.fixed_collection,
            display_name="Variables to Extract",
            description=(
                "Each entry declares one variable to capture, with its name, "
                "data type, and per-variable extraction hint."
            ),
            display_options=DisplayOptions(show={"extraction_enabled": [True]}),
            properties=[
                PropertySpec(
                    name="name",
                    type=PropertyType.string,
                    display_name="Variable Name",
                    description="snake_case identifier used downstream.",
                    required=True,
                ),
                PropertySpec(
                    name="type",
                    type=PropertyType.options,
                    display_name="Type",
                    description="Data type of the extracted value.",
                    required=True,
                    default="string",
                    options=[
                        PropertyOption(value="string", label="String"),
                        PropertyOption(value="number", label="Number"),
                        PropertyOption(value="boolean", label="Boolean"),
                    ],
                ),
                PropertySpec(
                    name="prompt",
                    type=PropertyType.string,
                    display_name="Extraction Hint",
                    description="Per-variable hint describing what to look for.",
                    editor="textarea",
                ),
            ],
        ),
        # ---- Tools / documents ----
        PropertySpec(
            name="tool_uuids",
            type=PropertyType.tool_refs,
            display_name="Tools",
            description="Tools the agent can invoke during the opening turn.",
            llm_hint="List of tool UUIDs from `list_tools`.",
        ),
        PropertySpec(
            name="document_uuids",
            type=PropertyType.document_refs,
            display_name="Knowledge Base Documents",
            description="Documents the agent can reference.",
            llm_hint="List of document UUIDs from `list_documents`.",
        ),
        # ---- Pre-call data fetch (advanced) ----
        PropertySpec(
            name="pre_call_fetch_enabled",
            type=PropertyType.boolean,
            display_name="Pre-Call Data Fetch",
            description=(
                "When true, makes a POST request to an external API before "
                "the call starts and merges the JSON response into the call "
                "context as template variables."
            ),
            default=False,
        ),
        PropertySpec(
            name="pre_call_fetch_url",
            type=PropertyType.url,
            display_name="Endpoint URL",
            description=(
                "URL the pre-call POST request is sent to. The request body "
                "includes caller and called numbers."
            ),
            display_options=DisplayOptions(show={"pre_call_fetch_enabled": [True]}),
            placeholder="https://api.example.com/customer-lookup",
        ),
        PropertySpec(
            name="pre_call_fetch_credential_uuid",
            type=PropertyType.credential_ref,
            display_name="Authentication",
            description="Optional credential attached to the pre-call request.",
            llm_hint="Credential UUID from `list_credentials`.",
            display_options=DisplayOptions(show={"pre_call_fetch_enabled": [True]}),
        ),
    ],
    examples=[
        NodeExample(
            name="warm_greeting",
            data={
                "name": "Greeting",
                "prompt": "Greet warmly and ask the caller's reason for calling.",
                "greeting_type": "text",
                "greeting": "Hi {{first_name}}, this is Sarah from Acme.",
                "allow_interrupt": True,
            },
        ),
    ],
    # `min_outgoing` is intentionally unset: a startCall is allowed to
    # sit on the canvas without an outgoing edge (e.g. a workflow with
    # just a greeting). Only constraint: nothing flows INTO the start.
    graph_constraints=GraphConstraints(
        min_incoming=0,
        max_incoming=0,
    ),
)
