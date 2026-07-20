from __future__ import annotations

from pydantic import model_validator

from api.services.integrations.base import IntegrationNodeRegistration
from api.services.workflow.node_data import BaseNodeData
from api.services.workflow.node_specs._base import (
    GraphConstraints,
    NodeCategory,
    NodeExample,
    PropertyType,
)
from api.services.workflow.node_specs.model_spec import (
    build_spec,
    node_spec,
    spec_field,
)


@node_spec(
    name="noveum",
    display_name="Noveum",
    description="Export the completed call to Noveum for tracing and evaluation",
    llm_hint=(
        "Noveum is a post-call observability export. It does not participate in the "
        "conversation graph and should not be connected to other nodes."
    ),
    category=NodeCategory.integration,
    icon="Activity",
    examples=[
        NodeExample(
            name="noveum_export",
            data={
                "name": "Primary Noveum Export",
                "noveum_enabled": True,
                "noveum_api_key": "noveum_live_xxxxxxxx",
                "noveum_project": "my-voice-agent",
            },
        )
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0, max_instances=1
    ),
    property_order=(
        "name",
        "noveum_enabled",
        "noveum_api_key",
        "noveum_project",
        "noveum_environment",
        "noveum_endpoint",
        "noveum_record_audio",
    ),
    field_overrides={
        "name": {
            "spec_default": "Noveum",
            "description": "Short identifier for this Noveum export configuration.",
        },
        "noveum_enabled": {
            "display_name": "Enabled",
            "description": "When false, Dograh skips exporting this call to Noveum.",
        },
        "noveum_api_key": {
            "display_name": "Noveum API Key",
            "description": "Bearer token used when exporting the completed call to Noveum.",
            "required": True,
        },
        "noveum_project": {
            "display_name": "Noveum Project",
            "description": "The Noveum project the call trace is exported into.",
            "required": True,
        },
    },
)
class NoveumNodeData(BaseNodeData):
    noveum_enabled: bool = spec_field(
        default=True,
        ui_type=PropertyType.boolean,
        display_name="Enabled",
        description="When false, Dograh skips exporting this call to Noveum.",
    )
    noveum_api_key: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Noveum API Key",
        description="Bearer token used when exporting the completed call to Noveum.",
    )
    noveum_project: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Noveum Project",
        description="The Noveum project the call trace is exported into.",
    )
    noveum_environment: str = spec_field(
        default="production",
        ui_type=PropertyType.string,
        display_name="Noveum Environment",
        description="Environment label stamped on exported traces (e.g. production, staging).",
    )
    noveum_endpoint: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Noveum Endpoint",
        description="Optional Noveum API endpoint override. Leave empty for the default (https://api.noveum.ai/api).",
    )
    noveum_record_audio: bool = spec_field(
        default=True,
        ui_type=PropertyType.boolean,
        display_name="Record audio",
        description="Capture per-segment STT/TTS audio and the full-conversation recording for audio evaluation on Noveum.",
    )

    @model_validator(mode="after")
    def _validate_enabled_config(self):
        if not self.noveum_enabled:
            return self

        missing: list[str] = []
        if not self.noveum_api_key or not self.noveum_api_key.strip():
            missing.append("noveum_api_key")
        if not self.noveum_project or not self.noveum_project.strip():
            missing.append("noveum_project")

        if missing:
            fields = ", ".join(missing)
            raise ValueError(
                f"Noveum node is enabled but missing required fields: {fields}"
            )

        return self


SPEC = build_spec(NoveumNodeData)


NODE = IntegrationNodeRegistration(
    type_name="noveum",
    data_model=NoveumNodeData,
    node_spec=SPEC,
    sensitive_fields=("noveum_api_key",),
)
