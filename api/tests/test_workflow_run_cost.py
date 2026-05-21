from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.pricing import workflow_run_cost as workflow_run_cost_mod
from api.services.pricing.workflow_run_cost import (
    build_workflow_run_cost_info,
    calculate_workflow_run_cost,
)


def _make_workflow_run():
    return SimpleNamespace(
        id=123,
        workflow_id=456,
        mode="textchat",
        created_at=datetime.now(UTC),
        usage_info={
            "llm": {},
            "tts": {},
            "stt": {},
            "call_duration_seconds": 7,
        },
        cost_info={},
        workflow=SimpleNamespace(
            organization_id=42,
            user=SimpleNamespace(selected_organization_id=42),
        ),
    )


@pytest.mark.asyncio
async def test_build_workflow_run_cost_info_does_not_update_org_usage(monkeypatch):
    workflow_run = _make_workflow_run()
    get_org = AsyncMock(return_value=SimpleNamespace(id=42, price_per_second_usd=1.5))
    update_usage = AsyncMock()

    monkeypatch.setattr(
        workflow_run_cost_mod.db_client, "get_organization_by_id", get_org
    )
    monkeypatch.setattr(
        workflow_run_cost_mod.db_client, "update_usage_after_run", update_usage
    )

    cost_info = await build_workflow_run_cost_info(workflow_run)

    assert cost_info is not None
    assert cost_info["call_duration_seconds"] == 7
    assert "cost_breakdown" in cost_info
    assert "dograh_token_usage" in cost_info
    assert cost_info["charge_usd"] == 10.5
    update_usage.assert_not_called()


@pytest.mark.asyncio
async def test_calculate_workflow_run_cost_keeps_org_usage_side_effect_in_wrapper(
    monkeypatch,
):
    workflow_run = _make_workflow_run()
    get_org = AsyncMock(return_value=SimpleNamespace(id=42, price_per_second_usd=None))
    update_run = AsyncMock()
    update_usage = AsyncMock()

    monkeypatch.setattr(
        workflow_run_cost_mod.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=workflow_run),
    )
    monkeypatch.setattr(
        workflow_run_cost_mod.db_client, "get_organization_by_id", get_org
    )
    monkeypatch.setattr(
        workflow_run_cost_mod.db_client, "update_workflow_run", update_run
    )
    monkeypatch.setattr(
        workflow_run_cost_mod.db_client, "update_usage_after_run", update_usage
    )

    await calculate_workflow_run_cost(workflow_run.id)

    update_run.assert_awaited_once()
    saved_kwargs = update_run.await_args.kwargs
    assert saved_kwargs["run_id"] == workflow_run.id
    assert "cost_breakdown" in saved_kwargs["cost_info"]
    update_usage.assert_awaited_once()
