"""
TDD tests for workflow versioning lifecycle.

Tests the version lifecycle on WorkflowDefinitionModel:
  - status: draft / published / archived
  - version_number: sequential per workflow
  - released_definition_id on WorkflowModel

Modules under test:
  - api.db.workflow_client (new versioning methods)
  - api.db.models (new columns on WorkflowDefinitionModel, WorkflowModel)

These are DB integration tests using the transactional test session.
"""

import pytest

from api.db.models import (
    OrganizationModel,
    UserModel,
)

# Sample workflow definitions (graph JSON)
GRAPH_V1 = {
    "nodes": [
        {"id": "1", "type": "startCall", "data": {"name": "Start", "prompt": "Hello"}},
        {"id": "2", "type": "endCall", "data": {"name": "End", "prompt": "Bye"}},
    ],
    "edges": [{"id": "e1", "source": "1", "target": "2", "data": {"label": "End"}}],
}

GRAPH_V2 = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "data": {"name": "Start", "prompt": "Hello v2"},
        },
        {
            "id": "2",
            "type": "agentNode",
            "data": {"name": "Agent", "prompt": "Collect info"},
        },
        {"id": "3", "type": "endCall", "data": {"name": "End", "prompt": "Bye"}},
    ],
    "edges": [
        {"id": "e1", "source": "1", "target": "2", "data": {"label": "Collect"}},
        {"id": "e2", "source": "2", "target": "3", "data": {"label": "End"}},
    ],
}

GRAPH_V3 = {
    "nodes": [
        {
            "id": "1",
            "type": "startCall",
            "data": {"name": "Start", "prompt": "Hello v3"},
        },
        {"id": "2", "type": "endCall", "data": {"name": "End", "prompt": "Goodbye"}},
    ],
    "edges": [{"id": "e1", "source": "1", "target": "2", "data": {"label": "End"}}],
}

CONFIG_V1 = {"max_call_duration": 300}
CONFIG_V2 = {
    "max_call_duration": 600,
    "model_overrides": {"llm": {"model": "gpt-4.1-mini"}},
}
TEMPLATE_VARS_V1 = {"company_name": "Acme"}
TEMPLATE_VARS_V2 = {"company_name": "Acme Inc"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def org_and_user(async_session):
    """Create an organization and user for workflow tests."""
    org = OrganizationModel(provider_id="test-org-versioning")
    async_session.add(org)
    await async_session.flush()

    user = UserModel(
        provider_id="test-user-versioning", selected_organization_id=org.id
    )
    async_session.add(user)
    await async_session.flush()

    return org, user


@pytest.fixture
async def workflow_with_v1(db_session, org_and_user):
    """Create a workflow — should produce V1 as published."""
    org, user = org_and_user
    workflow = await db_session.create_workflow(
        name="Test Workflow",
        workflow_definition=GRAPH_V1,
        user_id=user.id,
        organization_id=org.id,
    )
    return workflow, user


# ---------------------------------------------------------------------------
# Workflow creation → V1 published
# ---------------------------------------------------------------------------


class TestWorkflowCreation:
    async def test_create_workflow_produces_published_v1(
        self, db_session, org_and_user
    ):
        """Creating a new workflow should produce exactly one definition
        with status='published' and version_number=1."""
        org, user = org_and_user
        workflow = await db_session.create_workflow(
            name="New Workflow",
            workflow_definition=GRAPH_V1,
            user_id=user.id,
            organization_id=org.id,
        )

        versions = await db_session.get_workflow_versions(workflow.id)
        assert len(versions) == 1

        v1 = versions[0]
        assert v1.status == "published"
        assert v1.version_number == 1
        assert v1.workflow_json == GRAPH_V1

    async def test_create_workflow_sets_released_pointer(
        self, db_session, org_and_user
    ):
        """The workflow's released_definition_id should point to V1."""
        org, user = org_and_user
        workflow = await db_session.create_workflow(
            name="Pointer Test",
            workflow_definition=GRAPH_V1,
            user_id=user.id,
            organization_id=org.id,
        )

        versions = await db_session.get_workflow_versions(workflow.id)
        assert workflow.released_definition_id == versions[0].id


# ---------------------------------------------------------------------------
# Saving a draft
# ---------------------------------------------------------------------------


class TestSaveDraft:
    async def test_save_draft_creates_draft_version(self, db_session, workflow_with_v1):
        """Saving changes to a published workflow creates a draft version."""
        workflow, user = workflow_with_v1

        draft = await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
            workflow_configurations=CONFIG_V2,
            template_context_variables=TEMPLATE_VARS_V2,
        )

        assert draft.status == "draft"
        assert draft.version_number == 2
        assert draft.workflow_json == GRAPH_V2
        assert draft.workflow_configurations == CONFIG_V2
        assert draft.template_context_variables == TEMPLATE_VARS_V2

    async def test_save_draft_does_not_change_released_pointer(
        self, db_session, workflow_with_v1
    ):
        """Creating a draft must not move the released pointer."""
        workflow, user = workflow_with_v1
        original_released_id = workflow.released_definition_id

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )

        refreshed = await db_session.get_workflow(workflow.id)
        assert refreshed.released_definition_id == original_released_id

    async def test_save_draft_twice_updates_in_place(
        self, db_session, workflow_with_v1
    ):
        """Saving a second draft should update the existing draft, not create a new row."""
        workflow, user = workflow_with_v1

        draft1 = await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )

        draft2 = await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V3,
        )

        assert draft1.id == draft2.id  # same row
        assert draft2.workflow_json == GRAPH_V3
        assert draft2.version_number == 2  # unchanged

        versions = await db_session.get_workflow_versions(workflow.id)
        assert len(versions) == 2  # V1 published + V2 draft, no extras

    async def test_save_draft_with_only_config_change(
        self, db_session, workflow_with_v1
    ):
        """A draft can change only configs, keeping the same graph."""
        workflow, user = workflow_with_v1

        draft = await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V1,  # same graph
            workflow_configurations=CONFIG_V2,  # different config
        )

        assert draft.status == "draft"
        assert draft.workflow_json == GRAPH_V1
        assert draft.workflow_configurations == CONFIG_V2


# ---------------------------------------------------------------------------
# Publishing a draft
# ---------------------------------------------------------------------------


class TestPublishDraft:
    async def test_publish_promotes_draft_to_published(
        self, db_session, workflow_with_v1
    ):
        """Publishing moves draft → published and old published → archived."""
        workflow, user = workflow_with_v1

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
            workflow_configurations=CONFIG_V2,
        )

        published = await db_session.publish_workflow_draft(workflow.id)

        assert published.status == "published"
        assert published.workflow_json == GRAPH_V2

        versions = await db_session.get_workflow_versions(workflow.id)
        statuses = {v.version_number: v.status for v in versions}
        assert statuses[1] == "archived"
        assert statuses[2] == "published"

    async def test_publish_updates_released_pointer(self, db_session, workflow_with_v1):
        """After publishing, released_definition_id should point to the new version."""
        workflow, user = workflow_with_v1

        draft = await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )

        await db_session.publish_workflow_draft(workflow.id)

        refreshed = await db_session.get_workflow(workflow.id)
        assert refreshed.released_definition_id == draft.id

    async def test_publish_sets_published_at(self, db_session, workflow_with_v1):
        """Published version should have a published_at timestamp."""
        workflow, user = workflow_with_v1

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )

        published = await db_session.publish_workflow_draft(workflow.id)
        assert published.published_at is not None

    async def test_publish_with_no_draft_raises(self, db_session, workflow_with_v1):
        """Publishing when no draft exists should raise an error."""
        workflow, user = workflow_with_v1

        with pytest.raises(ValueError, match="[Nn]o draft"):
            await db_session.publish_workflow_draft(workflow.id)

    async def test_exactly_one_published_after_multiple_cycles(
        self, db_session, workflow_with_v1
    ):
        """After several draft/publish cycles, exactly one version is published."""
        workflow, user = workflow_with_v1

        # Cycle 1
        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )
        await db_session.publish_workflow_draft(workflow.id)

        # Cycle 2
        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V3,
        )
        await db_session.publish_workflow_draft(workflow.id)

        versions = await db_session.get_workflow_versions(workflow.id)
        published = [v for v in versions if v.status == "published"]
        assert len(published) == 1
        assert published[0].version_number == 3


# ---------------------------------------------------------------------------
# Discarding a draft
# ---------------------------------------------------------------------------


class TestDiscardDraft:
    async def test_discard_removes_draft(self, db_session, workflow_with_v1):
        """Discarding a draft should delete the draft row."""
        workflow, user = workflow_with_v1

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )

        await db_session.discard_workflow_draft(workflow.id)

        versions = await db_session.get_workflow_versions(workflow.id)
        assert len(versions) == 1
        assert versions[0].status == "published"

    async def test_discard_does_not_affect_published(
        self, db_session, workflow_with_v1
    ):
        """Published version and released pointer are unchanged after discard."""
        workflow, user = workflow_with_v1
        original_released_id = workflow.released_definition_id

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )
        await db_session.discard_workflow_draft(workflow.id)

        refreshed = await db_session.get_workflow(workflow.id)
        assert refreshed.released_definition_id == original_released_id

    async def test_discard_when_no_draft_raises(self, db_session, workflow_with_v1):
        """Discarding when no draft exists should raise an error."""
        workflow, user = workflow_with_v1

        with pytest.raises(ValueError, match="[Nn]o draft"):
            await db_session.discard_workflow_draft(workflow.id)

    async def test_new_draft_after_discard_gets_next_version_number(
        self, db_session, workflow_with_v1
    ):
        """After discarding V2 draft, the next draft should still be V2
        (since V2 was deleted and never published)."""
        workflow, user = workflow_with_v1

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )
        await db_session.discard_workflow_draft(workflow.id)

        new_draft = await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V3,
        )
        # Version number reuse is acceptable since V2 was never published
        assert new_draft.version_number == 2


# ---------------------------------------------------------------------------
# Reverting to an archived version
# ---------------------------------------------------------------------------


class TestRevert:
    async def _publish_v2(self, db_session, workflow):
        """Helper: create and publish V2, making V1 archived."""
        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
            workflow_configurations=CONFIG_V2,
            template_context_variables=TEMPLATE_VARS_V2,
        )
        return await db_session.publish_workflow_draft(workflow.id)

    async def test_revert_creates_draft_from_archived(
        self, db_session, workflow_with_v1
    ):
        """Reverting copies the archived version's full snapshot into a new draft."""
        workflow, user = workflow_with_v1

        # Get V1's definition ID before it gets archived
        versions_before = await db_session.get_workflow_versions(workflow.id)
        v1_id = versions_before[0].id

        # Publish V2, archiving V1
        await self._publish_v2(db_session, workflow)

        # Revert to V1
        draft = await db_session.revert_to_version(workflow.id, v1_id)

        assert draft.status == "draft"
        assert draft.workflow_json == GRAPH_V1

    async def test_revert_preserves_all_snapshot_fields(
        self, db_session, workflow_with_v1
    ):
        """Revert should copy graph, configs, and template vars."""
        workflow, user = workflow_with_v1

        # Publish V2 with full config
        v2 = await self._publish_v2(db_session, workflow)

        # Publish V3, archiving V2
        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V3,
        )
        await db_session.publish_workflow_draft(workflow.id)

        # Revert to V2
        draft = await db_session.revert_to_version(workflow.id, v2.id)

        assert draft.workflow_json == GRAPH_V2
        assert draft.workflow_configurations == CONFIG_V2
        assert draft.template_context_variables == TEMPLATE_VARS_V2

    async def test_revert_when_draft_exists_raises(self, db_session, workflow_with_v1):
        """Cannot revert when a draft already exists — must discard first."""
        workflow, user = workflow_with_v1
        versions = await db_session.get_workflow_versions(workflow.id)
        v1_id = versions[0].id

        await self._publish_v2(db_session, workflow)

        # Create a draft
        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V3,
        )

        with pytest.raises(ValueError, match="[Dd]raft.*exists"):
            await db_session.revert_to_version(workflow.id, v1_id)

    async def test_revert_does_not_change_released_pointer(
        self, db_session, workflow_with_v1
    ):
        """Revert creates a draft — the released pointer stays on the published version."""
        workflow, user = workflow_with_v1
        versions = await db_session.get_workflow_versions(workflow.id)
        v1_id = versions[0].id

        v2 = await self._publish_v2(db_session, workflow)

        await db_session.revert_to_version(workflow.id, v1_id)

        refreshed = await db_session.get_workflow(workflow.id)
        assert refreshed.released_definition_id == v2.id  # still V2


# ---------------------------------------------------------------------------
# Version listing & ordering
# ---------------------------------------------------------------------------


class TestVersionListing:
    async def test_versions_ordered_by_version_number_desc(
        self, db_session, workflow_with_v1
    ):
        """Versions should be returned newest first."""
        workflow, user = workflow_with_v1

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )
        await db_session.publish_workflow_draft(workflow.id)

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V3,
        )

        versions = await db_session.get_workflow_versions(workflow.id)
        version_numbers = [v.version_number for v in versions]
        assert version_numbers == sorted(version_numbers, reverse=True)

    async def test_versions_include_status(self, db_session, workflow_with_v1):
        """Each version should have an explicit status."""
        workflow, user = workflow_with_v1

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )
        await db_session.publish_workflow_draft(workflow.id)

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V3,
        )

        versions = await db_session.get_workflow_versions(workflow.id)
        statuses = {v.version_number: v.status for v in versions}
        assert statuses == {1: "archived", 2: "published", 3: "draft"}


# ---------------------------------------------------------------------------
# Version data stored on definition, not workflow
# ---------------------------------------------------------------------------


class TestVersionDataOnDefinition:
    async def test_configs_stored_on_definition(self, db_session, workflow_with_v1):
        """workflow_configurations should be on the definition, not just the workflow."""
        workflow, user = workflow_with_v1

        draft = await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
            workflow_configurations=CONFIG_V2,
            template_context_variables=TEMPLATE_VARS_V2,
        )

        assert draft.workflow_configurations == CONFIG_V2
        assert draft.template_context_variables == TEMPLATE_VARS_V2

    async def test_different_versions_have_different_configs(
        self, db_session, workflow_with_v1
    ):
        """V1 and V2 can have different configs stored independently."""
        workflow, user = workflow_with_v1

        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
            workflow_configurations=CONFIG_V2,
        )
        await db_session.publish_workflow_draft(workflow.id)

        versions = await db_session.get_workflow_versions(workflow.id)
        configs_by_version = {
            v.version_number: v.workflow_configurations for v in versions
        }

        assert configs_by_version[1] != configs_by_version[2]


# ---------------------------------------------------------------------------
# Run creation uses published (or draft for testing)
# ---------------------------------------------------------------------------


class TestRunDefinitionBinding:
    async def test_campaign_run_uses_published_version(
        self, db_session, workflow_with_v1
    ):
        """A campaign-initiated run should use the published version, not draft."""
        workflow, user = workflow_with_v1

        # Create a draft (unpublished)
        await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )

        # Create a run (simulating campaign dispatch)
        run = await db_session.create_workflow_run(
            name="Campaign Run",
            workflow_id=workflow.id,
            mode="webrtc",
            user_id=user.id,
        )

        # Run should be bound to the published V1, not the draft V2
        versions = await db_session.get_workflow_versions(workflow.id)
        published = next(v for v in versions if v.status == "published")
        assert run.definition_id == published.id

    async def test_test_run_uses_draft_if_exists(self, db_session, workflow_with_v1):
        """A test/phone call should use the draft version for pre-publish testing."""
        workflow, user = workflow_with_v1

        draft = await db_session.save_workflow_draft(
            workflow_id=workflow.id,
            workflow_definition=GRAPH_V2,
        )

        # Create a test run
        run = await db_session.create_workflow_run(
            name="Test Run",
            workflow_id=workflow.id,
            mode="webrtc",  # test mode
            user_id=user.id,
            use_draft=True,
        )

        assert run.definition_id == draft.id
