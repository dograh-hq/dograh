from __future__ import annotations

from pydantic import BaseModel

from api.services.workflow.node_specs._base import PropertyType
from api.services.workflow.node_specs.model_spec import spec_field


class BaseNodeData(BaseModel):
    name: str = spec_field(
        ...,
        min_length=1,
        ui_type=PropertyType.string,
        display_name="Name",
        description="Short identifier shown in the canvas and call logs.",
        required=True,
    )
    is_start: bool = spec_field(default=False, spec_exclude=True)
    is_end: bool = spec_field(default=False, spec_exclude=True)
