"""Exercise authenticated cache management against real, isolated Redis keys."""

import io
import os
import uuid
import wave
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from api.routes.tts_cache import get_cache_backend, router
from api.services.auth.depends import get_user
from api.services.pipecat.tts_cache.models import (
    CachedSpeech,
    CachePolicy,
    SynthesisRequest,
)
from api.services.pipecat.tts_cache.redis import RedisCacheBackend

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def cache_api():
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=False)
    prefix = f"test:tts:routes:{uuid.uuid4().hex}"
    backend = RedisCacheBackend(redis, prefix=prefix)
    app = FastAPI()
    app.include_router(router)
    user = SimpleNamespace(selected_organization_id=1)
    app.dependency_overrides[get_user] = lambda: user
    app.dependency_overrides[get_cache_backend] = lambda: backend
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield SimpleNamespace(client=client, backend=backend, user=user, app=app)
    finally:
        keys = [key async for key in redis.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()


def request(digit="a", organization_id=1, text="Hello"):
    return SynthesisRequest(
        organization_id, "minimax", digit * 64, 16000, 1, text, "speech", "voice"
    )


async def seed(api, req, duration=1):
    speech = CachedSpeech(b"\x01\x02" * (16000 * duration), 16000)
    for _ in range(3):
        token = uuid.uuid4().hex
        assert (await api.backend.claim_candidate(req, token, CachePolicy()))[0]
        await api.backend.put(req, speech.encode(), CachePolicy(), token=token)
    return speech


async def test_list_sort_filter_and_paginate_with_org_isolation(cache_api):
    api = cache_api
    a, b, c = request(), request("b", text="Goodbye"), request("c", text="Hello again")
    await seed(api, a, 1)
    await seed(api, b, 3)
    await seed(api, c, 2)
    await seed(
        api, request("d", organization_id=2, text="Other organization's phrase"), 5
    )
    for _ in range(3):
        await api.backend.get(a, 1049604, 86400)
    await api.backend.get(c, 1049604, 86400)

    response = await api.client.get("/tts-cache", params={"sort": "usage"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["total"] == 3
    assert [e["id"] for e in response.json()["entries"]] == [
        a.digest,
        c.digest,
        b.digest,
    ]
    assert [e["hit_count"] for e in response.json()["entries"]] == [3, 1, 0]
    response = await api.client.get(
        "/tts-cache", params={"sort": "duration", "offset": 1, "limit": 1}
    )
    assert response.json()["total"] == 3
    assert response.json()["entries"][0]["id"] == c.digest
    response = await api.client.get(
        "/tts-cache",
        params={"search": "HELLO", "min_duration": 1.5, "max_duration": 2.5},
    )
    assert [e["id"] for e in response.json()["entries"]] == [c.digest]


async def test_preview_is_wav_and_does_not_count_usage_or_renew_ttl(cache_api):
    api = cache_api
    req = request()
    speech = await seed(api, req)
    key = api.backend._key(req)
    before = await api.backend.client.hgetall(f"{key}:meta")
    expiry = await api.backend.client.pexpiretime(key)
    response = await api.client.get(f"/tts-cache/{req.digest}/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "no-store"
    with wave.open(io.BytesIO(response.content), "rb") as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (
            16000,
            1,
            2,
        )
        assert wav.readframes(wav.getnframes()) == speech.audio
    await api.client.get("/tts-cache")
    assert await api.backend.client.hgetall(f"{key}:meta") == before
    assert await api.backend.client.pexpiretime(key) == expiry


async def test_audio_and_invalidation_cannot_reach_other_organizations(cache_api):
    api = cache_api
    req = request(organization_id=2)
    await seed(api, req)
    response = await api.client.get(
        f"/tts-cache/{req.digest}/audio", params={"organization_id": 2}
    )
    assert response.status_code == 404
    response = await api.client.delete(
        f"/tts-cache/{req.digest}", params={"organization_id": 2}
    )
    assert response.json() == {"removed": 0}
    assert await api.backend.client.exists(api.backend._key(req))
    # The same content hash can exist in both organizations.
    await seed(api, replace(req, organization_id=1))
    response = await api.client.delete(f"/tts-cache/{req.digest}")
    assert response.json() == {"removed": 1}
    assert await api.backend.client.exists(api.backend._key(req))
    response = await api.client.delete("/tts-cache")
    assert response.json() == {"removed": 0}
    api.user.selected_organization_id = 2
    response = await api.client.delete("/tts-cache")
    assert response.json() == {"removed": 1}
    assert not await api.backend.client.exists(
        api.backend._key(req), f"{api.backend._key(req)}:meta"
    )


async def test_expired_entries_disappear_and_invalid_audio_can_be_removed(cache_api):
    api = cache_api
    req = request()
    await seed(api, req)
    await api.backend.client.set(api.backend._key(req), b"bad", ex=60)
    assert (await api.client.get(f"/tts-cache/{req.digest}/audio")).status_code == 409
    assert (await api.client.delete(f"/tts-cache/{req.digest}")).json() == {
        "removed": 1
    }
    await seed(api, req)
    await api.backend.client.pexpire(api.backend._key(req), 0)
    assert (await api.client.get("/tts-cache")).json() == {"entries": [], "total": 0}
    assert (await api.client.get(f"/tts-cache/{req.digest}/audio")).status_code == 404


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/tts-cache"),
        ("DELETE", "/tts-cache"),
        ("GET", f"/tts-cache/{'a' * 64}/audio"),
        ("DELETE", f"/tts-cache/{'a' * 64}"),
    ],
)
async def test_all_cache_routes_require_auth(cache_api, method, path):
    def unauthenticated():
        raise HTTPException(status_code=401)

    cache_api.app.dependency_overrides[get_user] = unauthenticated
    assert (await cache_api.client.request(method, path)).status_code == 401


async def test_management_requires_selected_org_and_valid_parameters(cache_api):
    api = cache_api
    api.user.selected_organization_id = None
    assert (await api.client.get("/tts-cache")).status_code == 400
    api.user.selected_organization_id = 1
    for query in (
        {"limit": 10000},
        {"min_duration": 3, "max_duration": 1},
        {"sort": "anything"},
    ):
        assert (await api.client.get("/tts-cache", params=query)).status_code == 422
    assert (await api.client.delete("/tts-cache/not-a-digest")).status_code == 422
