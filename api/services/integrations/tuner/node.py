from __future__ import annotations

from pydantic import model_validator

from api.services.integrations.base import IntegrationNodeRegistration
from api.services.workflow.node_data import BaseNodeData
from api.services.workflow.node_specs._base import (
    DisplayOptions,
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NumberInputOptions,
    PropertyLayoutOptions,
    PropertyRendererOptions,
    PropertyType,
)
from api.services.workflow.node_specs.model_spec import (
    build_spec,
    node_spec,
    spec_field,
)

# Cost rate fields are only shown once the user turns on cost calculation.
_COST_FIELDS_VISIBLE = DisplayOptions(show={"cost_calculation_enabled": [True]})
_COST_RATE_RENDERER_OPTIONS = PropertyRendererOptions(
    layout=PropertyLayoutOptions(column_span=6),
    number_input=NumberInputOptions(fractional=True),
)


@node_spec(
    name="tuner",
    display_name="Tuner",
    description="Export the completed call to Tuner for Agent Observability",
    llm_hint=(
        "Tuner is a post-call observability export. It does not participate in the "
        "conversation graph and should not be connected to other nodes."
    ),
    category=NodeCategory.integration,
    icon="Activity",
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
        )
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0, max_instances=1
    ),
    property_order=(
        "name",
        "tuner_enabled",
        "tuner_agent_id",
        "tuner_workspace_id",
        "tuner_api_key",
        "cost_calculation_enabled",
        "cost_llm_input_rate",
        "cost_llm_cached_input_rate",
        "cost_llm_output_rate",
        "cost_tts_rate",
        "cost_stt_rate",
        "cost_telephony_rate",
    ),
    field_overrides={
        "name": {
            "spec_default": "Tuner",
            "description": "Short identifier for this Tuner export configuration.",
        },
        "tuner_enabled": {
            "display_name": "Enabled",
            "description": "When false, Dograh skips exporting this call to Tuner.",
        },
        "tuner_agent_id": {
            "display_name": "Tuner Agent ID",
            "description": "The agent identifier registered in your Tuner workspace.",
            "required": True,
        },
        "tuner_workspace_id": {
            "display_name": "Tuner Workspace ID",
            "description": "Your numeric Tuner workspace ID.",
            "required": True,
            "min_value": 1,
        },
        "tuner_api_key": {
            "display_name": "Tuner API Key",
            "description": "Bearer token used when posting completed calls to Tuner.",
            "required": True,
        },
    },
)
class TunerNodeData(BaseNodeData):
    tuner_enabled: bool = spec_field(
        default=True,
        ui_type=PropertyType.boolean,
        display_name="Enabled",
        description="When false, Dograh skips exporting this call to Tuner.",
    )
    tuner_agent_id: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Tuner Agent ID",
        description="The agent identifier registered in your Tuner workspace.",
    )
    tuner_workspace_id: int | None = spec_field(
        default=None,
        gt=0,
        ui_type=PropertyType.number,
        display_name="Tuner Workspace ID",
        description="Your numeric Tuner workspace ID.",
    )
    tuner_api_key: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Tuner API Key",
        description="Bearer token used when posting completed calls to Tuner.",
    )

    cost_calculation_enabled: bool = spec_field(
        default=False,
        ui_type=PropertyType.boolean,
        display_name="Calculate cost",
        description="Send a per-call cost to Tuner, computed from your own provider rates (BYOK). All rates below are optional.",
    )
    cost_llm_input_rate: float | None = spec_field(
        default=None,
        ge=0,
        le=1000,
        ui_type=PropertyType.number,
        display_name="LLM input",
        description="USD per 1M tokens",
        display_options=_COST_FIELDS_VISIBLE,
        renderer_options=_COST_RATE_RENDERER_OPTIONS,
    )
    cost_llm_cached_input_rate: float | None = spec_field(
        default=None,
        ge=0,
        le=1000,
        ui_type=PropertyType.number,
        display_name="LLM cached input",
        description="USD per 1M cached tokens",
        display_options=_COST_FIELDS_VISIBLE,
        renderer_options=_COST_RATE_RENDERER_OPTIONS,
    )
    cost_llm_output_rate: float | None = spec_field(
        default=None,
        ge=0,
        le=1000,
        ui_type=PropertyType.number,
        display_name="LLM output",
        description="USD per 1M tokens",
        display_options=_COST_FIELDS_VISIBLE,
        renderer_options=_COST_RATE_RENDERER_OPTIONS,
    )
    cost_tts_rate: float | None = spec_field(
        default=None,
        ge=0,
        le=100,
        ui_type=PropertyType.number,
        display_name="TTS",
        description="USD per 1K characters",
        display_options=_COST_FIELDS_VISIBLE,
        renderer_options=_COST_RATE_RENDERER_OPTIONS,
    )
    cost_stt_rate: float | None = spec_field(
        default=None,
        ge=0,
        le=100,
        ui_type=PropertyType.number,
        display_name="STT",
        description="USD per minute",
        display_options=_COST_FIELDS_VISIBLE,
        renderer_options=_COST_RATE_RENDERER_OPTIONS,
    )
    cost_telephony_rate: float | None = spec_field(
        default=None,
        ge=0,
        le=100,
        ui_type=PropertyType.number,
        display_name="Telephony",
        description="USD per minute",
        display_options=_COST_FIELDS_VISIBLE,
        renderer_options=_COST_RATE_RENDERER_OPTIONS,
    )

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
            raise ValueError(
                f"Tuner node is enabled but missing required fields: {fields}"
            )

        return self


SPEC = build_spec(TunerNodeData)


NODE = IntegrationNodeRegistration(
    type_name="tuner",
    data_model=TunerNodeData,
    node_spec=SPEC,
    sensitive_fields=("tuner_api_key",),
)
