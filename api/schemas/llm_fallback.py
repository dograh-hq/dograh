"""Versioned fallback policy shared by saved references and resolved services."""

from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FallbackSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class NoOutputCondition(FallbackSchema):
    type: Literal["no_output"] = "no_output"
    after_ms: int = Field(default=1500, ge=100, le=10000)


class ErrorCondition(FallbackSchema):
    type: Literal["error"] = "error"


FallbackCondition = Annotated[
    NoOutputCondition | ErrorCondition, Field(discriminator="type")
]
Target = TypeVar("Target")


class FallbackRule(FallbackSchema, Generic[Target]):
    condition: FallbackCondition
    target: Target


class FallbackPolicy(FallbackSchema, Generic[Target]):
    version: Literal[1] = 1
    rules: list[FallbackRule[Target]] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def unique_conditions(self):
        types = [rule.condition.type for rule in self.rules]
        if len(types) != len(set(types)):
            raise ValueError("Only one fallback rule per condition is supported")
        return self
