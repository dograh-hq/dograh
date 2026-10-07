from __future__ import annotations

from uuid import UUID

from pydantic import field_validator, model_validator

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
    name="roark",
    display_name="Roark",
    description="Send the completed call to Roark for transcription, scoring and analytics",
    llm_hint=(
        "Roark is a post-call analytics export. It does not participate in the "
        "conversation graph and should not be connected to other nodes. The call "
        "recording must be reachable from the public internet for Roark to ingest it."
    ),
    docs_url="https://docs.dograh.com/integrations/roark",
    category=NodeCategory.integration,
    icon="AudioLines",
    examples=[
        NodeExample(
            name="roark_export",
            data={
                "name": "Roark",
                "roark_enabled": True,
                "roark_api_key": "rk_live_xxxxxxxx",
                "roark_agent_name": "Sales Bot",
            },
        )
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0, max_instances=1
    ),
    property_order=(
        "name",
        "roark_enabled",
        "roark_api_key",
        "roark_agent_name",
        "roark_agent_id",
        "roark_send_transcript",
        "roark_send_gathered_context",
    ),
    field_overrides={
        "name": {
            "spec_default": "Roark",
            "description": "Short identifier for this Roark export configuration.",
        },
        "roark_enabled": {
            "display_name": "Enabled",
            "description": "When false, Dograh skips exporting this call to Roark.",
        },
        "roark_api_key": {
            "display_name": "Roark API Key",
            "description": "Project API key used to post completed calls to Roark.",
            "required": True,
        },
    },
)
class RoarkNodeData(BaseNodeData):
    roark_enabled: bool = spec_field(
        default=True,
        ui_type=PropertyType.boolean,
        display_name="Enabled",
        description="When false, Dograh skips exporting this call to Roark.",
    )
    roark_api_key: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Roark API Key",
        description="Project API key used to post completed calls to Roark.",
    )
    roark_agent_name: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Roark Agent Name",
        description=(
            "Name of the agent in Roark. An agent with this exact name in the "
            "project is reused; otherwise Roark creates one."
        ),
    )
    roark_agent_id: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Roark Agent ID",
        description=(
            "Optional. UUID of an existing Roark agent. Takes precedence over "
            "the agent name when both are set."
        ),
    )
    roark_send_transcript: bool = spec_field(
        default=True,
        ui_type=PropertyType.boolean,
        display_name="Send Dograh transcript",
        description=(
            "Send the transcript and tool calls Dograh captured during the "
            "call. Turn this off to have Roark transcribe the recording "
            "itself, for example to measure your own speech-to-text against "
            "Roark's. Roark then has no record of the tool calls either."
        ),
    )
    roark_send_gathered_context: bool = spec_field(
        default=False,
        ui_type=PropertyType.boolean,
        display_name="Send gathered context",
        description=(
            "Also send the variables the agent gathered during the call as Roark "
            "call properties, so you can filter on them. Off by default because "
            "gathered context often holds personal data."
        ),
    )

    @field_validator("roark_agent_id")
    @classmethod
    def _agent_id_must_be_a_uuid(cls, value: str | None) -> str | None:
        """Reject a non-UUID agent id while the node is still being saved.

        Roark's `agent.roarkId` is a UUID, so a value of any other shape is
        refused with a 400 once the call is already over and there is nothing
        left to retry. Checked here instead, where the workflow save surfaces
        it on the field the user is editing.

        The value is also normalised to the canonical dashed form, because
        Roark matches on that spelling and a bare 32-character hex id is a
        valid UUID that it would still reject.
        """
        if value is None:
            return value
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return str(UUID(trimmed))
        except ValueError:
            raise ValueError("must be a UUID, as issued by Roark") from None

    @model_validator(mode="after")
    def _validate_enabled_config(self):
        if not self.roark_enabled:
            return self

        missing: list[str] = []
        if not self.roark_api_key or not self.roark_api_key.strip():
            missing.append("roark_api_key")

        has_name = bool(self.roark_agent_name and self.roark_agent_name.strip())
        has_id = bool(self.roark_agent_id and self.roark_agent_id.strip())
        if not has_name and not has_id:
            missing.append("roark_agent_name or roark_agent_id")

        if missing:
            fields = ", ".join(missing)
            raise ValueError(
                f"Roark node is enabled but missing required fields: {fields}"
            )

        return self


SPEC = build_spec(RoarkNodeData)


NODE = IntegrationNodeRegistration(
    type_name="roark",
    data_model=RoarkNodeData,
    node_spec=SPEC,
    sensitive_fields=("roark_api_key",),
)
