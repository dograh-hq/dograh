"""Spec for the Webhook node — sends an HTTP request to an external system
after the workflow completes."""

from api.services.workflow.node_specs._base import (
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertyOption,
    PropertySpec,
    PropertyType,
)

SPEC = NodeSpec(
    name="webhook",
    display_name="Webhook",
    description="Send HTTP request after the workflow completes.",
    llm_hint=(
        "Sends an HTTP request to an external system after the workflow "
        "completes. The payload is a Jinja-templated JSON body with access "
        "to `workflow_run_id`, `initial_context`, `gathered_context`, "
        "`annotations`, and call metadata."
    ),
    category=NodeCategory.integration,
    icon="Link2",
    properties=[
        PropertySpec(
            name="name",
            type=PropertyType.string,
            display_name="Name",
            description="Short identifier shown in the canvas and run logs.",
            required=True,
            min_length=1,
            default="Webhook",
        ),
        PropertySpec(
            name="enabled",
            type=PropertyType.boolean,
            display_name="Enabled",
            description="When false, the webhook is skipped at run time.",
            default=True,
        ),
        PropertySpec(
            name="http_method",
            type=PropertyType.options,
            display_name="HTTP Method",
            description="HTTP verb used for the outbound request.",
            default="POST",
            options=[
                PropertyOption(value="GET", label="GET"),
                PropertyOption(value="POST", label="POST"),
                PropertyOption(value="PUT", label="PUT"),
                PropertyOption(value="PATCH", label="PATCH"),
                PropertyOption(value="DELETE", label="DELETE"),
            ],
        ),
        PropertySpec(
            name="endpoint_url",
            type=PropertyType.url,
            display_name="Endpoint URL",
            description="URL the request is sent to.",
            placeholder="https://api.example.com/webhook",
        ),
        PropertySpec(
            name="credential_uuid",
            type=PropertyType.credential_ref,
            display_name="Authentication",
            description="Optional credential applied as the Authorization header.",
            llm_hint="Credential UUID from `list_credentials`.",
        ),
        PropertySpec(
            name="custom_headers",
            type=PropertyType.fixed_collection,
            display_name="Custom Headers",
            description="Additional HTTP headers to include with the request.",
            properties=[
                PropertySpec(
                    name="key",
                    type=PropertyType.string,
                    display_name="Header Name",
                    description="HTTP header name (e.g., 'X-Source').",
                    required=True,
                ),
                PropertySpec(
                    name="value",
                    type=PropertyType.string,
                    display_name="Header Value",
                    description="Header value (supports {{template_variables}}).",
                    required=True,
                ),
            ],
        ),
        PropertySpec(
            name="payload_template",
            type=PropertyType.json,
            display_name="Payload Template",
            description=(
                "JSON body of the request. Values are Jinja-rendered against "
                "the run context — `{{workflow_run_id}}`, "
                "`{{gathered_context.foo}}`, `{{annotations.qa_xxx}}`, etc."
            ),
            default={
                "call_id": "{{workflow_run_id}}",
                "first_name": "{{initial_context.first_name}}",
                "rsvp": "{{gathered_context.rsvp}}",
                "duration": "{{cost_info.call_duration_seconds}}",
                "recording_url": "{{recording_url}}",
                "transcript_url": "{{transcript_url}}",
            },
        ),
    ],
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
        ),
    ],
    # Webhooks fire post-call (run_integrations scans nodes by type),
    # never as a graph step. Reject any edge into or out of a webhook so
    # the editor can't wire one into the conversation flow.
    graph_constraints=GraphConstraints(
        min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0
    ),
)
