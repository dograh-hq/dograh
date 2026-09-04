from typing import Literal

from pydantic import BaseModel, Field


SCENARIO_FIELDS = (
    "title",
    "mode",
    "persona",
    "age",
    "gender",
    "language",
    "emotion",
    "communication_style",
    "initial_information",
    "hidden_information",
    "disclosure",
    "behaviour",
    "background",
    "additional_factors",
    "notes",
    "freestyle_prompt",
)


class ScenarioWriteRequest(BaseModel):
    title: str = Field(default="Untitled scenario", max_length=500)
    mode: Literal["structured", "freestyle"] = "structured"
    persona: str = Field(default="", max_length=20_000)
    age: str = Field(default="", max_length=200)
    gender: str = Field(default="", max_length=200)
    language: str = Field(default="", max_length=200)
    emotion: str = Field(default="", max_length=500)
    communication_style: str = Field(default="", max_length=20_000)
    initial_information: str = Field(default="", max_length=20_000)
    hidden_information: str = Field(default="", max_length=20_000)
    disclosure: str = Field(default="", max_length=20_000)
    behaviour: str = Field(default="", max_length=20_000)
    background: str = Field(default="", max_length=20_000)
    additional_factors: str = Field(default="", max_length=20_000)
    notes: str = Field(default="", max_length=20_000)
    freestyle_prompt: str = Field(default="", max_length=20_000)
