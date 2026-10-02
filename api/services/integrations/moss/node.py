from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from api.services.integrations.base import IntegrationNodeRegistration
from api.services.workflow.node_data import BaseNodeData
from api.services.workflow.node_specs._base import (
    GraphConstraints,
    NodeCategory,
    NodeExample,
    NumberInputOptions,
    PropertyOption,
    PropertyRendererOptions,
    PropertyType,
)
from api.services.workflow.node_specs.model_spec import (
    build_spec,
    node_spec,
    spec_field,
)


@node_spec(
    name="moss",
    display_name="Moss",
    description="Let the agent search a Moss index during the call",
    llm_hint=(
        "Moss is a knowledge retrieval configuration node. It does not participate "
        "in the conversation graph and should not be connected to other nodes. When "
        "enabled, Start Call and Agent nodes search the configured Moss index: in "
        "ambient mode on every caller turn before the LLM answers, in tool mode "
        "through a search_moss_index tool the LLM calls."
    ),
    docs_url="https://docs.dograh.com/integrations/moss",
    category=NodeCategory.integration,
    icon="Search",
    examples=[
        NodeExample(
            name="moss_support_kb",
            data={
                "name": "Support articles",
                "moss_enabled": True,
                "moss_mode": "ambient",
                "moss_index_name": "support-kb",
                "moss_project_id": "your-project-id",
                "moss_project_key": "moss_xxxxxxxx",
                "moss_index_description": "Return policy, shipping times and warranty terms.",
            },
        )
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0, max_instances=1
    ),
    property_order=(
        "name",
        "moss_enabled",
        "moss_mode",
        "moss_index_name",
        "moss_project_id",
        "moss_project_key",
        "moss_index_description",
        "moss_top_k",
        "moss_alpha",
    ),
    field_overrides={
        "name": {
            "spec_default": "Moss",
            "description": "Short identifier for this Moss index configuration.",
        },
        "moss_enabled": {
            "display_name": "Enabled",
            "description": "When false, agents do not search the Moss index.",
        },
        "moss_index_name": {
            "display_name": "Index Name",
            "description": "Name of the Moss index the agent searches.",
        },
        "moss_project_id": {
            "display_name": "Project ID",
            "description": "Moss project that owns the index.",
        },
        "moss_project_key": {
            "display_name": "Project Key",
            "description": "Moss project key used to download the index.",
        },
    },
)
class MossNodeData(BaseNodeData):
    moss_enabled: bool = spec_field(
        default=True,
        ui_type=PropertyType.boolean,
        display_name="Enabled",
        description="When false, agents do not search the Moss index.",
    )
    moss_mode: Literal["ambient", "tool"] = spec_field(
        default="ambient",
        ui_type=PropertyType.options,
        display_name="Mode",
        description="When the agent searches the index.",
        options=[
            PropertyOption(
                value="ambient",
                label="Ambient",
                description=(
                    "Search on every caller turn and give the results to the LLM "
                    "with the turn. No extra LLM round trip."
                ),
            ),
            PropertyOption(
                value="tool",
                label="Tool",
                description=(
                    "Give the LLM a search tool to call when it decides it needs one."
                ),
            ),
        ],
    )
    moss_index_name: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Index Name",
        description="Name of the Moss index the agent searches.",
    )
    moss_project_id: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Project ID",
        description="Moss project that owns the index.",
    )
    moss_project_key: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Project Key",
        description="Moss project key used to download the index.",
    )
    moss_index_description: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Index Contents",
        description=(
            "What the index contains. The agent reads this to decide when to call "
            "the search tool, in tool mode and on speech to speech calls."
        ),
        editor="textarea",
    )
    moss_top_k: int = spec_field(
        default=3,
        ge=1,
        le=20,
        ui_type=PropertyType.number,
        display_name="Results per Search",
        description="How many documents each search returns to the agent.",
    )
    moss_alpha: float = spec_field(
        default=0.8,
        ge=0.0,
        le=1.0,
        ui_type=PropertyType.number,
        display_name="Semantic Weight",
        description=(
            "Blend of semantic and keyword matching: 1.0 is semantic only, "
            "0.0 is keyword only."
        ),
        renderer_options=PropertyRendererOptions(
            number_input=NumberInputOptions(fractional=True)
        ),
    )

    @model_validator(mode="after")
    def _validate_enabled_config(self):
        if not self.moss_enabled:
            return self

        missing = [
            field
            for field in ("moss_index_name", "moss_project_id", "moss_project_key")
            if not (getattr(self, field) or "").strip()
        ]
        if missing:
            fields = ", ".join(missing)
            raise ValueError(
                f"Moss node is enabled but missing required fields: {fields}"
            )

        return self


SPEC = build_spec(MossNodeData)


NODE = IntegrationNodeRegistration(
    type_name="moss",
    data_model=MossNodeData,
    node_spec=SPEC,
    sensitive_fields=("moss_project_key",),
)
