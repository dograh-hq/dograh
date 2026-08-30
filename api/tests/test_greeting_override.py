from api.services.workflow.dto import (
    EdgeDataDTO,
    EndCallNodeData,
    Position,
    ReactFlowDTO,
    RFEdgeDTO,
    RFNodeDTO,
    StartCallNodeData,
)
from api.services.workflow.pipecat_engine import PipecatEngine
from api.services.workflow.workflow_graph import WorkflowGraph


def _workflow() -> WorkflowGraph:
    return WorkflowGraph(
        ReactFlowDTO(
            nodes=[
                RFNodeDTO(
                    id="start",
                    type="startCall",
                    position=Position(x=0, y=0),
                    data=StartCallNodeData(
                        name="Start",
                        prompt="Open the call",
                        greeting_type="text",
                        greeting="Saved greeting",
                    ),
                ),
                RFNodeDTO(
                    id="end",
                    type="endCall",
                    position=Position(x=0, y=100),
                    data=EndCallNodeData(name="End", prompt="End the call"),
                ),
            ],
            edges=[
                RFEdgeDTO(
                    id="edge",
                    source="start",
                    target="end",
                    data=EdgeDataDTO(label="End", condition="The call is complete"),
                )
            ],
        )
    )


def test_text_override_wins_and_renders_hydrated_context():
    workflow = _workflow()
    engine = PipecatEngine(
        workflow=workflow,
        call_context_vars={
            "account_id": "ACC-123",
            "greeting_override": {
                "type": "text",
                "text": "Please confirm account {{account_id}}.",
            },
        },
    )

    assert engine.get_start_greeting() == (
        "text",
        "Please confirm account ACC-123.",
    )


def test_audio_override_uses_public_recording_id():
    workflow = _workflow()
    engine = PipecatEngine(
        workflow=workflow,
        call_context_vars={
            "greeting_override": {
                "type": "audio",
                "recording_id": "callback-welcome",
            }
        },
    )

    assert engine.get_start_greeting() == (
        "audio_recording_id",
        "callback-welcome",
    )


def test_invalid_override_falls_back_to_saved_greeting():
    workflow = _workflow()
    engine = PipecatEngine(
        workflow=workflow,
        call_context_vars={"greeting_override": {"type": "text"}},
    )

    assert engine.get_start_greeting() == ("text", "Saved greeting")
