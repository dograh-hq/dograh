"""Moss indexes loaded into this worker process.

Each index downloads once per process and stays in memory,
so every search runs locally without a network call. The key
includes the project key, so a rotated key loads a fresh copy.
An index no workflow has used for an hour is unloaded on the next lookup
and loads again when a search needs it, so a rotated key or a deleted
workflow does not hold memory for good.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

# Polls the Moss cloud for a newer index version. Matches the SDK default.
_REFRESH_INTERVAL_SECONDS = 600

# An index unused for this long is unloaded on the next lookup.
_IDLE_SECONDS = 3600


def _new_client(project_id: str, project_key: str) -> Any:
    try:
        from moss import MossClient
    except ImportError as exc:
        raise RuntimeError(
            "The moss package is not installed. Install it with `pip install moss`."
        ) from exc
    return MossClient(project_id, project_key)


class MossIndex:
    """One Moss index, downloaded once and searched in memory."""

    def __init__(self, project_id: str, project_key: str, name: str) -> None:
        self.name = name
        self._project_id = project_id
        self._project_key = project_key
        self._client: Any = None
        self._loading: asyncio.Task | None = None
        self.last_used = time.monotonic()

    def start_loading(self) -> None:
        """Start the download in the background so the first search does not wait."""
        if self._client is not None or self._loading is not None:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._begin_load()

    def loaded_client(self) -> Any:
        """Return the client if the index is loaded, or None without waiting."""
        self.last_used = time.monotonic()
        if self._client is None:
            self.start_loading()
        return self._client

    async def client(self) -> Any:
        """Return the loaded client, waiting for the download if it is in flight."""
        self.last_used = time.monotonic()
        if self._client is not None:
            return self._client
        loading = self._loading or self._begin_load()
        # Shielded so a cancelled tool call does not abort a shared download.
        return await asyncio.shield(loading)

    def unload(self) -> None:
        """Free the index in the background. A later search loads it again."""
        if self._client is None:
            return
        holder = [self._client]
        self._client = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(_release(holder, self.name))
        _releasing.add(task)
        task.add_done_callback(_releasing.discard)

    def _begin_load(self) -> asyncio.Task:
        self._loading = asyncio.get_running_loop().create_task(self._load())
        self._loading.add_done_callback(self._on_loaded)
        return self._loading

    async def _load(self) -> Any:
        # The first `moss` import and client setup run off the event loop.
        client = await asyncio.to_thread(
            _new_client, self._project_id, self._project_key
        )
        await client.load_index(
            self.name,
            auto_refresh=True,
            polling_interval_in_seconds=_REFRESH_INTERVAL_SECONDS,
        )
        return client

    def _on_loaded(self, task: asyncio.Task) -> None:
        self._loading = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"Moss index '{self.name}' failed to load: {exc}")
            return
        self._client = task.result()
        logger.info(f"Moss index '{self.name}' loaded")


async def _release(holder: list[Any], name: str) -> None:
    try:
        await holder[0].unload_index(name)
    except Exception as exc:
        logger.warning(f"Moss index '{name}' failed to unload: {exc}")
    # The client flushes usage over the network when it is dropped,
    # so the last reference goes away off the event loop.
    await asyncio.to_thread(holder.clear)
    logger.info(
        f"Moss index '{name}' unloaded after {_IDLE_SECONDS // 60} idle minutes"
    )


_indexes: dict[tuple[str, str, str], MossIndex] = {}
_releasing: set[asyncio.Task] = set()


def get_index(project_id: str, project_key: str, name: str) -> MossIndex:
    now = time.monotonic()
    # The entry stays, so a later search reloads this same index instead of a copy.
    for idle in _indexes.values():
        if now - idle.last_used > _IDLE_SECONDS and idle._loading is None:
            idle.unload()

    key = (project_id, project_key, name)
    index = _indexes.get(key)
    if index is None:
        index = _indexes[key] = MossIndex(project_id, project_key, name)
    index.last_used = now
    return index
