"""Spec for the API Trigger node — exposes a public webhook URL that
external systems can hit to launch the workflow."""

from api.services.workflow.node_specs._base import (
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertySpec,
    PropertyType,
)

SPEC = NodeSpec(
    name="trigger",
    display_name="API Trigger",
    description=("Public HTTP endpoints that launch the workflow."),
    llm_hint=(
        "Exposes two public HTTP POST endpoints derived from the auto-generated "
        "`trigger_path`:\n"
        "  • Production: `<backend>/api/v1/public/agent/<trigger_path>` — runs "
        "the published agent. Use this from production systems.\n"
        "  • Test: `<backend>/api/v1/public/agent/test/<trigger_path>` — runs "
        "the latest draft, useful for verifying changes before publishing. "
        "Falls back to the published agent when no draft exists.\n"
        "Both require an API key in the `X-API-Key` header.\n"
        "Request body fields:\n"
        "  • `phone_number` (string, required) — destination to dial.\n"
        "  • `initial_context` (object, optional) — merged into the run's "
        "initial context.\n"
        "  • `telephony_configuration_id` (int, optional) — pick a specific "
        "telephony configuration for the call. Must belong to the same "
        "organization as the trigger. When omitted, the org's default "
        "outbound configuration is used."
    ),
    category=NodeCategory.trigger,
    icon="Webhook",
    properties=[
        PropertySpec(
            name="name",
            type=PropertyType.string,
            display_name="Name",
            description="Short identifier shown in the canvas. No runtime effect.",
            required=True,
            min_length=1,
            default="API Trigger",
        ),
        PropertySpec(
            name="enabled",
            type=PropertyType.boolean,
            display_name="Enabled",
            description="When false, the trigger URL returns 404.",
            default=True,
        ),
        PropertySpec(
            name="trigger_path",
            type=PropertyType.string,
            display_name="Trigger Path",
            description=(
                "Auto-generated UUID-style path segment that uniquely "
                "identifies this trigger. Used in both URLs:\n"
                "  • Production: `/api/v1/public/agent/<trigger_path>` — "
                "executes the published agent.\n"
                "  • Test: `/api/v1/public/agent/test/<trigger_path>` — "
                "executes the latest draft.\n"
                "Do not edit manually."
            ),
        ),
    ],
    examples=[
        NodeExample(
            name="default",
            data={"name": "Inbound Trigger", "enabled": True},
        ),
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0,
        max_incoming=0,
    ),
)
