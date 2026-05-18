"""
Tests verifying that the from_number pool isolates numbers per
telephony_configuration_id within an organization.

When an org has multiple telephony configurations (each with its own pool of
caller IDs), a campaign pinned to config A must never be handed a from_number
that belongs to config B. Otherwise the call is placed via provider A using a
DID owned by config B and either fails or originates from the wrong number.

These tests cover both:
- The rate_limiter, which owns the Redis-backed pool.
- The CampaignCallDispatcher, which must thread the campaign's
  telephony_configuration_id through acquire / release / mapping calls.
"""

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
from api.services.campaign.rate_limiter import RateLimiter


def _unique_id() -> int:
    """A stable-but-unique positive int derived from a uuid for keying tests."""
    return uuid.uuid4().int % 10_000_000


@pytest.fixture
async def isolated_rate_limiter():
    """A RateLimiter wired to the same Redis as production but using unique ids
    per test, with cleanup of any keys it touched."""
    rl = RateLimiter()
    redis_client = await rl._get_redis()
    created_keys: list[str] = []

    original_eval = redis_client.eval
    original_zadd = redis_client.zadd

    async def tracking_eval(script, numkeys, *args, **kwargs):
        if numkeys >= 1:
            created_keys.append(args[0])
        return await original_eval(script, numkeys, *args, **kwargs)

    async def tracking_zadd(name, *args, **kwargs):
        created_keys.append(name)
        return await original_zadd(name, *args, **kwargs)

    redis_client.eval = tracking_eval  # type: ignore[assignment]
    redis_client.zadd = tracking_zadd  # type: ignore[assignment]

    yield rl

    redis_client.eval = original_eval  # type: ignore[assignment]
    redis_client.zadd = original_zadd  # type: ignore[assignment]
    if created_keys:
        await redis_client.delete(*set(created_keys))
    await rl.close()


# ---------------------------------------------------------------------------
# Rate limiter pool isolation
# ---------------------------------------------------------------------------


class TestRateLimiterFromNumberPoolIsolation:
    """The rate_limiter pool keys must include telephony_configuration_id."""

    @pytest.mark.skipif(
        "REDIS_URL" not in os.environ,
        reason="Requires Redis (set REDIS_URL via .env.test)",
    )
    @pytest.mark.asyncio
    async def test_acquire_only_returns_numbers_for_requested_config(
        self, isolated_rate_limiter
    ):
        rl = isolated_rate_limiter
        org_id = _unique_id()
        config_a = _unique_id()
        config_b = _unique_id()
        numbers_a = [f"+1555111{i:04d}" for i in range(3)]
        numbers_b = [f"+1555222{i:04d}" for i in range(3)]

        await rl.initialize_from_number_pool(
            org_id, numbers_a, telephony_configuration_id=config_a
        )
        await rl.initialize_from_number_pool(
            org_id, numbers_b, telephony_configuration_id=config_b
        )

        # Drain and cycle config_a's pool many times; the acquire should never
        # hand out a config_b number.
        seen: set[str] = set()
        for _ in range(20):
            n = await rl.acquire_from_number(
                org_id, telephony_configuration_id=config_a
            )
            if n is None:
                break
            seen.add(n)
            await rl.release_from_number(org_id, n, telephony_configuration_id=config_a)

        assert seen == set(numbers_a), (
            f"Expected only config_a numbers, but acquire returned: {seen}. "
            f"Cross-config leak: {seen - set(numbers_a)}"
        )

    @pytest.mark.skipif(
        "REDIS_URL" not in os.environ,
        reason="Requires Redis (set REDIS_URL via .env.test)",
    )
    @pytest.mark.asyncio
    async def test_release_returns_number_to_owning_config_pool(
        self, isolated_rate_limiter
    ):
        rl = isolated_rate_limiter
        org_id = _unique_id()
        config_a = _unique_id()
        config_b = _unique_id()
        numbers_a = ["+15551110001", "+15551110002"]
        numbers_b = ["+15552220001", "+15552220002"]

        await rl.initialize_from_number_pool(
            org_id, numbers_a, telephony_configuration_id=config_a
        )
        await rl.initialize_from_number_pool(
            org_id, numbers_b, telephony_configuration_id=config_b
        )

        # Acquire all of config_a's numbers (none released).
        first = await rl.acquire_from_number(
            org_id, telephony_configuration_id=config_a
        )
        second = await rl.acquire_from_number(
            org_id, telephony_configuration_id=config_a
        )
        assert {first, second} == set(numbers_a)

        # config_a is now exhausted — config_b is fully untouched.
        # Acquiring for config_a must return None, NOT spill into config_b.
        none_for_a = await rl.acquire_from_number(
            org_id, telephony_configuration_id=config_a
        )
        assert none_for_a is None, (
            f"Pool for config_a is exhausted but acquire returned {none_for_a} — "
            "this indicates a cross-config leak."
        )

        # config_b's pool is fully available.
        b_acquired = []
        for _ in range(2):
            n = await rl.acquire_from_number(
                org_id, telephony_configuration_id=config_b
            )
            assert n is not None
            b_acquired.append(n)
        assert set(b_acquired) == set(numbers_b)

    @pytest.mark.skipif(
        "REDIS_URL" not in os.environ,
        reason="Requires Redis (set REDIS_URL via .env.test)",
    )
    @pytest.mark.asyncio
    async def test_workflow_from_number_mapping_round_trips_config(
        self, isolated_rate_limiter
    ):
        """The mapping stored at dispatch must include the config so cleanup
        can release back to the correct pool."""
        rl = isolated_rate_limiter
        workflow_run_id = _unique_id()
        org_id = _unique_id()
        config_id = _unique_id()
        from_number = "+15553330001"

        await rl.store_workflow_from_number_mapping(
            workflow_run_id,
            org_id,
            from_number,
            telephony_configuration_id=config_id,
        )

        mapping = await rl.get_workflow_from_number_mapping(workflow_run_id)
        assert mapping is not None
        # Tuple shape: (org_id, from_number, telephony_configuration_id)
        assert len(mapping) == 3, (
            f"mapping must include telephony_configuration_id, got: {mapping}"
        )
        assert mapping == (org_id, from_number, config_id)

        await rl.delete_workflow_from_number_mapping(workflow_run_id)


# ---------------------------------------------------------------------------
# Dispatcher: must thread telephony_configuration_id end-to-end
# ---------------------------------------------------------------------------


def _make_campaign(
    *,
    organization_id: int,
    telephony_configuration_id: int,
    workflow_id: int = 1,
    campaign_id: int = 99,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=campaign_id,
        organization_id=organization_id,
        workflow_id=workflow_id,
        created_by=1,
        telephony_configuration_id=telephony_configuration_id,
        rate_limit_per_second=100,
        processed_rows=0,
        orchestrator_metadata={},
    )


def _make_queued_run(
    *,
    queued_run_id: int = 1,
    phone_number: str = "+15559990001",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=queued_run_id,
        source_uuid=f"src-{queued_run_id}",
        context_variables={"phone_number": phone_number},
    )


class TestDispatcherThreadsTelephonyConfig:
    """The dispatcher must pass telephony_configuration_id when acquiring,
    storing the mapping, and releasing the from_number."""

    @pytest.mark.asyncio
    async def test_dispatch_call_acquires_from_number_for_campaign_config(self):
        org_id = 7
        config_id = 4242
        campaign = _make_campaign(
            organization_id=org_id, telephony_configuration_id=config_id
        )
        queued_run = _make_queued_run()

        provider = MagicMock()
        provider.PROVIDER_NAME = "twilio"
        provider.WEBHOOK_ENDPOINT = "twilio/voice"
        provider.from_numbers = ["+15551110001"]
        provider.initiate_call = AsyncMock(
            return_value=SimpleNamespace(call_id="call-1", provider_metadata={})
        )

        workflow_run = SimpleNamespace(id=555, logs={})

        dispatcher = CampaignCallDispatcher()

        with (
            patch.object(
                dispatcher,
                "get_provider_for_campaign",
                AsyncMock(return_value=provider),
            ),
            patch(
                "api.services.campaign.campaign_call_dispatcher.db_client"
            ) as mock_db,
            patch(
                "api.services.campaign.campaign_call_dispatcher.rate_limiter"
            ) as mock_rl,
            patch(
                "api.services.campaign.campaign_call_dispatcher.get_backend_endpoints",
                AsyncMock(return_value=("https://example.com", None)),
            ),
        ):
            mock_db.get_workflow_by_id = AsyncMock(return_value=SimpleNamespace(id=1))
            mock_db.create_workflow_run = AsyncMock(return_value=workflow_run)
            mock_db.update_workflow_run = AsyncMock()

            mock_rl.acquire_from_number = AsyncMock(return_value="+15551110001")
            mock_rl.release_from_number = AsyncMock()
            mock_rl.release_concurrent_slot = AsyncMock()
            mock_rl.store_workflow_slot_mapping = AsyncMock()
            mock_rl.store_workflow_from_number_mapping = AsyncMock()

            await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-1")

            # acquire_from_number on rate_limiter must be called with the
            # campaign's telephony_configuration_id.
            assert mock_rl.acquire_from_number.await_count == 1
            call = mock_rl.acquire_from_number.await_args
            kwargs = call.kwargs
            args = call.args
            received_config = kwargs.get("telephony_configuration_id") or (
                args[1] if len(args) > 1 else None
            )
            assert received_config == config_id, (
                "dispatch_call must pass campaign.telephony_configuration_id "
                f"({config_id}) to rate_limiter.acquire_from_number, got "
                f"args={args}, kwargs={kwargs}"
            )

            # The workflow→from_number mapping must also remember the config so
            # the cleanup path can release back to the right pool.
            assert mock_rl.store_workflow_from_number_mapping.await_count == 1
            store_call = mock_rl.store_workflow_from_number_mapping.await_args
            store_kwargs = store_call.kwargs
            store_args = store_call.args
            stored_config = store_kwargs.get("telephony_configuration_id") or (
                store_args[3] if len(store_args) > 3 else None
            )
            assert stored_config == config_id, (
                "store_workflow_from_number_mapping must persist the "
                f"telephony_configuration_id ({config_id}); got args={store_args}, "
                f"kwargs={store_kwargs}"
            )

    @pytest.mark.asyncio
    async def test_release_call_slot_uses_stored_telephony_config(self):
        """When a call completes, release_call_slot must release the from_number
        to the same telephony config it was acquired from."""
        org_id = 7
        config_id = 4242
        from_number = "+15551110001"
        workflow_run_id = 555

        dispatcher = CampaignCallDispatcher()

        with patch(
            "api.services.campaign.campaign_call_dispatcher.rate_limiter"
        ) as mock_rl:
            mock_rl.get_workflow_slot_mapping = AsyncMock(return_value=None)
            mock_rl.get_workflow_from_number_mapping = AsyncMock(
                return_value=(org_id, from_number, config_id)
            )
            mock_rl.release_from_number = AsyncMock(return_value=True)
            mock_rl.delete_workflow_from_number_mapping = AsyncMock(return_value=True)

            await dispatcher.release_call_slot(workflow_run_id)

            assert mock_rl.release_from_number.await_count == 1
            call = mock_rl.release_from_number.await_args
            args = call.args
            kwargs = call.kwargs
            released_config = kwargs.get("telephony_configuration_id") or (
                args[2] if len(args) > 2 else None
            )
            assert released_config == config_id, (
                "release_call_slot must pass telephony_configuration_id "
                f"({config_id}) so the number is returned to its pool; got "
                f"args={args}, kwargs={kwargs}"
            )
