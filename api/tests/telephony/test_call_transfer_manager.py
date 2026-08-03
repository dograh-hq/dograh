"""Tests for CallTransferManager Redis-backed transfer-context lookup.

These tests verify (regression for issue #328):
1. Lookup by original_call_sid resolves via a secondary index, never an
   O(N) `KEYS transfer:context:*` keyspace scan.
2. A lookup for an unknown call sid returns None without scanning.
3. Removing a transfer context also clears its call-sid index entry.
"""

from typing import Dict, List

import pytest


class _FakePubSub:
    """Pub/sub double that never delivers — models the real hazard.

    A subscriber that arrives after the publish gets nothing from Redis pub/sub,
    so a wait that only listens would hang until timeout. Any event the test
    sees therefore had to come from the persisted copy.
    """

    async def subscribe(self, channel: str) -> None:
        return None

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def close(self) -> None:
        return None

    async def listen(self):
        import asyncio

        while True:
            await asyncio.sleep(3600)
            yield {}  # pragma: no cover


class _FakeRedis:
    """Minimal in-memory async Redis double.

    Counts calls to ``keys()`` so tests can assert the lookup path no longer
    performs an O(N) keyspace scan (the regression behind issue #328).
    """

    def __init__(self) -> None:
        self._store: Dict[str, str] = {}
        self.keys_call_count = 0
        self.published: List[tuple] = []

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value

    async def set(
        self, key: str, value: str, *, ex: int | None = None, nx: bool = False
    ):
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def get(self, key: str):
        return self._store.get(key)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 0

    def pubsub(self):
        return _FakePubSub()

    async def keys(self, pattern: str) -> List[str]:
        self.keys_call_count += 1
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return [k for k in self._store if k.startswith(prefix)]
        return [k for k in self._store if k == pattern]


def _build_context(transfer_id: str, original_call_sid: str):
    from api.services.telephony.transfer_event_protocol import TransferContext

    return TransferContext(
        transfer_id=transfer_id,
        call_sid="dest-call-sid",
        target_number="+15551230000",
        tool_uuid="tool-uuid",
        original_call_sid=original_call_sid,
        conference_name="conference-name",
        initiated_at=0.0,
    )


class TestFindTransferContextByCallSid:
    """Lookup must use the call-sid index, not a KEYS scan (issue #328)."""

    @pytest.mark.asyncio
    async def test_lookup_uses_index_and_not_keys_scan(self):
        from api.services.telephony.call_transfer_manager import CallTransferManager

        fake = _FakeRedis()
        manager = CallTransferManager(redis_client=fake)

        await manager.store_transfer_context(_build_context("tx-1", "caller-abc"))

        found = await manager.find_transfer_context_for_call("caller-abc")

        assert found is not None
        assert found.transfer_id == "tx-1"
        # Regression (issue #328): the lookup must resolve via the secondary
        # index, never an O(N) `KEYS transfer:context:*` keyspace scan.
        assert fake.keys_call_count == 0

    @pytest.mark.asyncio
    async def test_lookup_returns_none_for_unknown_call_sid(self):
        from api.services.telephony.call_transfer_manager import CallTransferManager

        fake = _FakeRedis()
        manager = CallTransferManager(redis_client=fake)

        await manager.store_transfer_context(_build_context("tx-1", "caller-abc"))

        found = await manager.find_transfer_context_for_call("not-a-caller")

        assert found is None
        assert fake.keys_call_count == 0

    @pytest.mark.asyncio
    async def test_remove_clears_call_sid_index(self):
        from api.services.telephony.call_transfer_manager import CallTransferManager

        fake = _FakeRedis()
        manager = CallTransferManager(redis_client=fake)

        await manager.store_transfer_context(_build_context("tx-1", "caller-abc"))
        await manager.remove_transfer_context("tx-1")

        found = await manager.find_transfer_context_for_call("caller-abc")

        assert found is None
        assert fake.keys_call_count == 0


@pytest.mark.asyncio
async def test_claim_transfer_step_is_atomic_and_idempotent():
    from api.services.telephony.call_transfer_manager import CallTransferManager

    manager = CallTransferManager(redis_client=_FakeRedis())

    assert await manager.claim_transfer_step("tx-1", "bridge_requested") is True
    assert await manager.claim_transfer_step("tx-1", "bridge_requested") is False
    assert await manager.claim_transfer_step("tx-1", "aleg_joined") is True


class TestTerminalEventDurability:
    """A completion published before the waiter subscribes must not be lost.

    Redis pub/sub keeps nothing for a subscriber that has not arrived yet, and a
    provider callback can land before the workflow calls
    ``wait_for_transfer_completion`` — the connector-driven providers answer in
    well under a second. Terminal events are therefore persisted before being
    published, and the waiter reads that copy right after subscribing.
    """

    @pytest.mark.asyncio
    async def test_completion_published_before_wait_is_still_delivered(self):
        from api.services.telephony.call_transfer_manager import CallTransferManager
        from api.services.telephony.transfer_event_protocol import (
            TransferEvent,
            TransferEventType,
        )

        fake = _FakeRedis()
        manager = CallTransferManager(redis_client=fake)

        # Callback wins the race: published with no subscriber listening yet.
        await manager.publish_transfer_event(
            TransferEvent(
                type=TransferEventType.DESTINATION_ANSWERED,
                transfer_id="xfer-1",
                original_call_sid="orig-1",
                transfer_call_sid="dest-1",
            )
        )

        event = await manager.wait_for_transfer_completion(
            "xfer-1", timeout_seconds=1.0
        )

        assert event is not None, "completion event lost: pub/sub has no retention"
        assert event.type == TransferEventType.DESTINATION_ANSWERED
        assert event.transfer_id == "xfer-1"

    @pytest.mark.asyncio
    async def test_terminal_event_is_persisted_before_publish(self):
        from api.services.telephony.call_transfer_manager import CallTransferManager
        from api.services.telephony.transfer_event_protocol import (
            TransferEvent,
            TransferEventType,
            TransferRedisChannels,
        )

        fake = _FakeRedis()
        manager = CallTransferManager(redis_client=fake)
        await manager.publish_transfer_event(
            TransferEvent(
                type=TransferEventType.TRANSFER_FAILED,
                transfer_id="xfer-2",
                original_call_sid="orig-2",
            )
        )
        stored = await fake.get(TransferRedisChannels.transfer_result_key("xfer-2"))
        assert stored is not None
        assert TransferEvent.from_json(stored).type == TransferEventType.TRANSFER_FAILED

    @pytest.mark.asyncio
    async def test_removing_context_clears_stored_result(self):
        from api.services.telephony.call_transfer_manager import CallTransferManager
        from api.services.telephony.transfer_event_protocol import (
            TransferEvent,
            TransferEventType,
            TransferRedisChannels,
        )

        fake = _FakeRedis()
        manager = CallTransferManager(redis_client=fake)
        await manager.store_transfer_context(_build_context("xfer-3", "orig-3"))
        await manager.publish_transfer_event(
            TransferEvent(
                type=TransferEventType.TRANSFER_FAILED,
                transfer_id="xfer-3",
                original_call_sid="orig-3",
            )
        )
        await manager.remove_transfer_context("xfer-3")
        assert (
            await fake.get(TransferRedisChannels.transfer_result_key("xfer-3")) is None
        )


class TestPersistenceFailureDoesNotDropLiveEvent:
    """A failed durable write must not suppress the live publish.

    Persistence only rescues a waiter that has not subscribed yet; a waiter that
    is already subscribed depends entirely on the publish. If a storage error
    skipped publishing, a healthy pub/sub would still time out the transfer.
    """

    @pytest.mark.asyncio
    async def test_publish_still_happens_when_persist_fails(self):
        from api.services.telephony.call_transfer_manager import CallTransferManager
        from api.services.telephony.transfer_event_protocol import (
            TransferEvent,
            TransferEventType,
            TransferRedisChannels,
        )

        class _FailingSetexRedis(_FakeRedis):
            async def setex(self, key: str, ttl: int, value: str) -> None:
                raise RuntimeError("redis write unavailable")

        fake = _FailingSetexRedis()
        manager = CallTransferManager(redis_client=fake)

        await manager.publish_transfer_event(
            TransferEvent(
                type=TransferEventType.DESTINATION_ANSWERED,
                transfer_id="xfer-4",
                original_call_sid="orig-4",
                transfer_call_sid="dest-4",
            )
        )

        channel = TransferRedisChannels.transfer_events("xfer-4")
        assert [c for c, _ in fake.published] == [channel], (
            "publish was skipped after a persistence failure — an already "
            "subscribed waiter would time out"
        )
