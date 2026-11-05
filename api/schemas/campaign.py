from pydantic import BaseModel, Field, model_validator


class TrafficVariantRequest(BaseModel):
    workflow_id: int = Field(gt=0, strict=True)
    workflow_definition_id: int | None = Field(default=None, gt=0, strict=True)
    weight: int = Field(ge=1, le=100, strict=True)


class TrafficSplitRequest(BaseModel):
    variants: list[TrafficVariantRequest] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def validate_variants(self):
        if sum(v.weight for v in self.variants) != 100:
            raise ValueError("Traffic split weights must add up to 100")
        keys = [(v.workflow_id, v.workflow_definition_id) for v in self.variants]
        if len(set(keys)) != len(keys):
            raise ValueError("Each agent/version pair must be unique")
        return self


class TrafficVariantResponse(TrafficVariantRequest):
    id: str
    workflow_name: str
    version_number: int | None = None


class TrafficSplitResponse(BaseModel):
    revision: int
    variants: list[TrafficVariantResponse]


class TrafficDefinitionStats(BaseModel):
    definition_id: int | None
    version_number: int | None
    attempts: int


class TrafficVariantStats(BaseModel):
    id: str
    workflow_id: int
    workflow_name: str
    workflow_definition_id: int | None
    version_number: int | None
    target_weight: int | None
    attempts: int = 0
    completed: int = 0
    actual_percentage: float = 0
    states: dict[str, int] = Field(default_factory=dict)
    outcomes: dict[str, int] = Field(default_factory=dict)
    definitions: list[TrafficDefinitionStats] = Field(default_factory=list)


class CampaignTrafficStatsResponse(BaseModel):
    total_attempts: int
    variants: list[TrafficVariantStats]
