from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from api.services.integrations.base import IntegrationNodeRegistration
from api.services.workflow.node_specs._base import (
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NodeSpec,
    PropertySpec,
    PropertyType,
)


class TunerNodeData(BaseModel):
    name: str = Field(..., min_length=1)
    is_start: bool = False
    is_end: bool = False
    tuner_enabled: bool = True
    tuner_agent_id: str | None = None
    tuner_workspace_id: int | None = Field(default=None, gt=0)
    tuner_api_key: str | None = None

    @model_validator(mode="after")
    def _validate_enabled_config(self):
        if not self.tuner_enabled:
            return self

        missing: list[str] = []
        if not self.tuner_agent_id or not self.tuner_agent_id.strip():
            missing.append("tuner_agent_id")
        if self.tuner_workspace_id is None:
            missing.append("tuner_workspace_id")
        if not self.tuner_api_key or not self.tuner_api_key.strip():
            missing.append("tuner_api_key")

        if missing:
            fields = ", ".join(missing)
            raise ValueError(f"Tuner node is enabled but missing required fields: {fields}")

        return self


SPEC = NodeSpec(
    name="tuner",
    display_name="Tuner",
    description="Export the completed call to Tuner for Agent Observability",
    llm_hint=(
        "Tuner is a post-call observability export. It does not participate in the "
        "conversation graph and should not be connected to other nodes."
    ),
    category=NodeCategory.integration,
    icon="Activity",
    properties=[
        PropertySpec(
            name="name",
            type=PropertyType.string,
            display_name="Name",
            description="Short identifier for this Tuner export configuration.",
            required=True,
            min_length=1,
            default="Tuner",
        ),
        PropertySpec(
            name="tuner_enabled",
            type=PropertyType.boolean,
            display_name="Enabled",
            description="When false, Dograh skips exporting this call to Tuner.",
            default=True,
        ),
        PropertySpec(
            name="tuner_agent_id",
            type=PropertyType.string,
            display_name="Tuner Agent ID",
            description="The agent identifier registered in your Tuner workspace.",
            required=True,
        ),
        PropertySpec(
            name="tuner_workspace_id",
            type=PropertyType.number,
            display_name="Tuner Workspace ID",
            description="Your numeric Tuner workspace ID.",
            required=True,
            min_value=1,
        ),
        PropertySpec(
            name="tuner_api_key",
            type=PropertyType.string,
            display_name="Tuner API Key",
            description="Bearer token used when posting completed calls to Tuner.",
            required=True,
        ),
    ],
    examples=[
        NodeExample(
            name="tuner_export",
            data={
                "name": "Primary Tuner Export",
                "tuner_enabled": True,
                "tuner_agent_id": "sales-bot-prod",
                "tuner_workspace_id": 42,
                "tuner_api_key": "tuner_live_xxxxxxxx",
            },
        ),
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0,
        max_incoming=0,
        min_outgoing=0,
        max_outgoing=0,
    ),
)


NODE = IntegrationNodeRegistration(
    type_name="tuner",
    data_model=TunerNodeData,
    node_spec=SPEC,
    sensitive_fields=("tuner_api_key",),
)
