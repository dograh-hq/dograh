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
    description="Public HTTP endpoint that launches the workflow.",
    llm_hint=(
        "Exposes a public HTTP POST endpoint. External systems call the URL "
        "(derived from the auto-generated `trigger_path`) to launch this "
        "workflow. Requires an API key in the `X-API-Key` header."
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
                "identifies this trigger. Do not edit manually."
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
