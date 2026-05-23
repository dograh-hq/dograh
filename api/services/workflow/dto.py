from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from api.services.integrations import (
    all_packages,
)
from api.services.integrations import (
    get_node_data_model as get_integration_node_data_model,
)
from api.services.workflow.node_data import BaseNodeData
from api.services.workflow.node_specs._base import (
    DisplayOptions,
    GraphConstraints,
    NodeCategory,
    NodeExample,
    PropertyOption,
    PropertyType,
)
from api.services.workflow.node_specs.constants import DEFAULT_QA_SYSTEM_PROMPT
from api.services.workflow.node_specs.model_spec import node_spec, spec_field


class NodeType(str, Enum):
    startNode = "startCall"
    endNode = "endCall"
    agentNode = "agentNode"
    globalNode = "globalNode"
    trigger = "trigger"
    webhook = "webhook"
    qa = "qa"


class Position(BaseModel):
    x: float
    y: float


class VariableType(str, Enum):
    string = "string"
    number = "number"
    boolean = "boolean"


class ExtractionVariableDTO(BaseModel):
    name: str = spec_field(
        ...,
        min_length=1,
        ui_type=PropertyType.string,
        display_name="Variable Name",
        description="snake_case identifier used downstream.",
        required=True,
    )
    type: VariableType = spec_field(
        ...,
        display_name="Type",
        description="Data type of the extracted value.",
        required=True,
        options=[
            PropertyOption(value="string", label="String"),
            PropertyOption(value="number", label="Number"),
            PropertyOption(value="boolean", label="Boolean"),
        ],
        spec_default="string",
    )
    prompt: Optional[str] = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Extraction Hint",
        description="Per-variable hint describing what to look for.",
        editor="textarea",
    )


class CustomHeaderDTO(BaseModel):
    key: str = spec_field(
        ...,
        ui_type=PropertyType.string,
        display_name="Header Name",
        description="HTTP header name (e.g., 'X-Source').",
        required=True,
    )
    value: str = spec_field(
        ...,
        ui_type=PropertyType.string,
        display_name="Header Value",
        description="Header value (supports {{template_variables}}).",
        required=True,
    )


# ─────────────────────────────────────────────────────────────────────────
# Per-type node data classes.
#
# Shared fields live on `BaseNodeData` in a neutral module so both core and
# integration nodes can inherit the same workflow contract. Per-type classes
# then add only the mixins they need so mistyped fields raise at validation
# time and downstream consumers get accurate types.
# ─────────────────────────────────────────────────────────────────────────


class _PromptedNodeDataMixin(BaseModel):
    prompt: Optional[str] = spec_field(
        default=None,
        ui_type=PropertyType.mention_textarea,
        display_name="Prompt",
        description="System prompt for this node. Supports {{template_variables}}.",
        required=True,
        min_length=1,
    )
    allow_interrupt: bool = spec_field(
        default=False,
        ui_type=PropertyType.boolean,
        display_name="Allow Interruption",
        description="When true, the user can interrupt the agent mid-utterance.",
    )
    add_global_prompt: bool = spec_field(
        default=True,
        ui_type=PropertyType.boolean,
        display_name="Add Global Prompt",
        description=(
            "When true and a Global node exists, prepends the global prompt to this "
            "node's prompt at runtime."
        ),
    )


class _ExtractionNodeDataMixin(BaseModel):
    extraction_enabled: bool = spec_field(
        default=False,
        ui_type=PropertyType.boolean,
        display_name="Enable Variable Extraction",
        description="When true, runs an LLM extraction pass for this node.",
    )
    extraction_prompt: Optional[str] = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Extraction Prompt",
        description="Overall instructions guiding variable extraction.",
        display_options=DisplayOptions(show={"extraction_enabled": [True]}),
        editor="textarea",
    )
    extraction_variables: Optional[list[ExtractionVariableDTO]] = spec_field(
        default=None,
        display_name="Variables to Extract",
        description=(
            "Each entry declares one variable to capture, with its name, data "
            "type, and extraction hint."
        ),
        display_options=DisplayOptions(show={"extraction_enabled": [True]}),
    )


class _ToolDocumentRefsMixin(BaseModel):
    tool_uuids: Optional[List[str]] = spec_field(
        default=None,
        ui_type=PropertyType.tool_refs,
        display_name="Tools",
        description="Tools this node can invoke.",
        llm_hint="List of tool UUIDs from `list_tools`.",
    )
    document_uuids: Optional[List[str]] = spec_field(
        default=None,
        ui_type=PropertyType.document_refs,
        display_name="Knowledge Base Documents",
        description="Documents this node can reference.",
        llm_hint="List of document UUIDs from `list_documents`.",
    )
    mcp_tool_filters: Optional[Dict[str, List[str]]] = spec_field(
        default=None,
        spec_exclude=True,
    )


@node_spec(
    name="startCall",
    display_name="Start Call",
    description="Entry point of the workflow — plays a greeting and opens the conversation.",
    llm_hint=(
        "The entry point of every workflow (exactly one required). Plays an "
        "optional greeting, can fetch context from an external API before the "
        "call begins, and executes the first conversational turn."
    ),
    category=NodeCategory.call_node,
    icon="Play",
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
        )
    ],
    graph_constraints=GraphConstraints(min_incoming=0, max_incoming=0),
    property_order=(
        "name",
        "greeting_type",
        "greeting",
        "greeting_recording_id",
        "prompt",
        "allow_interrupt",
        "add_global_prompt",
        "delayed_start",
        "delayed_start_duration",
        "extraction_enabled",
        "extraction_prompt",
        "extraction_variables",
        "tool_uuids",
        "document_uuids",
        "pre_call_fetch_enabled",
        "pre_call_fetch_url",
        "pre_call_fetch_credential_uuid",
    ),
    field_overrides={
        "name": {
            "spec_default": "Start Call",
            "description": "Short identifier shown in the canvas and call logs.",
        },
        "prompt": {
            "description": (
                "Agent system prompt for the opening turn. Supports "
                "{{template_variables}} from pre-call fetch and the initial context."
            ),
            "placeholder": "Greet the caller warmly and ask how you can help today.",
        },
        "greeting_type": {
            "display_name": "Greeting Type",
            "description": (
                "Whether the optional greeting is spoken via TTS from text or "
                "played from a pre-recorded audio file."
            ),
            "options": [
                PropertyOption(value="text", label="Text (TTS)"),
                PropertyOption(value="audio", label="Pre-recorded Audio"),
            ],
            "spec_default": "text",
        },
        "greeting": {
            "display_name": "Greeting Text",
            "description": (
                "Text spoken via TTS at the start of the call. Supports "
                "{{template_variables}}. Leave empty to skip the greeting."
            ),
            "display_options": DisplayOptions(show={"greeting_type": ["text"]}),
            "placeholder": "Hi {{first_name}}, this is Sarah from Acme.",
            "editor": "textarea",
        },
        "greeting_recording_id": {
            "display_name": "Greeting Recording",
            "description": "Pre-recorded audio file played at the start of the call.",
            "ui_type": PropertyType.recording_ref,
            "llm_hint": (
                "Value is the `recording_id` string. Use the `list_recordings` "
                "MCP tool to discover available recordings."
            ),
            "display_options": DisplayOptions(show={"greeting_type": ["audio"]}),
        },
        "allow_interrupt": {
            "description": "When true, the user can interrupt the agent mid-utterance.",
        },
        "tool_uuids": {
            "description": "Tools the agent can invoke during the opening turn.",
        },
        "document_uuids": {
            "description": "Documents the agent can reference.",
        },
        "delayed_start": {
            "display_name": "Delayed Start",
            "description": (
                "When true, the agent waits before speaking after pickup. Useful "
                "for outbound calls where the called party needs a moment to settle."
            ),
        },
        "delayed_start_duration": {
            "display_name": "Delay Duration (seconds)",
            "description": "Seconds to wait before the agent speaks. 0.1–10.",
            "spec_default": 2.0,
            "min_value": 0.1,
            "max_value": 10.0,
            "display_options": DisplayOptions(show={"delayed_start": [True]}),
        },
        "pre_call_fetch_enabled": {
            "display_name": "Pre-Call Data Fetch",
            "description": (
                "When true, makes a POST request to an external API before the "
                "call starts and merges the JSON response into the call context "
                "as template variables."
            ),
        },
        "pre_call_fetch_url": {
            "display_name": "Endpoint URL",
            "description": (
                "URL the pre-call POST request is sent to. The request body "
                "includes caller and called numbers."
            ),
            "ui_type": PropertyType.url,
            "display_options": DisplayOptions(show={"pre_call_fetch_enabled": [True]}),
            "placeholder": "https://api.example.com/customer-lookup",
        },
        "pre_call_fetch_credential_uuid": {
            "display_name": "Authentication",
            "description": "Optional credential attached to the pre-call request.",
            "ui_type": PropertyType.credential_ref,
            "llm_hint": "Credential UUID from `list_credentials`.",
            "display_options": DisplayOptions(show={"pre_call_fetch_enabled": [True]}),
        },
    },
)
class StartCallNodeData(
    BaseNodeData,
    _PromptedNodeDataMixin,
    _ExtractionNodeDataMixin,
    _ToolDocumentRefsMixin,
):
    is_start: bool = spec_field(default=True, spec_exclude=True)
    greeting: Optional[str] = spec_field(default=None, ui_type=PropertyType.string)
    greeting_type: Optional[str] = spec_field(
        default=None, ui_type=PropertyType.options
    )
    greeting_recording_id: Optional[str] = spec_field(
        default=None, ui_type=PropertyType.recording_ref
    )
    delayed_start: bool = spec_field(default=False, ui_type=PropertyType.boolean)
    delayed_start_duration: Optional[float] = spec_field(
        default=None, ui_type=PropertyType.number
    )
    pre_call_fetch_enabled: bool = spec_field(
        default=False, ui_type=PropertyType.boolean
    )
    pre_call_fetch_url: Optional[str] = spec_field(
        default=None, ui_type=PropertyType.url
    )
    pre_call_fetch_credential_uuid: Optional[str] = spec_field(
        default=None, ui_type=PropertyType.credential_ref
    )


@node_spec(
    name="agentNode",
    display_name="Agent Node",
    description="Conversational step — the LLM runs one focused exchange.",
    llm_hint=(
        "Mid-call step executed by the LLM. Most workflows are a chain of agent "
        "nodes connected by edges that describe transition conditions. Each agent "
        "node can invoke tools and reference documents."
    ),
    category=NodeCategory.call_node,
    icon="Headset",
    examples=[
        NodeExample(
            name="qualify_lead",
            data={
                "name": "Qualify Budget",
                "prompt": "Ask about budget and timeline. Capture both before transitioning.",
                "allow_interrupt": True,
                "extraction_enabled": True,
                "extraction_prompt": "Extract budget amount and rough timeline.",
                "extraction_variables": [
                    {
                        "name": "budget_usd",
                        "type": "number",
                        "prompt": "Stated budget in USD",
                    },
                    {
                        "name": "timeline",
                        "type": "string",
                        "prompt": "When they want to start",
                    },
                ],
            },
        )
    ],
    graph_constraints=GraphConstraints(min_incoming=1),
    property_order=(
        "name",
        "prompt",
        "allow_interrupt",
        "add_global_prompt",
        "extraction_enabled",
        "extraction_prompt",
        "extraction_variables",
        "tool_uuids",
        "document_uuids",
    ),
    field_overrides={
        "name": {
            "spec_default": "Agent",
            "description": (
                "Short identifier for this step (e.g., 'Qualify Budget'). Appears "
                "in call logs and edge transition tools."
            ),
        },
        "prompt": {
            "description": (
                "Agent system prompt for this step. Supports {{template_variables}} "
                "from extraction or pre-call fetch."
            ),
            "placeholder": "Ask the caller about their budget and timeline.",
        },
        "allow_interrupt": {
            "description": (
                "When true, the user can interrupt the agent mid-utterance. Set "
                "false for non-interruptible disclosures."
            ),
            "spec_default": True,
        },
        "tool_uuids": {
            "description": "Tools the agent can invoke during this step.",
        },
        "document_uuids": {
            "description": "Documents the agent can reference during this step.",
        },
    },
)
class AgentNodeData(
    BaseNodeData,
    _PromptedNodeDataMixin,
    _ExtractionNodeDataMixin,
    _ToolDocumentRefsMixin,
):
    pass


@node_spec(
    name="endCall",
    display_name="End Call",
    description="Closes the conversation and hangs up.",
    llm_hint=(
        "Terminal node that politely closes the conversation. Variable extraction "
        "can run before hangup. A workflow can have multiple endCall nodes reached "
        "via different edge conditions."
    ),
    category=NodeCategory.call_node,
    icon="OctagonX",
    examples=[
        NodeExample(
            name="successful_close",
            data={
                "name": "Successful Close",
                "prompt": "Confirm the appointment time, thank the caller, and end the call.",
                "add_global_prompt": False,
            },
        )
    ],
    graph_constraints=GraphConstraints(min_incoming=1, min_outgoing=0, max_outgoing=0),
    property_order=(
        "name",
        "prompt",
        "add_global_prompt",
        "extraction_enabled",
        "extraction_prompt",
        "extraction_variables",
    ),
    field_overrides={
        "name": {
            "spec_default": "End Call",
            "description": (
                "Short identifier shown in call logs. Should describe the ending "
                "context (e.g., 'Successful close', 'Polite decline')."
            ),
        },
        "prompt": {
            "description": (
                "Agent system prompt for the closing exchange. Supports "
                "{{template_variables}} from extraction or pre-call fetch."
            ),
            "placeholder": "Thank the caller and confirm next steps before ending the call.",
        },
        "allow_interrupt": {"spec_exclude": True},
        "add_global_prompt": {
            "description": (
                "When true and a Global node exists, prepends the global prompt "
                "to this node's prompt at runtime."
            ),
            "spec_default": False,
        },
        "extraction_enabled": {
            "description": (
                "When true, runs an LLM extraction pass before hangup to capture "
                "variables from the conversation."
            )
        },
        "extraction_prompt": {
            "description": (
                "Overall instructions guiding how variables should be extracted "
                "from the conversation."
            )
        },
        "extraction_variables": {
            "description": (
                "Each entry declares one variable to capture from the conversation, "
                "with its name, data type, and a per-variable extraction hint."
            )
        },
    },
)
class EndCallNodeData(
    BaseNodeData,
    _PromptedNodeDataMixin,
    _ExtractionNodeDataMixin,
):
    is_end: bool = spec_field(default=True, spec_exclude=True)


@node_spec(
    name="globalNode",
    display_name="Global Node",
    description="Persona/tone appended to every agent node's prompt.",
    llm_hint=(
        "System-level prompt appended to every prompted node whose "
        "`add_global_prompt` is true. Use it for persona, tone, and shared "
        "rules that apply across the entire conversation. At most one global "
        "node per workflow."
    ),
    category=NodeCategory.global_node,
    icon="Globe",
    examples=[
        NodeExample(
            name="basic_persona",
            description="Establishes a consistent persona across the call.",
            data={
                "name": "Persona",
                "prompt": (
                    "You are Sarah, a polite and warm representative from Acme Corp. "
                    "Always thank the caller for their time and speak in short "
                    "conversational sentences."
                ),
            },
        )
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0,
        max_incoming=0,
        min_outgoing=0,
        max_outgoing=0,
    ),
    property_order=("name", "prompt"),
    field_overrides={
        "name": {
            "spec_default": "Global Node",
            "description": (
                "Short identifier shown in the canvas and call logs. Has no "
                "runtime effect."
            ),
        },
        "prompt": {
            "display_name": "Global Prompt",
            "description": (
                "Text appended to every prompted node's system prompt when that "
                "node has `add_global_prompt=true`. Supports {{template_variables}}."
            ),
            "placeholder": (
                "You are a friendly assistant calling on behalf of {{company_name}}."
            ),
            "spec_default": (
                "You are a helpful assistant whose mode of interaction with the "
                "user is voice. So don't use any special characters which can not "
                "be pronounced. Use short sentences and simple language."
            ),
        },
        "allow_interrupt": {"spec_exclude": True},
        "add_global_prompt": {"spec_exclude": True},
    },
)
class GlobalNodeData(BaseNodeData, _PromptedNodeDataMixin):
    pass


@node_spec(
    name="trigger",
    display_name="API Trigger",
    description="Public HTTP endpoints that launch the workflow.",
    llm_hint=(
        "Exposes two public HTTP POST endpoints derived from the auto-generated "
        "`trigger_path`:\n"
        "  • Production: `<backend>/api/v1/public/agent/<trigger_path>` — runs "
        "the published agent. Use this from production systems.\n"
        "  • Test: `<backend>/api/v1/public/agent/test/<trigger_path>` — runs "
        "the latest draft, useful for verifying changes before publishing. Falls "
        "back to the published agent when no draft exists.\n"
        "Both require an API key in the `X-API-Key` header.\n"
        "Request body fields:\n"
        "  • `phone_number` (string, required) — destination to dial.\n"
        "  • `initial_context` (object, optional) — merged into the run's initial context.\n"
        "  • `telephony_configuration_id` (int, optional) — pick a specific telephony "
        "configuration for the call. Must belong to the same organization as the "
        "trigger. When omitted, the org's default outbound configuration is used."
    ),
    category=NodeCategory.trigger,
    icon="Webhook",
    examples=[
        NodeExample(name="default", data={"name": "Inbound Trigger", "enabled": True})
    ],
    graph_constraints=GraphConstraints(min_incoming=0, max_incoming=0),
    property_order=("name", "enabled", "trigger_path"),
    field_overrides={
        "name": {
            "spec_default": "API Trigger",
            "description": "Short identifier shown in the canvas. No runtime effect.",
        },
        "enabled": {
            "display_name": "Enabled",
            "description": "When false, the trigger URL returns 404.",
        },
        "trigger_path": {
            "display_name": "Trigger Path",
            "description": (
                "Path segment that uniquely identifies "
                "this trigger. Used in both URLs:\n"
                "  • Production: `/api/v1/public/agent/<trigger_path>` — executes "
                "the published agent.\n"
                "  • Test: `/api/v1/public/agent/test/<trigger_path>` — executes "
                "the latest draft.\n"
                "Can be customized to a descriptive value up to 36 characters "
                "using letters, numbers, hyphens, or underscores."
            ),
        },
    },
)
class TriggerNodeData(BaseNodeData):
    trigger_path: Optional[str] = spec_field(default=None, ui_type=PropertyType.string)
    enabled: bool = spec_field(default=True, ui_type=PropertyType.boolean)


@node_spec(
    name="webhook",
    display_name="Webhook",
    description="Send HTTP request after the workflow completes.",
    llm_hint=(
        "Sends an HTTP request to an external system after the workflow completes. "
        "The payload is a Jinja-templated JSON body with access to "
        "`workflow_run_id`, `initial_context`, `gathered_context`, `annotations`, "
        "and call metadata."
    ),
    category=NodeCategory.integration,
    icon="Link2",
    examples=[
        NodeExample(
            name="post_to_crm",
            data={
                "name": "Notify CRM",
                "enabled": True,
                "http_method": "POST",
                "endpoint_url": "https://crm.example.com/calls",
                "payload_template": {
                    "run_id": "{{workflow_run_id}}",
                    "outcome": "{{gathered_context.call_disposition}}",
                },
            },
        )
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0
    ),
    property_order=(
        "name",
        "enabled",
        "http_method",
        "endpoint_url",
        "credential_uuid",
        "custom_headers",
        "payload_template",
    ),
    field_overrides={
        "name": {
            "spec_default": "Webhook",
            "description": "Short identifier shown in the canvas and run logs.",
        },
        "enabled": {
            "display_name": "Enabled",
            "description": "When false, the webhook is skipped at run time.",
        },
        "http_method": {
            "display_name": "HTTP Method",
            "description": "HTTP verb used for the outbound request.",
            "options": [
                PropertyOption(value="GET", label="GET"),
                PropertyOption(value="POST", label="POST"),
                PropertyOption(value="PUT", label="PUT"),
                PropertyOption(value="PATCH", label="PATCH"),
                PropertyOption(value="DELETE", label="DELETE"),
            ],
            "spec_default": "POST",
        },
        "endpoint_url": {
            "display_name": "Endpoint URL",
            "description": "URL the request is sent to.",
            "ui_type": PropertyType.url,
            "placeholder": "https://api.example.com/webhook",
        },
        "credential_uuid": {
            "display_name": "Authentication",
            "description": "Optional credential applied as the Authorization header.",
            "ui_type": PropertyType.credential_ref,
            "llm_hint": "Credential UUID from `list_credentials`.",
        },
        "custom_headers": {
            "display_name": "Custom Headers",
            "description": "Additional HTTP headers to include with the request.",
        },
        "payload_template": {
            "display_name": "Payload Template",
            "description": (
                "JSON body of the request. Values are Jinja-rendered against the "
                "run context — `{{workflow_run_id}}`, `{{gathered_context.foo}}`, "
                "`{{annotations.qa_xxx}}`, etc."
            ),
            "ui_type": PropertyType.json,
            "spec_default": {
                "call_id": "{{workflow_run_id}}",
                "first_name": "{{initial_context.first_name}}",
                "rsvp": "{{gathered_context.rsvp}}",
                "duration": "{{cost_info.call_duration_seconds}}",
                "recording_url": "{{recording_url}}",
                "transcript_url": "{{transcript_url}}",
            },
        },
    },
)
class WebhookNodeData(BaseNodeData):
    enabled: bool = spec_field(default=True, ui_type=PropertyType.boolean)
    http_method: Optional[str] = spec_field(default=None, ui_type=PropertyType.options)
    endpoint_url: Optional[str] = spec_field(default=None, ui_type=PropertyType.url)
    credential_uuid: Optional[str] = spec_field(
        default=None, ui_type=PropertyType.credential_ref
    )
    custom_headers: Optional[list[CustomHeaderDTO]] = spec_field(default=None)
    payload_template: Optional[dict] = spec_field(
        default=None, ui_type=PropertyType.json
    )


@node_spec(
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
        )
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0
    ),
    property_order=(
        "name",
        "qa_enabled",
        "qa_system_prompt",
        "qa_min_call_duration",
        "qa_voicemail_calls",
        "qa_sample_rate",
        "qa_use_workflow_llm",
        "qa_provider",
        "qa_model",
        "qa_api_key",
        "qa_endpoint",
    ),
    field_overrides={
        "name": {
            "spec_default": "QA Analysis",
            "description": "Short identifier for this QA configuration.",
        },
        "qa_enabled": {
            "display_name": "Enabled",
            "description": "When false, the QA run is skipped.",
        },
        "qa_system_prompt": {
            "display_name": "System Prompt",
            "description": (
                "Instructions to the QA reviewer LLM. Supports placeholders: "
                "`{node_summary}`, `{previous_conversation_summary}`, "
                "`{transcript}`, `{metrics}`."
            ),
            "spec_default": DEFAULT_QA_SYSTEM_PROMPT,
            "editor": "textarea",
        },
        "qa_min_call_duration": {
            "display_name": "Minimum Call Duration (seconds)",
            "description": "Calls shorter than this are skipped.",
            "min_value": 0,
        },
        "qa_voicemail_calls": {
            "display_name": "Include Voicemail Calls",
            "description": "When false, calls flagged as voicemail are skipped.",
        },
        "qa_sample_rate": {
            "display_name": "Sample Rate (%)",
            "description": (
                "Percent of eligible calls QA'd. 100 means every call; lower "
                "values use random sampling."
            ),
            "min_value": 1,
            "max_value": 100,
        },
        "qa_use_workflow_llm": {
            "display_name": "Use Workflow's LLM",
            "description": (
                "When true, the QA pass uses the same LLM the workflow runs with. "
                "Set false to specify a separate provider/model."
            ),
        },
        "qa_provider": {
            "display_name": "QA LLM Provider",
            "description": "LLM provider used for the QA pass.",
            "options": [
                PropertyOption(value="openai", label="OpenAI"),
                PropertyOption(value="azure", label="Azure OpenAI"),
                PropertyOption(value="openrouter", label="OpenRouter"),
                PropertyOption(value="anthropic", label="Anthropic"),
            ],
            "display_options": DisplayOptions(show={"qa_use_workflow_llm": [False]}),
        },
        "qa_model": {
            "display_name": "QA Model",
            "description": (
                "Model identifier (e.g., 'gpt-4o', 'claude-sonnet-4-6'). "
                "Provider-specific."
            ),
            "spec_default": "default",
            "display_options": DisplayOptions(show={"qa_use_workflow_llm": [False]}),
        },
        "qa_api_key": {
            "display_name": "API Key",
            "description": "API key for the chosen provider.",
            "display_options": DisplayOptions(show={"qa_use_workflow_llm": [False]}),
        },
        "qa_endpoint": {
            "display_name": "Azure Endpoint",
            "description": "Required for the Azure provider.",
            "ui_type": PropertyType.url,
            "display_options": DisplayOptions(
                show={"qa_use_workflow_llm": [False], "qa_provider": ["azure"]}
            ),
        },
    },
)
class QANodeData(BaseNodeData):
    qa_enabled: bool = spec_field(default=True, ui_type=PropertyType.boolean)
    qa_use_workflow_llm: bool = spec_field(default=True, ui_type=PropertyType.boolean)
    qa_provider: Optional[str] = spec_field(default=None, ui_type=PropertyType.options)
    qa_model: Optional[str] = spec_field(default=None, ui_type=PropertyType.string)
    qa_api_key: Optional[str] = spec_field(default=None, ui_type=PropertyType.string)
    qa_endpoint: Optional[str] = spec_field(default=None, ui_type=PropertyType.url)
    qa_system_prompt: Optional[str] = spec_field(
        default=None, ui_type=PropertyType.string
    )
    qa_min_call_duration: int = spec_field(default=15, ui_type=PropertyType.number)
    qa_voicemail_calls: bool = spec_field(default=False, ui_type=PropertyType.boolean)
    qa_sample_rate: int = spec_field(default=100, ui_type=PropertyType.number)


# Union of every per-type data class — useful as a type annotation on
# consumers that handle any node data without dispatching on type. Cannot
# be called as a constructor; use the per-type class directly.
NodeDataDTO = Union[
    StartCallNodeData,
    AgentNodeData,
    EndCallNodeData,
    GlobalNodeData,
    TriggerNodeData,
    WebhookNodeData,
    QANodeData,
]


# ─────────────────────────────────────────────────────────────────────────
# Per-type RF nodes.
#
# Core node variants keep concrete helper classes for tests and type-aware
# consumers. The persisted workflow DTO itself validates `type` dynamically
# against the core registry plus any integration packages.
# ─────────────────────────────────────────────────────────────────────────


class _RFNodeBase(BaseModel):
    id: str
    position: Position


def _require_prompt(data, type_label: str) -> None:
    prompt = getattr(data, "prompt", None)
    if not prompt or len(prompt.strip()) == 0:
        raise ValueError(f"Prompt is required for {type_label} nodes")


class StartCallRFNode(_RFNodeBase):
    type: Literal["startCall"] = "startCall"
    data: StartCallNodeData

    @model_validator(mode="after")
    def _validate(self):
        _require_prompt(self.data, "start")
        return self


class AgentRFNode(_RFNodeBase):
    type: Literal["agentNode"] = "agentNode"
    data: AgentNodeData

    @model_validator(mode="after")
    def _validate(self):
        _require_prompt(self.data, "agent")
        return self


class EndCallRFNode(_RFNodeBase):
    type: Literal["endCall"] = "endCall"
    data: EndCallNodeData

    @model_validator(mode="after")
    def _validate(self):
        _require_prompt(self.data, "end")
        return self


class GlobalRFNode(_RFNodeBase):
    type: Literal["globalNode"] = "globalNode"
    data: GlobalNodeData

    @model_validator(mode="after")
    def _validate(self):
        _require_prompt(self.data, "global")
        return self


class TriggerRFNode(_RFNodeBase):
    type: Literal["trigger"] = "trigger"
    data: TriggerNodeData


class WebhookRFNode(_RFNodeBase):
    type: Literal["webhook"] = "webhook"
    data: WebhookNodeData


class QARFNode(_RFNodeBase):
    type: Literal["qa"] = "qa"
    data: QANodeData


_PROMPT_REQUIRED_NODE_TYPES: dict[str, str] = {
    NodeType.startNode.value: "start",
    NodeType.agentNode.value: "agent",
    NodeType.endNode.value: "end",
    NodeType.globalNode.value: "global",
}


class RFNodeDTO(_RFNodeBase):
    type: str = Field(..., min_length=1)
    data: Any

    @field_validator("type")
    @classmethod
    def _validate_type(cls, value: str) -> str:
        if get_node_data_model(value) is None:
            raise ValueError(f"Unknown node type: {value!r}")
        return value

    @model_validator(mode="after")
    def _validate(self):
        data_model = get_node_data_model(self.type)
        if data_model is None:
            raise ValueError(f"Unknown node type: {self.type!r}")

        self.data = data_model.model_validate(self.data)

        prompt_label = _PROMPT_REQUIRED_NODE_TYPES.get(self.type)
        if prompt_label:
            _require_prompt(self.data, prompt_label)

        return self


# ─────────────────────────────────────────────────────────────────────────
# Edges
# ─────────────────────────────────────────────────────────────────────────


class EdgeDataDTO(BaseModel):
    label: str = Field(..., min_length=1)
    condition: str = Field(..., min_length=1)
    transition_speech: Optional[str] = None
    transition_speech_type: Optional[str] = None  # 'text' or 'audio'
    transition_speech_recording_id: Optional[str] = None


class RFEdgeDTO(BaseModel):
    id: str
    source: str
    target: str
    data: EdgeDataDTO


class ReactFlowDTO(BaseModel):
    nodes: List[RFNodeDTO]
    edges: List[RFEdgeDTO]

    @model_validator(mode="after")
    def _referential_integrity(self):
        node_ids = {n.id for n in self.nodes}
        line_errors: list[dict[str, str]] = []

        for idx, edge in enumerate(self.edges):
            for endpoint in (edge.source, edge.target):
                if endpoint not in node_ids:
                    line_errors.append(
                        dict(
                            loc=("edges", idx),
                            type="missing_node",
                            msg="Edge references missing node",
                            input=edge.model_dump(mode="python"),
                            ctx={"edge_id": edge.id, "endpoint": endpoint},
                        )
                    )

        if line_errors:
            raise ValidationError.from_exception_data(
                title="ReactFlowDTO validation failed",
                line_errors=line_errors,
            )

        return self


_CORE_NODE_DATA_CLASSES: dict[str, type[BaseNodeData]] = {
    NodeType.startNode.value: StartCallNodeData,
    NodeType.agentNode.value: AgentNodeData,
    NodeType.endNode.value: EndCallNodeData,
    NodeType.globalNode.value: GlobalNodeData,
    NodeType.trigger.value: TriggerNodeData,
    NodeType.webhook.value: WebhookNodeData,
    NodeType.qa.value: QANodeData,
}


def get_node_data_model(type_name: str) -> type[BaseNodeData] | None:
    return _CORE_NODE_DATA_CLASSES.get(type_name) or get_integration_node_data_model(
        type_name
    )


def all_node_type_names() -> set[str]:
    return set(_CORE_NODE_DATA_CLASSES) | {
        node.type_name for package in all_packages() for node in package.nodes
    }


def sanitize_workflow_definition(definition: dict | None) -> dict | None:
    """Strip unknown fields from each node.data and edge.data so UI-only
    runtime state (`invalid`, `validationMessage`, etc.) doesn't leak into
    persisted workflow JSON.

    Only `.data` is filtered — top-level keys on nodes/edges/definition
    (viewport, ReactFlow-computed width/height, etc.) are preserved as-is.
    This is a stripper, not a validator: it doesn't enforce required fields
    or run model_validators, so partial drafts save cleanly.
    """
    if not definition:
        return definition

    out = dict(definition)
    raw_nodes = out.get("nodes")
    if isinstance(raw_nodes, list):
        out["nodes"] = [_sanitize_node(n) for n in raw_nodes]
    raw_edges = out.get("edges")
    if isinstance(raw_edges, list):
        out["edges"] = [_sanitize_edge(e) for e in raw_edges]
    return out


def _sanitize_node(node):
    if not isinstance(node, dict):
        return node
    data_cls = get_node_data_model(node.get("type"))
    raw_data = node.get("data")
    if not data_cls or not isinstance(raw_data, dict):
        return node
    allowed = data_cls.model_fields.keys()
    return {**node, "data": {k: v for k, v in raw_data.items() if k in allowed}}


def _sanitize_edge(edge):
    if not isinstance(edge, dict):
        return edge
    raw_data = edge.get("data")
    if not isinstance(raw_data, dict):
        return edge
    allowed = EdgeDataDTO.model_fields.keys()
    return {**edge, "data": {k: v for k, v in raw_data.items() if k in allowed}}
