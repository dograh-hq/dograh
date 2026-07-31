"""Short-lived, Redis-backed security state for TryVox callbacks and streams."""

from __future__ import annotations

import hashlib
import secrets
import time

import redis.asyncio as aioredis

from api.constants import REDIS_URL


class TryVoxSecurity:
    """Issue one-shot stream capabilities and deduplicate signed callbacks."""

    STREAM_TOKEN_TTL_SECONDS = 600
    STREAM_RESERVATION_TTL_SECONDS = STREAM_TOKEN_TTL_SECONDS
    CALLBACK_REPLAY_TTL_SECONDS = 300
    CONSUMED_STREAM_TOKEN_PREFIX = "__consumed__:"
    RESERVED_STREAM_TOKEN_PREFIX = "__reserved__:"
    CALLBACK_PROCESSING_PREFIX = "__processing__:"
    CALLBACK_COMPLETED = "__completed__"

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
            if str(existing).startswith(
                (
                    self.CONSUMED_STREAM_TOKEN_PREFIX,
                    self.RESERVED_STREAM_TOKEN_PREFIX,
                )
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
                if str(existing).startswith(
                    (
                        self.CONSUMED_STREAM_TOKEN_PREFIX,
                        self.RESERVED_STREAM_TOKEN_PREFIX,
                    )
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
        reservation = (
            f"{self.RESERVED_STREAM_TOKEN_PREFIX}"
            f"{int(time.time()) + self.STREAM_RESERVATION_TTL_SECONDS}:"
            f"{supplied_token}:{secrets.token_urlsafe(32)}"
        )
        script = """
        local stored = redis.call('GET', KEYS[1])
        if not stored or string.sub(stored, 1, string.len(ARGV[2])) == ARGV[2] then
            return 0
        end
        if string.sub(stored, 1, string.len(ARGV[3])) == ARGV[3] then
            local separator = string.find(stored, ':', string.len(ARGV[3]) + 1)
            local token_end = separator and string.find(stored, ':', separator + 1)
            local expires_at = separator and tonumber(
                string.sub(stored, string.len(ARGV[3]) + 1, separator - 1)
            )
            if not token_end or not expires_at or expires_at > tonumber(ARGV[4]) or
               string.sub(stored, separator + 1, token_end - 1) ~= ARGV[1] then
                return 0
            end
        elseif stored ~= ARGV[1] then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[5], 'KEEPTTL')
        return 1
        """
        result = await redis.eval(
            script,
            1,
            self._stream_key(workflow_id, organization_id, workflow_run_id),
            supplied_token,
            self.CONSUMED_STREAM_TOKEN_PREFIX,
            self.RESERVED_STREAM_TOKEN_PREFIX,
            int(time.time()),
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
        consumed = (
            self.CONSUMED_STREAM_TOKEN_PREFIX
            + hashlib.sha256(reservation.encode()).hexdigest()
        )
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
        return 1
        """
        result = await redis.eval(
            script,
            1,
            self._stream_key(workflow_id, organization_id, workflow_run_id),
            reservation,
            consumed,
        )
        return bool(result)

    async def rollback_consumed_stream_token(
        self,
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
        reservation: str,
        supplied_token: str,
    ) -> bool:
        """Restore a just-consumed capability when run startup fails."""
        redis = await self._get_redis()
        consumed = (
            self.CONSUMED_STREAM_TOKEN_PREFIX
            + hashlib.sha256(reservation.encode()).hexdigest()
        )
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
        return 1
        """
        result = await redis.eval(
            script,
            1,
            self._stream_key(workflow_id, organization_id, workflow_run_id),
            consumed,
            supplied_token,
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
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[2], 'KEEPTTL')
        return 1
        """
        result = await redis.eval(
            script,
            1,
            self._stream_key(workflow_id, organization_id, workflow_run_id),
            reservation,
            supplied_token,
        )
        return bool(result)

    @staticmethod
    def _callback_key(
        account_id: str,
        callback_type: str,
        workflow_run_id: int,
        timestamp: str,
        raw_body: str,
    ) -> str:
        digest = hashlib.sha256(
            (
                f"{account_id}.{callback_type}.{workflow_run_id}."
                f"{timestamp}.{raw_body}"
            ).encode()
        ).hexdigest()
        return f"tryvox:callback:{digest}"

    async def reserve_callback(
        self,
        account_id: str,
        callback_type: str,
        workflow_run_id: int,
        timestamp: str,
        raw_body: str,
    ) -> tuple[str, str | None]:
        """Reserve callback processing or report its existing state."""
        redis = await self._get_redis()
        owner = secrets.token_urlsafe(32)
        processing = f"{self.CALLBACK_PROCESSING_PREFIX}{owner}"
        script = """
        local stored = redis.call('GET', KEYS[1])
        if not stored then
            redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
            return 1
        end
        if stored == ARGV[2] then
            return 2
        end
        return 0
        """
        result = int(
            await redis.eval(
                script,
                1,
                self._callback_key(
                    account_id,
                    callback_type,
                    workflow_run_id,
                    timestamp,
                    raw_body,
                ),
                processing,
                self.CALLBACK_COMPLETED,
                self.CALLBACK_REPLAY_TTL_SECONDS,
            )
        )
        if result == 1:
            return "acquired", owner
        if result == 2:
            return "completed", None
        return "in_progress", None

    async def finalize_callback(
        self,
        account_id: str,
        callback_type: str,
        workflow_run_id: int,
        timestamp: str,
        raw_body: str,
        owner: str,
    ) -> bool:
        """Finalize only the callback reservation owned by this worker."""
        redis = await self._get_redis()
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
        return 1
        """
        return bool(
            await redis.eval(
                script,
                1,
                self._callback_key(
                    account_id,
                    callback_type,
                    workflow_run_id,
                    timestamp,
                    raw_body,
                ),
                f"{self.CALLBACK_PROCESSING_PREFIX}{owner}",
                self.CALLBACK_COMPLETED,
                self.CALLBACK_REPLAY_TTL_SECONDS,
            )
        )

    async def release_callback(
        self,
        account_id: str,
        callback_type: str,
        workflow_run_id: int,
        timestamp: str,
        raw_body: str,
        owner: str,
    ) -> bool:
        """Release only the callback reservation owned by this worker."""
        redis = await self._get_redis()
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call('DEL', KEYS[1])
        return 1
        """
        return bool(
            await redis.eval(
                script,
                1,
                self._callback_key(
                    account_id,
                    callback_type,
                    workflow_run_id,
                    timestamp,
                    raw_body,
                ),
                f"{self.CALLBACK_PROCESSING_PREFIX}{owner}",
            )
        )


tryvox_security = TryVoxSecurity()


__all__ = ["TryVoxSecurity", "tryvox_security"]
