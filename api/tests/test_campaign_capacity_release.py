"""ARI terminal events preserve concurrency cleanup, including before Stasis."""

from unittest.mock import AsyncMock, patch

import pytest

ORG_ID = 206
CONFIG_ID = 55
RUN_ID = 2485

# --- dograh#737: a call destroyed before it ever entered Stasis ---------------


def _ari_connection():
    from api.services.telephony.ari_manager import ARIConnection

    return ARIConnection(
        organization_id=ORG_ID,
        telephony_configuration_id=CONFIG_ID,
        ari_endpoint="http://asterisk:8088",
        app_name="dograh",
        app_password="secret",
    )


@pytest.mark.asyncio
async def test_channel_destroyed_before_stasis_returns_its_reservation():
    # Rejected, busy and unanswered calls never enter Stasis, so no StasisEnd
    # arrives and ChannelDestroyed is the only event that can free anything.
    connection = _ari_connection()

    with (
        patch.object(
            connection, "_get_channel_run", AsyncMock(return_value=str(RUN_ID))
        ),
        patch.object(connection, "_delete_channel_run", AsyncMock()) as forget,
        patch(
            "api.services.telephony.ari_manager.call_concurrency"
        ) as mock_concurrency,
    ):
        mock_concurrency.release_workflow_run_slot = AsyncMock(return_value=True)

        await connection._release_destroyed_channel("1789335496.125", 1, "Unallocated")

    mock_concurrency.release_workflow_run_slot.assert_awaited_once_with(RUN_ID)
    forget.assert_awaited_once_with("1789335496.125")


@pytest.mark.asyncio
async def test_a_channel_we_never_originated_is_left_alone():
    # Inbound legs and anything else Asterisk destroys have no mapping, and
    # inventing a release for them would free capacity a live call is using.
    connection = _ari_connection()

    with (
        patch.object(connection, "_get_channel_run", AsyncMock(return_value=None)),
        patch(
            "api.services.telephony.ari_manager.call_concurrency"
        ) as mock_concurrency,
    ):
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        await connection._release_destroyed_channel("other.1", 16, "Normal Clearing")

    mock_concurrency.release_workflow_run_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_completed_call_releases_only_once():
    # StasisEnd already released and deleted the mapping, so the ChannelDestroyed
    # that follows finds nothing. Idempotence comes from the mapping, not from a
    # flag that could drift.
    connection = _ari_connection()

    with (
        patch.object(connection, "_get_channel_run", AsyncMock(return_value=None)),
        patch(
            "api.services.telephony.ari_manager.call_concurrency"
        ) as mock_concurrency,
    ):
        mock_concurrency.release_workflow_run_slot = AsyncMock()

        await connection._release_destroyed_channel("done.1", 16, "Normal Clearing")

    mock_concurrency.release_workflow_run_slot.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_redis_failure_does_not_escape_the_event_loop():
    # This runs as a fire-and-forget task off the event stream; an exception
    # here would be an unretrieved task exception, not a handled error.
    connection = _ari_connection()

    with patch.object(
        connection,
        "_get_channel_run",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        await connection._release_destroyed_channel("x.1", 1, "Unallocated")
