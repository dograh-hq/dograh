"""Credit reservation + reconcile: reserve, insufficient, unmetered, settle."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.services.credits import reservation
from api.services.credits.reservation import (
    CREDITS_SETTLED_KEY,
    RESERVED_CREDIT_SECONDS_KEY,
    reconcile_call_credits,
    reserve_call_credits,
    settle_workflow_run_credits,
)


def _patch(method, **kw):
    return patch.object(reservation.db_client, method, new=AsyncMock(**kw))


async def test_reserve_unmetered_returns_zero_and_never_charges():
    charge = AsyncMock(return_value=True)
    with _patch("get_free_call_seconds_remaining", return_value=None), patch.object(
        reservation.db_client, "try_charge_call_seconds", new=charge
    ):
        assert await reserve_call_credits(1, 600) == 0
    charge.assert_not_awaited()


async def test_reserve_metered_sufficient_returns_est():
    with _patch("get_free_call_seconds_remaining", return_value=1000), _patch(
        "try_charge_call_seconds", return_value=True
    ):
        assert await reserve_call_credits(1, 600) == 600


async def test_reserve_metered_insufficient_returns_none():
    with _patch("get_free_call_seconds_remaining", return_value=100), _patch(
        "try_charge_call_seconds", return_value=False
    ):
        assert await reserve_call_credits(1, 600) is None


async def test_reconcile_metered_releases_hold_then_charges_actual():
    add = AsyncMock(return_value=470)
    consume = AsyncMock()
    with _patch("get_free_call_seconds_remaining", return_value=400), patch.object(
        reservation.db_client, "add_call_seconds", new=add
    ), patch.object(reservation, "consume_free_call_seconds", new=consume):
        await reconcile_call_credits(1, 600, 130)
    add.assert_awaited_once_with(1, 600)
    consume.assert_awaited_once_with(1, 130)


async def test_reconcile_no_reservation_only_consumes():
    add = AsyncMock()
    consume = AsyncMock()
    with patch.object(reservation.db_client, "add_call_seconds", new=add), patch.object(
        reservation, "consume_free_call_seconds", new=consume
    ):
        await reconcile_call_credits(1, 0, 95)
    add.assert_not_awaited()
    consume.assert_awaited_once_with(1, 95)


async def test_reconcile_swallows_errors():
    with patch.object(
        reservation, "consume_free_call_seconds", new=AsyncMock(side_effect=RuntimeError("x"))
    ):
        await reconcile_call_credits(1, 0, 10)  # must not raise


async def test_settle_reads_reserved_and_duration_off_run():
    run = SimpleNamespace(
        id=9,
        initial_context={RESERVED_CREDIT_SECONDS_KEY: 600},
        usage_info={"call_duration_seconds": 130},
        cost_info={},
    )
    rec = AsyncMock()
    with patch.object(reservation, "reconcile_call_credits", new=rec), _patch(
        "update_workflow_run"
    ):
        await settle_workflow_run_credits(1, run)
    rec.assert_awaited_once_with(1, 600, 130)


async def test_settle_is_idempotent_when_already_settled():
    run = SimpleNamespace(
        id=9,
        initial_context={RESERVED_CREDIT_SECONDS_KEY: 600, CREDITS_SETTLED_KEY: True},
        usage_info={"call_duration_seconds": 130},
        cost_info={},
    )
    rec = AsyncMock()
    upd = AsyncMock()
    with patch.object(reservation, "reconcile_call_credits", new=rec), patch.object(
        reservation.db_client, "update_workflow_run", new=upd
    ):
        await settle_workflow_run_credits(1, run)
    rec.assert_not_awaited()  # already settled — a retry must not double-charge
    upd.assert_not_awaited()


async def test_settle_marks_run_settled_after_reconcile():
    run = SimpleNamespace(
        id=9,
        initial_context={RESERVED_CREDIT_SECONDS_KEY: 600},
        usage_info={"call_duration_seconds": 130},
        cost_info={},
    )
    upd = AsyncMock()
    with patch.object(reservation, "reconcile_call_credits", new=AsyncMock()), patch.object(
        reservation.db_client, "update_workflow_run", new=upd
    ):
        await settle_workflow_run_credits(1, run)
    upd.assert_awaited_once_with(9, initial_context={CREDITS_SETTLED_KEY: True})
