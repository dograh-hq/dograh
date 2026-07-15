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
    name="paygent",
    display_name="Paygent",
    description="Cost Tracking and Billing",
    llm_hint=(
        "Paygent is a post-call usage-tracking and billing integration. "
        "It does not participate in the conversation graph and should not be connected to other nodes."
    ),
    docs_url="https://docs.dograh.com/integrations/paygent",
    category=NodeCategory.integration,
    icon="CreditCard",
    examples=[
        NodeExample(
            name="paygent_tracking",
            data={
                "name": "Paygent Tracking",
                "paygent_enabled": True,
                "paygent_api_key": "pg_live_xxxxxxxxxxxxxxxx",
                "paygent_agent_id": "my-voice-agent-prod",
                "paygent_customer_id": "org-123",
                "paygent_indicator": "per-minute-call",
            },
        )
    ],
    graph_constraints=GraphConstraints(
        min_incoming=0, max_incoming=0, min_outgoing=0, max_outgoing=0, max_instances=1
    ),
    property_order=(
        "name",
        "paygent_enabled",
        "paygent_api_key",
        "paygent_agent_id",
        "paygent_customer_id",
        "paygent_indicator",
    ),
    field_overrides={
        "name": {
            "spec_default": "Paygent",
            "description": "Short identifier for this Paygent configuration.",
        },
        "paygent_enabled": {
            "display_name": "Enabled",
            "description": "When false, Dograh skips all Paygent tracking for this call.",
        },
        "paygent_api_key": {
            "display_name": "Paygent API Key",
            "description": "API key used to authenticate requests to the Paygent REST API.",
            "required": True,
        },
        "paygent_agent_id": {
            "display_name": "Agent ID",
            "description": "The agent identifier registered in your Paygent account.",
            "required": True,
        },
        "paygent_customer_id": {
            "display_name": "Customer ID",
            "description": "Your Paygent customer / organisation ID.",
            "required": True,
        },
        "paygent_indicator": {
            "display_name": "Indicator",
            "description": "The indicator event name sent at the end of the call (e.g. per-minute-call).",
            "required": True,
            "spec_default": "per-minute-call",
        },
    },
)
class PaygentNodeData(BaseNodeData):
    paygent_enabled: bool = spec_field(
        default=True,
        ui_type=PropertyType.boolean,
        display_name="Enabled",
        description="When false, Dograh skips all Paygent tracking for this call.",
    )
    paygent_api_key: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Paygent API Key",
        description="API key used to authenticate requests to the Paygent REST API.",
    )
    paygent_agent_id: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Agent ID",
        description="The agent identifier registered in your Paygent account.",
    )
    paygent_customer_id: str | None = spec_field(
        default=None,
        ui_type=PropertyType.string,
        display_name="Customer ID",
        description="Your Paygent customer / organisation ID.",
    )
    paygent_indicator: str = spec_field(
        default="per-minute-call",
        ui_type=PropertyType.string,
        display_name="Indicator",
        description="The indicator event name sent at the end of the call (e.g. per-minute-call).",
    )

    @model_validator(mode="after")
    def _validate_enabled_config(self) -> "PaygentNodeData":
        if not self.paygent_enabled:
            return self

        missing: list[str] = []
        if not self.paygent_api_key or not self.paygent_api_key.strip():
            missing.append("paygent_api_key")
        if not self.paygent_agent_id or not self.paygent_agent_id.strip():
            missing.append("paygent_agent_id")
        if not self.paygent_customer_id or not self.paygent_customer_id.strip():
            missing.append("paygent_customer_id")
        if not self.paygent_indicator or not self.paygent_indicator.strip():
            missing.append("paygent_indicator")

        if missing:
            fields = ", ".join(missing)
            raise ValueError(
                f"Paygent node is enabled but missing required fields: {fields}"
            )

        return self


SPEC = build_spec(PaygentNodeData)

NODE = IntegrationNodeRegistration(
    type_name="paygent",
    data_model=PaygentNodeData,
    node_spec=SPEC,
    sensitive_fields=("paygent_api_key",),
)
