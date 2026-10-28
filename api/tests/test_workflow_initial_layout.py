"""Creation persists a tidy canvas; later agent edits keep the user's layout."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from api.mcp_server.tools.create_workflow import create_workflow
from api.mcp_server.tools.save_workflow import save_workflow
from api.services.workflow.builder import save_builder_draft
from api.utils.workflow_revision import workflow_revision

CODE = """import { Workflow } from "@dograh/sdk";
import { startCall, agentNode, endCall } from "@dograh/sdk/typed";
const wf = new Workflow({ name: "Receptionist" });
const start = wf.addTyped(startCall({ name: "Welcome", prompt: "Hello." }));
const agent = wf.addTyped(agentNode({ name: "Intake", prompt: "Ask a question." }));
const end = wf.addTyped(endCall({ name: "Goodbye", prompt: "Bye." }));
wf.edge(start, agent, { label: "Continue", condition: "Caller is ready" });
wf.edge(agent, end, { label: "Done", condition: "Intake is complete" });
"""


@pytest.mark.parametrize("source", ["mcp", "builder"])
async def test_backend_tidy_on_creation_preserves_positions_on_later_edits(
    source, monkeypatch
):
    user = SimpleNamespace(id=3, provider_id="test", selected_organization_id=7)
    session_id = uuid4()
    state = SimpleNamespace(
        checkpoint="ready", can_continue=False, proposal=SimpleNamespace(code=CODE)
    )
    workflow = SimpleNamespace(id=41, name="Receptionist", status="published")
    create = AsyncMock(return_value=workflow)
    monkeypatch.setattr("api.db.db_client.create_workflow", create)
    monkeypatch.setattr("api.db.db_client.create_builder_draft", create)
    monkeypatch.setattr("api.mcp_server.tools.create_workflow.capture_event", Mock())
    for module in (
        "api.mcp_server.tools.create_workflow",
        "api.mcp_server.tools.save_workflow",
    ):
        monkeypatch.setattr(
            f"{module}.authenticate_mcp_request", AsyncMock(return_value=user)
        )
        monkeypatch.setattr(
            f"{module}.validate_workflow_tool_name_collisions",
            AsyncMock(return_value=[]),
        )
    monkeypatch.setattr(
        "api.services.workflow.builder_validation.validate_workflow_tool_name_collisions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "api.services.workflow.builder.load_session", AsyncMock(return_value=state)
    )

    if source == "mcp":
        assert (await create_workflow(CODE))["created"]
        initial = create.call_args.args[1]
    else:
        assert await save_builder_draft(user, session_id, "ready") == 41
        initial = create.call_args.kwargs["workflow_definition"]

    # The UI's top-to-bottom layout, including the staggered conversation step,
    # is already stored before a browser opens the new workflow.
    assert [node["position"] for node in initial["nodes"]] == [
        {"x": 175, "y": 60},
        {"x": 775, "y": 480},
        {"x": 175, "y": 900},
    ]
    create.assert_awaited_once()

    manual = deepcopy(initial)
    positions = [{"x": -900, "y": 250}, {"x": 50, "y": -400}, {"x": 0, "y": 0}]
    for node, position in zip(manual["nodes"], positions, strict=True):
        node["position"] = position
    workflow.workflow_definition = manual
    state.workflow_id = workflow.id
    state.workflow_revision = workflow_revision(manual)
    state.proposal.code = CODE.replace("Ask a question.", "Ask for a name.")
    save = AsyncMock(return_value=SimpleNamespace(version_number=2, status="draft"))
    monkeypatch.setattr("api.db.db_client.save_workflow_draft", save)
    monkeypatch.setattr(
        "api.db.db_client.get_workflow", AsyncMock(return_value=workflow)
    )
    monkeypatch.setattr(
        "api.services.workflow.builder.load_builder_workflow",
        AsyncMock(return_value=workflow),
    )
    monkeypatch.setattr(
        "api.mcp_server.tools.save_workflow._previous_workflow_json",
        AsyncMock(return_value=manual),
    )

    if source == "mcp":
        assert (await save_workflow(workflow.id, state.proposal.code))["saved"]
    else:
        assert await save_builder_draft(user, session_id, "ready", workflow.id) == 41

    updated = save.call_args.kwargs["workflow_definition"]
    assert [node["position"] for node in updated["nodes"]] == positions
    assert updated["nodes"][1]["data"]["prompt"] == "Ask for a name."
    create.assert_awaited_once()
