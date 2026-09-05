"""The completion job actually reaches the external-PBX write-back.

The write-back has silently stopped running twice, both times because of where
it was wired rather than what it did: once hung off the ARI hangup strategy,
which only executes when Dograh ends the call, and once off a trigger that ran
before the disposition existed. Neither had a test on the wiring itself. These
cover that seam.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.telephony import external_pbx_writeback
from api.tasks import workflow_completion


@pytest.fixture
def stub_completion_steps(monkeypatch):
    """Silence the other completion steps and capture the write-back call."""
    write_back = AsyncMock()
    monkeypatch.setattr(
        workflow_completion, "sync_external_pbx_call_record", write_back
    )
    monkeypatch.setattr(
        workflow_completion, "run_integrations_post_workflow_run", AsyncMock()
    )
    monkeypatch.setattr(
        workflow_completion,
        "report_completed_workflow_run_platform_usage",
        AsyncMock(),
    )
    return write_back


@pytest.mark.asyncio
async def test_completion_job_writes_back_to_the_pbx(stub_completion_steps):
    await workflow_completion.process_workflow_completion(None, 11)

    stub_completion_steps.assert_awaited_once_with(11)


@pytest.mark.asyncio
async def test_write_back_still_runs_when_integrations_fail(
    monkeypatch, stub_completion_steps
):
    # Integrations run QA against an LLM and are the most failure-prone step in
    # the job. The lead must still get its outcome when they blow up.
    monkeypatch.setattr(
        workflow_completion,
        "run_integrations_post_workflow_run",
        AsyncMock(side_effect=RuntimeError("QA provider is down")),
    )

    await workflow_completion.process_workflow_completion(None, 11)

    stub_completion_steps.assert_awaited_once_with(11)


@pytest.mark.asyncio
async def test_a_failing_write_back_does_not_fail_the_job(
    monkeypatch, stub_completion_steps
):
    stub_completion_steps.side_effect = RuntimeError("VICIdial unreachable")

    # No raise: completion is best-effort throughout.
    await workflow_completion.process_workflow_completion(None, 11)


@pytest.mark.asyncio
async def test_a_run_with_no_pbx_leg_is_a_silent_no_op(monkeypatch):
    """Text chats and web calls go through the same completion job."""
    adapter_built = AsyncMock()
    monkeypatch.setattr(
        external_pbx_writeback.db_client,
        "get_workflow_run_by_id",
        AsyncMock(
            return_value=SimpleNamespace(
                initial_context={},
                gathered_context={"mapped_call_disposition": "user_hangup"},
                workflow=SimpleNamespace(organization_id=7),
            )
        ),
    )
    monkeypatch.setattr(
        external_pbx_writeback, "external_pbx_integrations_enabled", adapter_built
    )

    await external_pbx_writeback.sync_external_pbx_call_record(11)

    # Bailed at the identity check, before doing any adapter or config work.
    adapter_built.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_missing_run_is_a_silent_no_op(monkeypatch):
    adapter_built = AsyncMock()
    monkeypatch.setattr(
        external_pbx_writeback.db_client,
        "get_workflow_run_by_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        external_pbx_writeback, "external_pbx_integrations_enabled", adapter_built
    )

    await external_pbx_writeback.sync_external_pbx_call_record(11)

    adapter_built.assert_not_awaited()
