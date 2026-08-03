from unittest.mock import AsyncMock

import pytest

from api.services.call_concurrency.rate_limiter import RateLimiter
from api.services.workflow.embed_chat_limiter import (
    EMBED_CHAT_INIT_RATE_LIMIT_PER_SECOND,
    EMBED_CHAT_MESSAGE_RATE_LIMIT_PER_SECOND,
    allow_embed_chat_init,
    allow_embed_chat_message,
)


@pytest.mark.asyncio
async def test_scoped_rate_bucket_does_not_use_org_concurrency_counters():
    limiter = RateLimiter()
    redis = AsyncMock()
    redis.eval.return_value = 1
    limiter._get_redis = AsyncMock(return_value=redis)

    assert await limiter.acquire_token(
        11,
        5,
        scope_key="public_embed_chat:init:42",
    )

    args = redis.eval.await_args.args
    assert args[2] == "rate_limit:public_embed_chat:init:42"
    assert "concurrent_calls" not in args[2]


@pytest.mark.asyncio
async def test_embed_chat_limiters_use_separate_init_and_session_buckets(monkeypatch):
    acquire_token = AsyncMock(side_effect=[True, True])
    monkeypatch.setattr(
        "api.services.workflow.embed_chat_limiter.rate_limiter.acquire_token",
        acquire_token,
    )

    assert await allow_embed_chat_init(42)
    assert await allow_embed_chat_message(501)

    assert acquire_token.await_args_list[0].args == (
        42,
        EMBED_CHAT_INIT_RATE_LIMIT_PER_SECOND,
    )
    assert acquire_token.await_args_list[0].kwargs == {
        "scope_key": "public_embed_chat:init:42"
    }
    assert acquire_token.await_args_list[1].args == (
        501,
        EMBED_CHAT_MESSAGE_RATE_LIMIT_PER_SECOND,
    )
    assert acquire_token.await_args_list[1].kwargs == {
        "scope_key": "public_embed_chat:messages:501"
    }
