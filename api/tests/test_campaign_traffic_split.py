import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from api.routes.campaign import CreateCampaignRequest, _authorize_campaign_variants
from api.schemas.campaign import TrafficSplitRequest
from api.services.campaign.source_sync import ValidationResult
from api.services.campaign.traffic_split import (
    build_split,
    campaign_split,
    pick_variant,
    resolve_variants,
    validate_variant_templates,
)
from api.services.workflow.run_creation import prepare_workflow_run_inputs
from api.tests import test_campaign_outbound_capacity as fixtures
from api.tests.conftest import DEFAULT_WORKFLOW_DEFINITION

setup_call = fixtures.setup_call


def request(weights=(50, 50)):
    return TrafficSplitRequest(
        variants=[
            {"workflow_id": i + 1, "weight": weight} for i, weight in enumerate(weights)
        ]
    )


def test_hash_stability_distribution_and_seed_independence():
    first = {**build_split(request()), "seed": "first"}
    second = {**first, "seed": "second"}
    numbers = [f"+1555{i:07d}" for i in range(100000)]
    a = [pick_variant(first, n)["id"] for n in numbers]
    assert a == [pick_variant(first, n)["id"] for n in numbers]
    assert 49000 < a.count("1:latest") < 51000
    assert (
        49000
        < sum(x != pick_variant(second, n)["id"] for x, n in zip(a, numbers))
        < 51000
    )
    edited = build_split(request((40, 60)), first)
    assert (
        9500
        < sum(x != pick_variant(edited, n)["id"] for x, n in zip(a, numbers))
        < 10500
    )
    assert pick_variant(first, "  +15550000001  ") == pick_variant(
        first, "+15550000001"
    )


def test_order_only_edit_is_noop_and_weight_edits_can_move_other_variants():
    first = build_split(request((20, 20, 20, 20, 20)))
    reverse = TrafficSplitRequest(variants=list(reversed(request((20,) * 5).variants)))
    assert build_split(reverse, first) == first
    edited = build_split(request((10, 20, 20, 20, 30)), first)
    assert edited["revision"] == first["revision"] + 1
    assert edited["seed"] == first["seed"]
    moved = sum(
        pick_variant(first, str(i))["id"] != pick_variant(edited, str(i))["id"]
        for i in range(10000)
    )
    assert 3700 < moved < 4300


def test_legacy_fallback_and_create_compatibility():
    campaign = SimpleNamespace(workflow_id=7, orchestrator_metadata={})
    assert pick_variant(campaign_split(campaign), "1001")["workflow_id"] == 7
    data = {"name": "test", "source_type": "csv", "source_id": "test"}
    assert CreateCampaignRequest(**data, workflow_id=7).traffic_split is None
    assert CreateCampaignRequest(**data, traffic_split=request()).workflow_id is None
    with pytest.raises(ValidationError):
        CreateCampaignRequest(**data)
    with pytest.raises(ValidationError):
        CreateCampaignRequest(**data, workflow_id=7, traffic_split=request())


@pytest.mark.parametrize(
    "variants",
    [
        [],
        [{"workflow_id": 1, "weight": 99}],
        [{"workflow_id": 1, "weight": True}],
        [{"workflow_id": 1, "weight": 100.0}],
        [{"workflow_id": 1, "weight": 0}, {"workflow_id": 2, "weight": 100}],
        [{"workflow_id": 1, "weight": 50}, {"workflow_id": 1, "weight": 50}],
        [
            {"workflow_id": i, "weight": w}
            for i, w in enumerate([20, 20, 20, 20, 10, 10], 1)
        ],
    ],
)
def test_invalid_splits_rejected(variants):
    with pytest.raises(ValidationError):
        TrafficSplitRequest(variants=variants)


@pytest.mark.asyncio
async def test_pinned_and_latest_resolve_to_different_definitions():
    live = SimpleNamespace(id=9, status="published")
    archived = SimpleNamespace(id=8, status="archived")
    workflow = SimpleNamespace(id=1, organization_id=2, released_definition=live)
    db = SimpleNamespace(get_workflow_definition=AsyncMock(return_value=archived))
    assert (
        await prepare_workflow_run_inputs(db, workflow, definition_id=8)
    ).definition_id == 8
    db.get_workflow_definition.assert_awaited_once_with(1, 8, 2)
    assert (await prepare_workflow_run_inputs(db, workflow)).definition_id == 9
    db.get_workflow_definition.return_value = SimpleNamespace(id=8, status="draft")
    with pytest.raises(ValueError):
        await prepare_workflow_run_inputs(db, workflow, definition_id=8)
    db.get_workflow_definition.return_value = None
    with pytest.raises(ValueError):
        await prepare_workflow_run_inputs(db, workflow, definition_id=8)


@pytest.mark.asyncio
async def test_cross_org_agent_rejected():
    db = SimpleNamespace(get_workflow=AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="agent not found"):
        await resolve_variants(db, [{"workflow_id": 4}], 3)
    db.get_workflow.assert_awaited_once_with(4, organization_id=3)


def test_template_check_uses_every_selected_definition():
    graph = deepcopy(DEFAULT_WORKFLOW_DEFINITION)
    graph["nodes"][0]["data"]["prompt"] = "Hello {{customer_name}}"
    resolved = [
        (None, SimpleNamespace(workflow_json=DEFAULT_WORKFLOW_DEFINITION)),
        (None, SimpleNamespace(workflow_json=graph)),
    ]
    source = ValidationResult(is_valid=True, headers=["phone_number"], rows=[["1001"]])
    with pytest.raises(ValueError, match="customer_name"):
        validate_variant_templates(resolved, source)
    source.headers.append("customer_name")
    source.rows[0].append("")
    with pytest.raises(ValueError, match="empty"):
        validate_variant_templates(resolved, source)
    source.rows[0][1] = "Alex"
    validate_variant_templates(resolved, source)


@pytest.mark.asyncio
async def test_dispatch_uses_selected_agent_version_and_attribution(setup_call):
    s = setup_call
    s.campaign.orchestrator_metadata["traffic_split"] = build_split(
        TrafficSplitRequest(
            variants=[
                {"workflow_id": 999, "workflow_definition_id": 77, "weight": 100},
            ]
        )
    )
    with patch(
        "api.services.campaign.campaign_call_dispatcher.prepare_workflow_run_inputs",
        AsyncMock(return_value=SimpleNamespace(definition_id=77)),
    ) as prepare:
        await asyncio.wait_for(s.dispatcher.dispatch_call(s.row, s.campaign, s.slot), 3)
    s.db.get_workflow.assert_awaited_once_with(999, organization_id=206)
    assert prepare.await_args.kwargs["definition_id"] == 77
    args = s.db.create_workflow_run.await_args.kwargs
    assert (args["workflow_id"], args["definition_id"]) == (999, 77)
    assert args["campaign_traffic_split"] == {
        "variant_id": "999:77",
        "revision": 1,
        "workflow_definition_id": 77,
        "weight": 100,
    }
    dial = s.provider.initiate_call.await_args.kwargs
    assert dial["workflow_id"] == 999
    assert "workflow_id=999" in dial["webhook_url"]


@pytest.mark.asyncio
async def test_campaign_preflight_checks_each_resolved_version():
    split = build_split(
        TrafficSplitRequest(
            variants=[
                {"workflow_id": 1, "workflow_definition_id": 8, "weight": 50},
                {"workflow_id": 1, "weight": 50},
            ]
        )
    )
    workflow = SimpleNamespace(
        id=1,
        organization_id=2,
        released_definition=SimpleNamespace(id=9, status="published"),
    )
    db = SimpleNamespace(
        get_workflow=AsyncMock(return_value=workflow),
        get_workflow_definition=AsyncMock(
            return_value=SimpleNamespace(id=8, status="archived")
        ),
    )
    authorize = AsyncMock(return_value=SimpleNamespace(has_quota=True))
    with (
        patch("api.routes.campaign.db_client", db),
        patch("api.routes.campaign.authorize_workflow_run_start", authorize),
    ):
        await _authorize_campaign_variants(
            SimpleNamespace(
                organization_id=2, orchestrator_metadata={"traffic_split": split}
            ),
            SimpleNamespace(selected_organization_id=2),
        )
    assert {call.kwargs["definition_id"] for call in authorize.await_args_list} == {
        8,
        9,
    }
