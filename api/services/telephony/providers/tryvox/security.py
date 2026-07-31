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
    ) -> str:
        """Return the existing token for a run or atomically create one."""
        redis = await self._get_redis()
        key = self._stream_key(workflow_id, organization_id, workflow_run_id)

        existing = await redis.get(key)
        if existing:
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
                return str(existing)

        raise RuntimeError("Unable to issue TryVox stream capability")

    async def redeem_stream_token(
        self,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        supplied_token: str,
    ) -> bool:
        """Atomically compare and consume a run-bound stream capability."""
        if not supplied_token:
            return False

        redis = await self._get_redis()
        script = """
        local stored = redis.call('GET', KEYS[1])
        if not stored or stored ~= ARGV[1] then
            return 0
        end
        redis.call('DEL', KEYS[1])
        return 1
        """
        result = await redis.eval(
            script,
            1,
            self._stream_key(workflow_id, organization_id, workflow_run_id),
            supplied_token,
        )
        return bool(result)

    async def claim_callback(
        self, account_id: str, timestamp: str, raw_body: str
    ) -> bool:
        """Atomically claim a signed callback payload for its replay window."""
        digest = hashlib.sha256(
            f"{account_id}.{timestamp}.{raw_body}".encode()
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
