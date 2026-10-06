"""Shared stage guidance; interface policy and runtime orchestration stay separate."""

from pathlib import Path

from .contracts import AuthoringStage, BuildResult, CallBrief, ReviewResult

_ASSETS = Path(__file__).parent


def render_authoring_instructions(interface_instructions: str) -> str:
    """Small MCP bootstrap. Stage guidance and syntax are retrieved on demand."""
    return "\n\n".join(
        [
            (_ASSETS / "procedure.md").read_text(encoding="utf-8").strip(),
            interface_instructions.strip(),
        ]
    )


def authoring_stage(stage: AuthoringStage | str) -> dict:
    """Return the same instructions and handoff schema through either interface."""
    stage = AuthoringStage(stage)
    parts = [
        (_ASSETS / "stages" / f"{stage.value}.md").read_text(encoding="utf-8").strip(),
        (_ASSETS / "common.md").read_text(encoding="utf-8").strip(),
    ]
    if stage == AuthoringStage.build:
        parts.append((_ASSETS / "source.md").read_text(encoding="utf-8").strip())
    contract = {
        AuthoringStage.plan: CallBrief,
        AuthoringStage.build: BuildResult,
        AuthoringStage.review: ReviewResult,
    }[stage]
    return {
        "stage": stage.value,
        "instructions": "\n\n".join(parts),
        "output_schema": contract.model_json_schema(),
    }
