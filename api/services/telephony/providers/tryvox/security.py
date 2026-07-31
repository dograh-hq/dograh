"""Short-lived, Redis-backed security state for TryVox callbacks and streams."""

from __future__ import annotations

import hashlib
import secrets

import redis.asyncio as aioredis

from api.constants import REDIS_URL


class TryVoxSecurity:
    """Issue one-shot stream capabilities and deduplicate signed callbacks."""

    STREAM_TOKEN_TTL_SECONDS = 600
    CALLBACK_REPLAY_TTL_SECONDS = 300
    CONSUMED_STREAM_TOKEN = "__consumed__"
    RESERVED_STREAM_TOKEN_PREFIX = "__reserved__:"

    def __init__(self, redis_client: aioredis.Redis | None = None):
        self._redis_client = redis_client

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis_client is None:
            self._redis_client = await aioredis.from_url(
                REDIS_URL, decode_responses=True
            )
        return self._redis_client

    @staticmethod
    def _stream_key(
        workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        return (
            "tryvox:stream-capability:"
            f"{workflow_id}:{organization_id}:{workflow_run_id}"
        )

    async def issue_stream_token(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str | None:
        """Return a run's active token, without reopening a consumed capability."""
        redis = await self._get_redis()
        key = self._stream_key(workflow_id, organization_id, workflow_run_id)

        existing = await redis.get(key)
        if existing:
            if existing == self.CONSUMED_STREAM_TOKEN or str(existing).startswith(
                self.RESERVED_STREAM_TOKEN_PREFIX
            ):
                return None
            return str(existing)

        for _ in range(2):
            token = secrets.token_urlsafe(32)
            created = await redis.set(
                key,
                token,
                ex=self.STREAM_TOKEN_TTL_SECONDS,
                nx=True,
            )
            if created:
                return token
            existing = await redis.get(key)
            if existing:
                if existing == self.CONSUMED_STREAM_TOKEN or str(existing).startswith(
                    self.RESERVED_STREAM_TOKEN_PREFIX
                ):
                    return None
                return str(existing)

        raise RuntimeError("Unable to issue TryVox stream capability")

    async def reserve_stream_token(
        self,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        supplied_token: str,
    ) -> str | None:
        """Atomically reserve a capability while the WebSocket is accepted."""
        if not supplied_token:
            return None

        redis = await self._get_redis()
        reservation = secrets.token_urlsafe(32)
        script = """
        local stored = redis.call('GET', KEYS[1])
        if not stored or stored == ARGV[2] or
           string.sub(stored, 1, string.len(ARGV[3])) == ARGV[3] or
           stored ~= ARGV[1] then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[3] .. ARGV[4], 'KEEPTTL')
        return 1
        """
        result = await redis.eval(
            script,
            1,
            self._stream_key(workflow_id, organization_id, workflow_run_id),
            supplied_token,
            self.CONSUMED_STREAM_TOKEN,
            self.RESERVED_STREAM_TOKEN_PREFIX,
            reservation,
        )
        return reservation if result else None

    async def consume_stream_token(
        self,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        reservation: str,
    ) -> bool:
        """Consume a capability owned by this WebSocket reservation."""
        redis = await self._get_redis()
        script = """
        local expected = ARGV[1] .. ARGV[2]
        if redis.call('GET', KEYS[1]) ~= expected then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[3], 'KEEPTTL')
        return 1
        """
        result = await redis.eval(
            script,
            1,
            self._stream_key(workflow_id, organization_id, workflow_run_id),
            self.RESERVED_STREAM_TOKEN_PREFIX,
            reservation,
            self.CONSUMED_STREAM_TOKEN,
        )
        return bool(result)

    async def release_stream_token(
        self,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        reservation: str,
        supplied_token: str,
    ) -> bool:
        """Restore a reservation only when its WebSocket was not accepted."""
        redis = await self._get_redis()
        script = """
        local expected = ARGV[1] .. ARGV[2]
        if redis.call('GET', KEYS[1]) ~= expected then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[3], 'KEEPTTL')
        return 1
        """
        result = await redis.eval(
            script,
            1,
            self._stream_key(workflow_id, organization_id, workflow_run_id),
            self.RESERVED_STREAM_TOKEN_PREFIX,
            reservation,
            supplied_token,
        )
        return bool(result)

    async def claim_callback(
        self,
        account_id: str,
        workflow_run_id: int,
        timestamp: str,
        raw_body: str,
    ) -> bool:
        """Atomically claim a signed, run-bound callback for its replay window."""
        digest = hashlib.sha256(
            f"{account_id}.{workflow_run_id}.{timestamp}.{raw_body}".encode()
        ).hexdigest()
        redis = await self._get_redis()
        return bool(
            await redis.set(
                f"tryvox:callback:{digest}",
                "1",
                ex=self.CALLBACK_REPLAY_TTL_SECONDS,
                nx=True,
            )
        )


tryvox_security = TryVoxSecurity()


__all__ = ["TryVoxSecurity", "tryvox_security"]
