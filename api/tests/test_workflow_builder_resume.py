"""Continue a saved builder session without replacing newer or published work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from fastapi import HTTPException
from langchain_core.messages import AIMessage

from api.db.models import CheckpointModel, OrganizationModel, UserModel
from api.schemas.workflow_builder import BuilderSession
from api.services.workflow.builder import (
    load_builder_workflow,
    open_workflow_builder,
    save_builder_draft,
)
from api.tests.test_workflow_builder_stages import Conversation
from api.utils.workflow_revision import workflow_revision


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner,organization,session_matches", [(4, 7, True), (3, 8, True), (3, 7, False)]
)
async def test_resume_rejects_another_users_orgs_or_sessions_workflow(
    owner, organization, session_matches
):
    session = uuid4()
    user = SimpleNamespace(id=3, selected_organization_id=7)
    workflow = SimpleNamespace(user_id=owner)
    with (
        patch(
            "api.services.workflow.builder.db_client.get_workflow",
            AsyncMock(return_value=workflow if organization == 7 else None),
        ) as get,
        patch(
            "api.services.workflow.builder.db_client.get_workflow_builder_session_id",
            AsyncMock(
                return_value=str(session)
                if owner == 3 and session_matches
                else str(uuid4())
            ),
        ),
    ):
        with pytest.raises(HTTPException) as error:
            await load_builder_workflow(user, session, 41)
    assert error.value.status_code == 404
    get.assert_awaited_once_with(41, organization_id=7)


@pytest.mark.asyncio
async def test_save_continued_session_updates_its_existing_draft():
    session = uuid4()
    user = SimpleNamespace(id=3, selected_organization_id=7)
    previous = {"nodes": [], "edges": []}
    state = BuilderSession(
        id=session,
        checkpoint="new",
        workflow_id=41,
        workflow_revision=workflow_revision(previous),
        proposal={
            "name": "Agent",
            "code": "source",
            "summary": "Updated",
            "workflow": previous,
        },
    )
    with (
        patch(
            "api.services.workflow.builder.load_session", AsyncMock(return_value=state)
        ),
        patch(
            "api.services.workflow.builder.load_builder_workflow",
            AsyncMock(return_value=SimpleNamespace(workflow_definition=previous)),
        ),
        patch(
            "api.services.workflow.builder.preview_workflow_code",
            AsyncMock(return_value={"valid": True, "workflow": previous}),
        ) as preview,
        patch(
            "api.services.workflow.builder.db_client.save_workflow_draft", AsyncMock()
        ) as save,
        patch(
            "api.services.workflow.builder.db_client.create_builder_draft", AsyncMock()
        ) as create,
    ):
        assert await save_builder_draft(user, session, "new", 41) == 41
        preview.assert_awaited_once_with("source", 7, tidy=False)
        save.assert_awaited_once_with(
            41,
            workflow_definition=previous,
            expected_workflow_revision=state.workflow_revision,
        )
        create.assert_not_called()
        state.workflow_id = 42
        with pytest.raises(HTTPException) as error:
            await save_builder_draft(user, session, "new", 41)
        assert error.value.status_code == 409
        assert save.await_count == 1


@pytest.mark.asyncio
async def test_resume_keeps_history_and_uses_current_canvas_without_resetting_unsaved_iterations():
    conversation = Conversation(
        [
            AIMessage(content="Original conversation"),
            AIMessage(content="First change"),
            AIMessage(content="Second change"),
            AIMessage(content="Canvas changed"),
        ]
    )
    await conversation.step(message="Build a receptionist")
    context = {"id": 41, "code": "latest saved canvas", "revision": "v1"}
    result = await conversation.step(
        message="Change the greeting", current_workflow=context
    )
    state = await conversation.state()
    assert state["previous_code"] == context["code"]
    assert result["workflow_id"] == 41
    assert result["workflow_revision"] == "v1"
    assert [m["content"] for m in result["messages"]][:2] == [
        "Build a receptionist",
        "Original conversation",
    ]
    assert all(context["code"] not in m["content"] for m in result["messages"])
    assert context["code"] in conversation.model.seen[-1][-2].content

    # A subsequent request before saving must refine the last candidate.
    await conversation.graph.aupdate_state(
        conversation.config, {"candidate": {"code": "unsaved first change"}}
    )
    conversation.current["checkpoint"] = (
        await conversation.graph.aget_state(conversation.config)
    ).config["configurable"]["checkpoint_id"]
    await conversation.step(message="Also change the ending", current_workflow=context)
    assert (await conversation.state())["candidate"]["code"] == "unsaved first change"
    await conversation.step(
        message="Keep my canvas edits",
        current_workflow={
            **context,
            "revision": "v2",
            "code": "manually edited canvas",
        },
    )
    state = await conversation.state()
    assert state["candidate"] is None
    assert state["previous_code"] == "manually edited canvas"


@pytest.mark.asyncio
async def test_builder_updates_preserve_settings_and_release_and_reject_stale_proposals(
    db_session, async_session
):
    org = OrganizationModel(provider_id=f"builder-resume-{uuid4()}")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id=f"builder-resume-{uuid4()}", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()
    session = str(uuid4())
    original = {"nodes": [], "edges": []}
    workflow = await db_session.create_builder_draft(
        workflow_uuid=str(uuid4()),
        name="Agent",
        workflow_definition=original,
        user_id=user.id,
        organization_id=org.id,
        builder_session_id=session,
    )
    assert (await db_session.get_draft_version(workflow.id)).extra_metadata == {
        "builder_sessions": {str(user.id): session}
    }
    assert (
        await db_session.get_workflow_builder_session_id(
            workflow.id, organization_id=org.id, user_id=user.id
        )
        == session
    )
    await db_session.save_workflow_draft(
        workflow.id, template_context_variables={"name": "Ada"}
    )
    published = await db_session.publish_workflow_draft(workflow.id)
    changed = {"nodes": [], "edges": [], "updated": True}
    draft = await db_session.save_workflow_draft(
        workflow.id,
        workflow_definition=changed,
        expected_workflow_revision=workflow_revision(original),
    )
    assert draft.status == "draft"
    assert draft.version_number == 2
    assert draft.template_context_variables == {"name": "Ada"}
    stored = await db_session.get_workflow(workflow.id, organization_id=org.id)
    assert stored.released_definition.id == published.id
    assert stored.released_definition.workflow_json == original
    # Retrying an already applied save is safe.
    await db_session.save_workflow_draft(
        workflow.id,
        workflow_definition=changed,
        expected_workflow_revision=workflow_revision(original),
    )
    with pytest.raises(ValueError, match="workflow changed"):
        await db_session.save_workflow_draft(
            workflow.id,
            workflow_definition={"nodes": [], "edges": [], "stale": True},
            expected_workflow_revision=workflow_revision(original),
        )
    assert (await db_session.get_draft_version(workflow.id)).workflow_json == changed

    assert (
        await db_session.get_workflow_builder_session_id(
            workflow.id, organization_id=org.id, user_id=user.id
        )
        == session
    )
    assert (
        await db_session.get_workflow_builder_session_id(
            workflow.id, organization_id=org.id, user_id=user.id + 1
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_builder", [False, True])
async def test_open_existing_workflow_retains_session_in_json_and_preserves_metadata(
    db_session, async_session, legacy_builder
):
    org = OrganizationModel(provider_id=f"builder-json-{uuid4()}")
    async_session.add(org)
    await async_session.flush()
    user = UserModel(
        provider_id=f"builder-json-{uuid4()}", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()
    old_session = uuid4()
    workflow_uuid = (
        str(uuid5(NAMESPACE_URL, f"dograh-builder:{org.id}:{user.id}:{old_session}"))
        if legacy_builder
        else str(uuid4())
    )
    workflow = await db_session.create_builder_draft(
        workflow_uuid=workflow_uuid,
        name="Existing agent",
        workflow_definition={"nodes": [], "edges": []},
        user_id=user.id,
        organization_id=org.id,
    )
    draft = await db_session.get_draft_version(workflow.id)
    draft.extra_metadata = {"version_name": "Keep this release note"}
    if legacy_builder:
        async_session.add(
            CheckpointModel(
                thread_id=f"workflow-builder:{org.id}:{user.id}:{old_session}",
                checkpoint_ns="",
                checkpoint_id="old",
                checkpoint={},
            )
        )
    await async_session.flush()
    first = await db_session.get_workflow_builder_session_id(
        workflow.id, organization_id=org.id, user_id=user.id, create=True
    )
    assert first
    if legacy_builder:
        assert first == str(old_session)
    assert (
        await db_session.get_workflow_builder_session_id(
            workflow.id, organization_id=org.id, user_id=user.id, create=True
        )
        == first
    )
    assert draft.extra_metadata == {
        "version_name": "Keep this release note",
        "builder_sessions": {str(user.id): first},
    }
    assert (
        await db_session.get_workflow_builder_session_id(
            workflow.id, organization_id=org.id + 1, user_id=user.id, create=True
        )
        is None
    )
    # Teammates can build from the same workflow in their own conversation.
    teammate_session = await db_session.get_workflow_builder_session_id(
        workflow.id, organization_id=org.id, user_id=user.id + 1, create=True
    )
    assert teammate_session != first
    assert draft.extra_metadata["builder_sessions"] == {
        str(user.id): first,
        str(user.id + 1): teammate_session,
    }


@pytest.mark.asyncio
async def test_open_workflow_without_checkpoint_returns_empty_persistent_session():
    session = uuid4()
    user = SimpleNamespace(id=3, selected_organization_id=7)
    with (
        patch(
            "api.services.workflow.builder.db_client.get_workflow_builder_session_id",
            AsyncMock(return_value=str(session)),
        ) as get,
        patch(
            "api.services.workflow.builder.load_session",
            AsyncMock(side_effect=HTTPException(404, "Builder session not found")),
        ),
    ):
        first = await open_workflow_builder(user, 41)
        second = await open_workflow_builder(user, 41)
        assert first.id == second.id == session
        assert first.messages == []
        get.assert_awaited_with(41, organization_id=7, user_id=3, create=True)
    with patch(
        "api.services.workflow.builder.db_client.get_workflow_builder_session_id",
        AsyncMock(return_value=None),
    ):
        with pytest.raises(HTTPException) as error:
            await open_workflow_builder(user, 41)
        assert error.value.status_code == 404
