"""Authenticated access to the selected organization's cached speech."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from api.constants import REDIS_URL
from api.db.models import UserModel
from api.schemas.tts_cache import (
    TTSCacheInvalidation,
    TTSCacheList,
    TTSCacheOrder,
    TTSCacheSort,
)
from api.services.auth.depends import get_user_with_selected_organization
from api.services.pipecat.tts_cache.management import TTSCacheManager
from api.services.pipecat.tts_cache.redis import RedisCacheBackend

router = APIRouter(prefix="/tts-cache", tags=["tts-cache"])
EntryId = Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")]


async def get_cache_backend() -> AsyncIterator[RedisCacheBackend]:
    # Administrative reads can return a page of metadata or a WAV. Keep their
    # connection deadlines separate from the conversational path's 20 ms budget.
    client = Redis.from_url(
        REDIS_URL,
        decode_responses=False,
        socket_timeout=2,
        socket_connect_timeout=2,
        retry=Retry(NoBackoff(), 0),
        max_connections=2,
    )
    try:
        yield RedisCacheBackend(client)
    except RedisError as exc:
        raise HTTPException(
            status_code=503, detail="Speech cache is temporarily unavailable"
        ) from exc
    finally:
        await client.aclose()


def get_cache_manager(
    user: Annotated[UserModel, Depends(get_user_with_selected_organization)],
    backend: Annotated[RedisCacheBackend, Depends(get_cache_backend)],
) -> TTSCacheManager:
    return TTSCacheManager(backend, user.selected_organization_id)


@router.get("", response_model=TTSCacheList)
async def list_tts_cache(
    response: Response,
    manager: Annotated[TTSCacheManager, Depends(get_cache_manager)],
    search: Annotated[str, Query(max_length=200)] = "",
    sort: TTSCacheSort = "last_used",
    order: TTSCacheOrder = "desc",
    min_duration: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None,
    max_duration: Annotated[float | None, Query(ge=0, allow_inf_nan=False)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> TTSCacheList:
    if (
        min_duration is not None
        and max_duration is not None
        and min_duration > max_duration
    ):
        raise HTTPException(
            status_code=422, detail="Minimum duration must not exceed maximum duration"
        )
    response.headers["Cache-Control"] = "no-store"
    return await manager.list_entries(
        search=search,
        sort=sort,
        order=order,
        min_duration=min_duration,
        max_duration=max_duration,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/{entry_id}/audio",
    response_class=Response,
    responses={
        200: {
            "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}
        }
    },
)
async def preview_tts_cache(
    entry_id: EntryId,
    manager: Annotated[TTSCacheManager, Depends(get_cache_manager)],
) -> Response:
    try:
        audio = await manager.preview(entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if audio is None:
        raise HTTPException(
            status_code=404, detail="Cached audio expired or was removed"
        )
    return Response(
        content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"}
    )


@router.delete("/{entry_id}", response_model=TTSCacheInvalidation)
async def invalidate_tts_cache_entry(
    entry_id: EntryId,
    manager: Annotated[TTSCacheManager, Depends(get_cache_manager)],
) -> TTSCacheInvalidation:
    # Idempotent: expiration and eviction may race a user's invalidation.
    return TTSCacheInvalidation(removed=int(await manager.delete_entry(entry_id)))


@router.delete("", response_model=TTSCacheInvalidation)
async def clear_tts_cache(
    manager: Annotated[TTSCacheManager, Depends(get_cache_manager)],
) -> TTSCacheInvalidation:
    return TTSCacheInvalidation(removed=await manager.clear())
