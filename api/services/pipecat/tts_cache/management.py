"""Browse and preview the cache without changing synthesis usage or idle expiry."""

import io
import json
import wave

from pydantic import ValidationError

from api.schemas.tts_cache import (
    TTSCacheEntry,
    TTSCacheList,
    TTSCacheOrder,
    TTSCacheSort,
)
from api.services.pipecat.tts_cache.models import (
    CachedSpeech,
    CachePolicy,
    SynthesisRequest,
)
from api.services.pipecat.tts_cache.redis import RedisCacheBackend


class TTSCacheManager:
    def __init__(self, backend: RedisCacheBackend, organization_id: int):
        if type(organization_id) is not int or organization_id <= 0:
            raise ValueError("Organization ID is required")
        self.backend = backend
        self.organization_id = organization_id
        self.policy = CachePolicy()

    async def list_entries(
        self,
        *,
        search: str,
        sort: TTSCacheSort,
        order: TTSCacheOrder,
        min_duration: float | None,
        max_duration: float | None,
        offset: int,
        limit: int,
    ) -> TTSCacheList:
        rows = await self.backend.list_entries(
            self.organization_id, self.policy.max_entries_per_org
        )
        entries = []
        needle = search.strip().casefold()
        for row in rows:
            try:
                entry = TTSCacheEntry.model_validate(row)
            except ValidationError:
                continue
            searchable = (
                f"{entry.text_preview} {entry.provider} {entry.model} {entry.voice_id}"
            )
            if needle and needle not in searchable.casefold():
                continue
            if min_duration is not None and entry.duration_seconds < min_duration:
                continue
            if max_duration is not None and entry.duration_seconds > max_duration:
                continue
            entries.append(entry)
        field = {
            "last_used": "last_used_at",
            "duration": "duration_seconds",
            "usage": "hit_count",
        }[sort]
        entries.sort(
            key=lambda entry: (getattr(entry, field), entry.id), reverse=order == "desc"
        )
        return TTSCacheList(
            entries=entries[offset : offset + limit], total=len(entries)
        )

    async def preview(self, digest: str) -> bytes | None:
        result = await self.backend.preview(
            self.organization_id, digest, self.policy.max_entry_bytes + 1028
        )
        if not result:
            return None
        value, details = result
        try:
            metadata = json.loads(details)
            request = SynthesisRequest(
                self.organization_id,
                metadata["provider"],
                digest,
                metadata["sample_rate"],
                metadata["channels"],
            )
            if (
                type(request.sample_rate) is not int
                or not 0 < request.sample_rate <= 192000
                or request.channels != 1
            ):
                raise ValueError("Invalid audio format")
            speech = CachedSpeech.decode(value, request, self.policy)
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(
                "This cached audio is invalid. Invalidate it to regenerate on the next request."
            ) from exc
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(speech.channels)
            wav.setsampwidth(2)
            wav.setframerate(speech.sample_rate)
            wav.writeframes(speech.audio)
        return output.getvalue()

    async def delete_entry(self, digest: str) -> bool:
        return await self.backend.delete_entry(self.organization_id, digest)

    async def clear(self) -> int:
        return await self.backend.invalidate_organization(self.organization_id)
