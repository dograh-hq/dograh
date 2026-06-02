"""Tests for CallTransferManager Redis-backed transfer-context lookup.

These tests verify (regression for issue #328):
1. Lookup by original_call_sid resolves via a secondary index, never an
   O(N) `KEYS transfer:context:*` keyspace scan.
2. A lookup for an unknown call sid returns None without scanning.
3. Removing a transfer context also clears its call-sid index entry.
"""

from typing import Dict, List

import pytest


class _FakeRedis:
    """Minimal in-memory async Redis double.

    Counts calls to ``keys()`` so tests can assert the lookup path no longer
    performs an O(N) keyspace scan (the regression behind issue #328).
    """

    def __init__(self) -> None:
        self._store: Dict[str, str] = {}
        self.keys_call_count = 0

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str):
        return self._store.get(key)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)

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
