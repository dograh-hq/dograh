from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.campaign.sources.csv import CSVSyncService
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


def test_campaign_csv_text_override_is_rendered_with_row_context():
    headers = [
        "phone_number",
        "first_name",
        "greeting_override_type",
        "greeting_override_text",
    ]
    context = CSVSyncService._build_context_variables(
        headers,
        ["+14155550100", "Jane", "text", "Hello {{first_name}}"],
    )

    engine = PipecatEngine(workflow=_workflow(), call_context_vars=context)

    assert context["greeting_override"] == {
        "type": "text",
        "text": "Hello {{first_name}}",
    }
    assert engine.get_start_greeting() == ("text", "Hello Jane")


def test_campaign_csv_audio_override_uses_recording_id():
    context = CSVSyncService._build_context_variables(
        [
            "phone_number",
            "greeting_override_type",
            "greeting_override_recording_id",
        ],
        ["+14155550100", "audio", "campaign-welcome"],
    )

    assert context["greeting_override"] == {
        "type": "audio",
        "recording_id": "campaign-welcome",
    }


@pytest.mark.asyncio
async def test_campaign_sync_stores_nested_greeting_override():
    service = CSVSyncService()
    service._fetch_csv_data = AsyncMock(
        return_value=[
            [
                "phone_number",
                "first_name",
                "greeting_override_type",
                "greeting_override_text",
            ],
            ["+14155550100", "Jane", "text", "Hello {{first_name}}"],
        ]
    )
    campaign = MagicMock(source_id="campaigns/contacts.csv")

    with patch("api.services.campaign.sources.csv.db_client") as db:
        db.get_campaign_by_id = AsyncMock(return_value=campaign)
        db.bulk_create_queued_runs = AsyncMock()
        db.update_campaign = AsyncMock()

        assert await service.sync_source_data(campaign_id=42) == 1

        queued_runs = db.bulk_create_queued_runs.await_args.args[0]
        assert queued_runs[0]["context_variables"] == {
            "phone_number": "+14155550100",
            "first_name": "Jane",
            "greeting_override": {
                "type": "text",
                "text": "Hello {{first_name}}",
            },
        }
