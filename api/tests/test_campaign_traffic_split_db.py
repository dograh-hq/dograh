"""Real PostgreSQL coverage for attribution, concurrent edits, retries and redials."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, update

from api.db import db_client
from api.db.models import (
    CampaignModel,
    OrganizationModel,
    QueuedRunModel,
    UserModel,
    WorkflowDefinitionModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.routes.campaign import (
    CreateCampaignRequest,
    UpdateCampaignRequest,
    create_campaign,
    get_campaign_traffic_stats,
    update_campaign,
)
from api.schemas.campaign import TrafficSplitRequest
from api.services.campaign.source_sync import ValidationResult
from api.services.campaign.traffic_split import (
    campaign_split,
    pick_variant,
    resolve_variants,
    traffic_stats,
)
from api.tests import test_campaign_call_dispatcher as fixtures
from api.tests.conftest import DEFAULT_WORKFLOW_DEFINITION

sessions = fixtures.sessions


@pytest.fixture
async def data(sessions):
    async with sessions() as session:
        org = OrganizationModel(provider_id=f"traffic-{uuid.uuid4().hex}")
        session.add(org)
        await session.flush()
        user = UserModel(
            provider_id=f"traffic-{uuid.uuid4().hex}", selected_organization_id=org.id
        )
        session.add(user)
        await session.flush()
        workflows = [
            WorkflowModel(name=f"Variant {i}", user_id=user.id, organization_id=org.id)
            for i in range(2)
        ]
        session.add_all(workflows)
        await session.flush()
        definitions = [
            WorkflowDefinitionModel(
                workflow_id=w.id,
                workflow_json=DEFAULT_WORKFLOW_DEFINITION,
                status="published",
                version_number=1,
                is_current=True,
                workflow_configurations={},
            )
            for w in workflows
        ]
        session.add_all(definitions)
        await session.flush()
        for w, d in zip(workflows, definitions):
            w.released_definition_id = d.id
        await session.commit()
    campaign = await db_client.create_campaign(
        "traffic", workflows[0].id, "csv", "test.csv", user.id, org.id
    )
    await db_client.update_campaign(campaign.id, state="running")
    row = await db_client.create_queued_run(
        campaign.id, "contact", {"phone_number": "+15550000001"}
    )
    yield SimpleNamespace(
        org=org,
        user=user,
        workflows=workflows,
        definitions=definitions,
        campaign=campaign,
        row=row,
    )
    async with sessions() as session:
        campaign_ids = select(CampaignModel.id).where(
            CampaignModel.organization_id == org.id
        )
        await session.execute(
            delete(WorkflowRunModel).where(
                WorkflowRunModel.campaign_id.in_(campaign_ids)
            )
        )
        await session.execute(
            delete(QueuedRunModel).where(QueuedRunModel.campaign_id.in_(campaign_ids))
        )
        await session.execute(
            delete(CampaignModel).where(CampaignModel.organization_id == org.id)
        )
        ids = [w.id for w in workflows]
        await session.execute(
            update(WorkflowModel)
            .where(WorkflowModel.id.in_(ids))
            .values(released_definition_id=None)
        )
        await session.execute(
            delete(WorkflowDefinitionModel).where(
                WorkflowDefinitionModel.workflow_id.in_(ids)
            )
        )
        await session.execute(delete(WorkflowModel).where(WorkflowModel.id.in_(ids)))
        await session.execute(delete(UserModel).where(UserModel.id == user.id))
        await session.execute(
            delete(OrganizationModel).where(OrganizationModel.id == org.id)
        )
        await session.commit()


def split_request(data, weights=(50, 50)):
    return TrafficSplitRequest(
        variants=[
            {"workflow_id": w.id, "weight": weight}
            for w, weight in zip(data.workflows, weights)
        ]
    )


async def create_run(data, index=1, *, attribution=None, logs=None, context=None):
    return await db_client.create_workflow_run(
        name="traffic",
        workflow_id=data.workflows[index].id,
        mode="twilio",
        user_id=data.user.id,
        organization_id=data.org.id,
        campaign_id=data.campaign.id,
        queued_run_id=data.row.id,
        definition_id=data.definitions[index].id,
        campaign_traffic_split=attribution,
        logs=logs,
        gathered_context=context,
    )


@pytest.mark.asyncio
async def test_concurrent_settings_updates_preserve_split_seed_and_primary(data):
    async def split_update():
        return await db_client.update_campaign_settings(
            data.campaign.id,
            data.org.id,
            settings={},
            metadata_patch={},
            traffic_split=split_request(data, (20, 80)),
        )

    async def schedule_update():
        return await db_client.update_campaign_settings(
            data.campaign.id,
            data.org.id,
            settings={"name": "renamed"},
            metadata_patch={"schedule_config": {"enabled": False}},
        )

    await asyncio.wait_for(asyncio.gather(split_update(), schedule_update()), 5)
    saved = await db_client.get_campaign(data.campaign.id, data.org.id)
    split = campaign_split(saved)
    assert saved.name == "renamed"
    assert saved.orchestrator_metadata["schedule_config"] == {"enabled": False}
    assert saved.workflow_id == data.workflows[1].id
    await db_client.update_campaign_settings(
        saved.id,
        data.org.id,
        settings={},
        metadata_patch={"max_concurrency": None},
        traffic_split=split_request(data, (60, 40)),
    )
    edited = await db_client.get_campaign(saved.id, data.org.id)
    assert campaign_split(edited)["seed"] == split["seed"]
    assert campaign_split(edited)["revision"] == 2
    assert edited.workflow_id == data.workflows[0].id
    with pytest.raises(ValueError, match="not found"):
        await db_client.update_campaign_settings(
            saved.id, data.org.id + 1, settings={}, metadata_patch={}
        )


@pytest.mark.asyncio
async def test_stats_distinguish_latest_and_pinned_same_definition_and_keep_removed(
    data,
):
    workflow_id, definition_id = data.workflows[1].id, data.definitions[1].id
    request = TrafficSplitRequest(
        variants=[
            {"workflow_id": workflow_id, "weight": 50},
            {
                "workflow_id": workflow_id,
                "workflow_definition_id": definition_id,
                "weight": 50,
            },
        ]
    )
    campaign = await db_client.update_campaign_settings(
        data.campaign.id,
        data.org.id,
        settings={},
        metadata_patch={},
        traffic_split=request,
    )
    split = campaign_split(campaign)
    for i, variant in enumerate(split["variants"]):
        run = await create_run(
            data,
            attribution={
                "variant_id": variant["id"],
                "workflow_definition_id": variant["workflow_definition_id"],
                "revision": 1,
                "weight": 50,
            },
            context={"call_disposition": "voicemail" if i else "completed"},
        )
        await db_client.update_workflow_run(
            run.id, is_completed=True, state="completed"
        )
    stats = await traffic_stats(db_client, campaign)
    assert stats.total_attempts == 2
    assert len(stats.variants) == 2
    assert all(
        v.attempts == 1 and v.actual_percentage == 50 and v.completed == 1
        for v in stats.variants
    )
    assert {v.workflow_definition_id for v in stats.variants} == {None, definition_id}
    assert all(v.definitions[0].version_number == 1 for v in stats.variants)
    campaign = await db_client.update_campaign_settings(
        campaign.id,
        data.org.id,
        settings={},
        metadata_patch={},
        traffic_split=TrafficSplitRequest(
            variants=[{"workflow_id": workflow_id, "weight": 100}]
        ),
    )
    stats = await traffic_stats(db_client, campaign)
    assert {v.target_weight for v in stats.variants} == {100, None}
    assert (
        await db_client.get_campaign_traffic_counts(campaign.id, data.org.id + 1) == []
    )
    runs, count = await db_client.get_campaign_runs_paginated(campaign.id, data.org.id)
    assert count == 2
    assert all(
        r.workflow_name == data.workflows[1].name and r.version_number == 1
        for r in runs
    )


@pytest.mark.asyncio
async def test_stats_exclude_undialed_setups(data):
    campaign = await db_client.update_campaign_settings(
        data.campaign.id,
        data.org.id,
        settings={},
        metadata_patch={},
        traffic_split=split_request(data),
    )
    run = await create_run(
        data,
        0,
        logs={"campaign_dispatch": {"outcome": "not_started"}},
        context={"error": "Call setup cancelled"},
    )
    await db_client.update_workflow_run(run.id, is_completed=True, state="completed")

    stats = await traffic_stats(db_client, campaign)
    assert stats.total_attempts == 0
    for variant in stats.variants:
        assert variant.attempts == variant.completed == variant.actual_percentage == 0
        assert variant.states == variant.outcomes == {}
        assert variant.definitions == []

    for index in range(2):
        run = await create_run(data, index, context={"call_disposition": "completed"})
        await db_client.update_workflow_run(
            run.id, is_completed=True, state="completed"
        )

    stats = await traffic_stats(db_client, campaign)
    assert stats.total_attempts == 2
    for variant in stats.variants:
        assert variant.attempts == variant.completed == 1
        assert variant.actual_percentage == 50
        assert variant.states == {"completed": 1}
        assert variant.outcomes == {"completed": 1}
        assert len(variant.definitions) == 1
        assert variant.definitions[0].attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatch_outcome", [None, "failed", "uncertain", "stale"])
async def test_stats_keep_possible_call_attempts(data, dispatch_outcome):
    await create_run(data, 0, logs={"campaign_dispatch": {"outcome": dispatch_outcome}})
    stats = await traffic_stats(db_client, data.campaign)
    assert stats.total_attempts == 1
    assert stats.variants[0].attempts == 1
    assert stats.variants[0].actual_percentage == 100


@pytest.mark.asyncio
async def test_stats_use_mapped_outcomes_with_legacy_fallbacks(data):
    contexts = [
        {"mapped_call_disposition": "XFER", "call_disposition": "transfer"},
        {
            "mapped_call_disposition": "XFER",
            "call_disposition": "external_pbx_transfer",
        },
        {"call_disposition": "completed"},
        {"mapped_call_disposition": None, "call_disposition": "completed"},
        {"mapped_call_disposition": "", "call_disposition": "completed"},
        {"call_status": "no-answer"},
        {},
    ]
    for context in contexts:
        await create_run(data, 0, context=context)

    stats = await traffic_stats(db_client, data.campaign)
    assert stats.total_attempts == len(contexts)
    assert stats.variants[0].outcomes == {
        "XFER": 2,
        "completed": 3,
        "no-answer": 1,
        "unknown": 1,
    }


@pytest.mark.asyncio
async def test_non_primary_retry_and_stale_recovery(data):
    run = await create_run(
        data,
        logs={
            "campaign_dispatch": {
                "uncertain_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            }
        },
        context={"call_initiation_uncertain": True},
    )
    retry = await db_client.record_campaign_retry_decision(
        run.id, data.campaign.id, data.org.id, "no_answer", datetime.now(UTC)
    )
    assert retry is not None
    assert retry == await db_client.record_campaign_retry_decision(
        run.id, data.campaign.id, data.org.id, "no_answer", datetime.now(UTC)
    )
    recoveries = await db_client.recover_stale_campaign_dispatches(
        stale_before=datetime.now(UTC) - timedelta(minutes=1), limit=1000
    )
    assert run.id in {row["workflow_run_id"] for row in recoveries}


@pytest.mark.asyncio
async def test_redial_copies_split_and_keeps_contact_assignment(data):
    campaign = await db_client.update_campaign_settings(
        data.campaign.id,
        data.org.id,
        settings={},
        metadata_patch={},
        traffic_split=split_request(data),
    )
    child = await db_client.create_redial_campaign(
        campaign,
        "redial",
        None,
        [
            {
                "campaign_id": 0,
                "source_uuid": "contact",
                "context_variables": data.row.context_variables,
            }
        ],
    )
    assert campaign_split(child) == campaign_split(campaign)
    assert pick_variant(campaign_split(child), "+15550000001") == pick_variant(
        campaign_split(campaign), "+15550000001"
    )


@pytest.mark.asyncio
async def test_definition_ownership_and_draft_checks(data, sessions):
    workflow, definition = data.workflows[0], data.definitions[0]
    assert (
        await db_client.get_workflow_definition(
            workflow.id, definition.id, data.org.id + 1
        )
        is None
    )
    assert (
        await db_client.get_workflow_definition(
            data.workflows[1].id, definition.id, data.org.id
        )
        is None
    )
    variants = [{"workflow_id": workflow.id, "workflow_definition_id": definition.id}]
    assert len(await resolve_variants(db_client, variants, data.org.id)) == 1
    async with sessions() as session:
        await session.execute(
            update(WorkflowDefinitionModel)
            .where(WorkflowDefinitionModel.id == definition.id)
            .values(status="draft")
        )
        await session.commit()
    with pytest.raises(ValueError):
        await resolve_variants(db_client, variants, data.org.id)


@pytest.mark.asyncio
async def test_create_and_edit_api_return_split_and_validate_csv(data):
    source = ValidationResult(
        is_valid=True, headers=["phone_number"], rows=[["+15550000001"]]
    )
    with (
        patch(
            "api.routes.campaign.resolve_outbound_configuration_id",
            AsyncMock(return_value=None),
        ),
        patch(
            "api.routes.campaign.requires_e164_destinations",
            AsyncMock(return_value=True),
        ),
        patch(
            "api.routes.campaign.get_sync_service",
            return_value=SimpleNamespace(
                validate_source=AsyncMock(return_value=source)
            ),
        ),
        patch("api.routes.campaign._validate_dial_rate", AsyncMock()),
        patch(
            "api.routes.campaign._get_telephony_configuration_name",
            AsyncMock(return_value=None),
        ),
    ):
        created = await create_campaign(
            CreateCampaignRequest(
                name="API split",
                source_type="csv",
                source_id="test.csv",
                traffic_split=split_request(data),
            ),
            data.user,
        )
        assert created.traffic_split.revision == 1
        assert {v.workflow_name for v in created.traffic_split.variants} == {
            w.name for w in data.workflows
        }
        edited = await update_campaign(
            created.id,
            UpdateCampaignRequest(traffic_split=split_request(data, (30, 70))),
            data.user,
        )
        assert edited.workflow_id == data.workflows[1].id
        assert edited.traffic_split.revision == 2
        assert [v.weight for v in edited.traffic_split.variants] == [30, 70]
        from api.services.campaign.source_sync import ValidationError

        source.is_valid = False
        source.error = ValidationError(message="Missing CSV column")
        with pytest.raises(HTTPException, match="Missing CSV column"):
            await update_campaign(
                created.id,
                UpdateCampaignRequest(traffic_split=split_request(data)),
                data.user,
            )
        unchanged = await db_client.get_campaign(created.id, data.org.id)
        assert campaign_split(unchanged)["revision"] == 2


@pytest.mark.asyncio
async def test_stats_api_rejects_other_org(data):
    with pytest.raises(HTTPException) as exc:
        await get_campaign_traffic_stats(
            data.campaign.id, SimpleNamespace(selected_organization_id=data.org.id + 1)
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_version_summary_endpoint_is_scoped_and_excludes_drafts(data, sessions):
    from api.routes.workflow import get_workflow_version_summaries

    rows = await get_workflow_version_summaries(data.workflows[0].id, data.user)
    assert rows == [
        {
            "id": data.definitions[0].id,
            "version_number": 1,
            "status": "published",
            "published_at": None,
        }
    ]
    with pytest.raises(HTTPException) as exc:
        await get_workflow_version_summaries(
            data.workflows[0].id,
            SimpleNamespace(selected_organization_id=data.org.id + 1),
        )
    assert exc.value.status_code == 404
    async with sessions() as session:
        await session.execute(
            update(WorkflowDefinitionModel)
            .where(WorkflowDefinitionModel.id == data.definitions[0].id)
            .values(status="draft")
        )
        await session.commit()
    assert await get_workflow_version_summaries(data.workflows[0].id, data.user) == []
