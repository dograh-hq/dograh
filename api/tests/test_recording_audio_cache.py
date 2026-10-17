"""Recording cache identity, reuse, invalidation and concurrent fills."""

import asyncio
import os
import threading
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from weakref import WeakValueDictionary

import pytest

from api.db import db_client
from api.services import storage
from api.services.pipecat import audio_file_cache
from api.services.pipecat import recording_audio_cache as cache
from api.services.worker_sync.protocol import WorkerSyncEvent, WorkerSyncEventType


def recording(pk=42, *, org=1, name="welcome", transcript="Hello"):
    return SimpleNamespace(
        id=pk,
        organization_id=org,
        recording_id=name,
        transcript=transcript,
        storage_key=f"recordings/{org}/{pk}.wav",
        storage_backend="s3",
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
        is_active=True,
        audio=b"\x10\x27" * 80,
        workflow_id=None,
        tts_provider=None,
        tts_model=None,
        tts_voice_id=None,
        recording_metadata={},
        created_by=1,
    )


@pytest.fixture
def recordings(monkeypatch, tmp_path):
    rows = [recording()]
    monkeypatch.setattr(cache, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(audio_file_cache, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(cache, "_metadata_cache", OrderedDict())
    monkeypatch.setattr(cache, "_metadata_invalidations", WeakValueDictionary())
    monkeypatch.setattr(cache, "_download_locks", WeakValueDictionary())

    async def lookup(identifier, org, *, by_pk):
        return next(
            (
                r
                for r in rows
                if r.organization_id == org
                and r.is_active
                and (r.id if by_pk else r.recording_id) == identifier
            ),
            None,
        )

    async def lookup_pk(pk, org):
        return await lookup(pk, org, by_pk=True)

    async def lookup_name(name, org):
        return await lookup(name, org, by_pk=False)

    by_pk = AsyncMock(side_effect=lookup_pk)
    by_name = AsyncMock(side_effect=lookup_name)
    listing = AsyncMock(
        side_effect=lambda organization_id: [
            r for r in rows if r.organization_id == organization_id and r.is_active
        ]
    )
    monkeypatch.setattr(db_client, "get_recording_by_id", by_pk)
    monkeypatch.setattr(db_client, "get_recording_by_recording_id", by_name)
    monkeypatch.setattr(db_client, "get_recordings", listing)
    storage_factory = Mock(return_value=object())
    monkeypatch.setattr(storage, "get_storage_for_backend", storage_factory)

    async def download(key, backend, get_storage):
        get_storage(backend)
        path = tmp_path / f"download-{download_mock.await_count}.wav"
        path.write_text(key)
        return str(path)

    async def convert(path, sample_rate, output_format):
        key = Path(path).read_text()
        return next(r.audio for r in rows if r.storage_key == key)

    download_mock = AsyncMock(side_effect=download)
    convert_mock = AsyncMock(side_effect=convert)
    monkeypatch.setattr(cache, "download_storage_file", download_mock)
    monkeypatch.setattr(cache, "convert_audio_file", convert_mock)
    return SimpleNamespace(
        rows=rows,
        by_pk=by_pk,
        by_name=by_name,
        listing=listing,
        download=download_mock,
        convert=convert_mock,
        storage=storage_factory,
        path=tmp_path,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("first_lookup", ["pk", "name"])
@pytest.mark.parametrize("transcript", ["Hello", ""])
async def test_fetchers_share_pcm_and_transcript_across_calls(
    recordings, first_lookup, transcript
):
    row = recordings.rows[0]
    row.transcript = transcript
    first = (
        {"recording_pk": row.id}
        if first_lookup == "pk"
        else {"recording_id": row.recording_id}
    )
    expected = cache.RecordingAudio(row.audio, transcript or None)
    assert await cache.create_recording_audio_fetcher(1, 8000)(**first) == expected
    assert (
        await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=row.id)
        == expected
    )
    assert (
        await cache.create_recording_audio_fetcher(1, 8000)(
            recording_id=row.recording_id
        )
        == expected
    )
    assert recordings.by_pk.await_count + recordings.by_name.await_count == 1
    recordings.download.assert_awaited_once()
    recordings.convert.assert_awaited_once()
    recordings.storage.assert_called_once()
    assert Path(cache._cache_path(1, row.id, 8000)).read_bytes() == row.audio
    assert not list(recordings.path.glob("download-*.wav"))


@pytest.mark.asyncio
async def test_warming_populates_the_same_files_and_metadata_as_fetch(recordings):
    await cache.warm_recording_cache(1, 8000)
    recordings.download.reset_mock()
    recordings.convert.reset_mock()
    recordings.storage.reset_mock()
    assert await cache.create_recording_audio_fetcher(1, 8000)(
        recording_pk=42
    ) == cache.RecordingAudio(recordings.rows[0].audio, "Hello")
    await cache.warm_recording_cache(1, 8000)
    recordings.by_pk.assert_not_awaited()
    recordings.by_name.assert_not_awaited()
    recordings.download.assert_not_awaited()
    recordings.convert.assert_not_awaited()
    recordings.storage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("warm", [False, True])
async def test_legacy_file_is_migrated_without_download(recordings, warm):
    row = recordings.rows[0]
    legacy = recordings.path / f"1_{row.recording_id}_8000.pcm"
    legacy.write_bytes(row.audio)
    if warm:
        await cache.warm_recording_cache(1, 8000)
    result = await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=row.id)
    assert result == cache.RecordingAudio(row.audio, "Hello")
    assert not legacy.exists()
    assert Path(cache._cache_path(1, row.id, 8000)).exists()
    recordings.download.assert_not_awaited()
    recordings.storage.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_cache_older_than_recording_is_not_reused(recordings):
    legacy = recordings.path / "1_welcome_8000.pcm"
    legacy.write_bytes(b"old recording")
    os.utime(legacy, (1, 1))
    result = await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42)
    assert result.audio == recordings.rows[0].audio
    recordings.download.assert_awaited_once()


@pytest.mark.asyncio
async def test_numeric_name_cannot_collide_with_another_recording_pk(recordings):
    numeric = recording(99, name="42", transcript="Different recording")
    numeric.audio = b"\x20\x27" * 80
    recordings.rows.append(numeric)
    (recordings.path / "1_42_8000.pcm").write_bytes(numeric.audio)
    fetch = cache.create_recording_audio_fetcher(1, 8000)
    assert (await fetch(recording_pk=42)).audio == recordings.rows[0].audio
    assert await fetch(recording_id="42") == cache.RecordingAudio(
        numeric.audio, numeric.transcript
    )
    assert (
        Path(cache._cache_path(1, 42, 8000)).read_bytes()
        != Path(cache._cache_path(1, 99, 8000)).read_bytes()
    )
    recordings.download.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_is_scoped_by_organization_and_sample_rate(recordings):
    other = recording(43, org=2, transcript="Other organization")
    other.audio = b"\x30\x27" * 80
    recordings.rows.append(other)
    assert (
        await cache.create_recording_audio_fetcher(1, 8000)(recording_id="welcome")
    ).transcript == "Hello"
    assert (
        await cache.create_recording_audio_fetcher(2, 8000)(recording_id="welcome")
    ).transcript == "Other organization"
    assert await cache.create_recording_audio_fetcher(2, 8000)(recording_pk=42) is None
    assert (
        await cache.create_recording_audio_fetcher(1, 16000)(recording_pk=42)
    ).audio == recordings.rows[0].audio
    assert recordings.download.await_count == 3
    assert len(list(recordings.path.glob("recording_*.pcm"))) == 3


@pytest.mark.asyncio
async def test_disk_cache_does_not_authorize_a_deleted_recording(recordings):
    Path(cache._cache_path(1, 42, 8000)).write_bytes(recordings.rows[0].audio)
    recordings.rows[0].is_active = False
    assert await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42) is None
    recordings.download.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["rename", "delete"])
async def test_worker_sync_invalidates_existing_fetchers(recordings, mutation):
    fetch = cache.create_recording_audio_fetcher(1, 8000)
    await fetch(recording_pk=42)
    other = recording(100, org=2)
    recordings.rows.append(other)
    await cache.create_recording_audio_fetcher(2, 8000)(recording_pk=100)
    if mutation == "rename":
        recordings.rows[0].recording_id = "renamed"
    else:
        recordings.rows[0].is_active = False
    await cache.handle_recording_cache_sync(
        WorkerSyncEvent(
            event_type=WorkerSyncEventType.RECORDINGS, action="invalidate", org_id="1"
        )
    )
    assert await fetch(recording_id="welcome") is None
    if mutation == "rename":
        assert (await fetch(recording_id="renamed")).audio == recordings.rows[0].audio
        assert (await fetch(recording_pk=42)).audio == recordings.rows[0].audio
    else:
        assert await fetch(recording_pk=42) is None
    assert cache._cached_recording(2, 100) is not None
    assert (
        recordings.download.await_count == 2
    )  # One fill per org; rename preserves PCM.


@pytest.mark.asyncio
async def test_metadata_ttl_recovers_from_missed_invalidation(recordings, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cache, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    fetch = cache.create_recording_audio_fetcher(1, 8000)
    await fetch(recording_pk=42)
    recordings.rows[0].is_active = False
    clock[0] += cache._METADATA_TTL_SECONDS + 1
    assert await fetch(recording_pk=42) is None
    assert await fetch(recording_id="welcome") is None


@pytest.mark.asyncio
async def test_metadata_cache_is_bounded_and_eviction_keeps_disk_audio(
    recordings, monkeypatch
):
    monkeypatch.setattr(cache, "_MAX_METADATA_ENTRIES", 2)
    recordings.rows.append(recording(43, name="second"))
    fetch = cache.create_recording_audio_fetcher(1, 8000)
    await fetch(recording_pk=42)
    await fetch(recording_pk=43)
    assert len(cache._metadata_cache) == 2
    await fetch(recording_pk=42)
    assert recordings.by_pk.await_count == 3
    assert recordings.download.await_count == 2


@pytest.mark.asyncio
async def test_warm_and_concurrent_calls_share_one_download(recordings):
    entered, release = asyncio.Event(), asyncio.Event()
    original = recordings.download.side_effect

    async def held_download(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    recordings.download.side_effect = held_download
    warming = asyncio.create_task(cache.warm_recording_cache(1, 8000))
    calls = []
    try:
        await asyncio.wait_for(entered.wait(), 1)
        calls = [
            asyncio.create_task(
                cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42)
            )
            for _ in range(4)
        ]
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(warming, *calls), 2)
        assert all(call.result().audio == recordings.rows[0].audio for call in calls)
        recordings.download.assert_awaited_once()
        recordings.convert.assert_awaited_once()
        recordings.by_pk.assert_not_awaited()
        assert not cache._download_locks
    finally:
        release.set()
        await asyncio.gather(warming, *calls, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["download", "conversion", "cancel"])
async def test_failed_fill_can_be_retried(recordings, failure):
    fetch = cache.create_recording_audio_fetcher(1, 8000)
    mock = recordings.download if failure == "download" else recordings.convert
    original = mock.side_effect
    mock.side_effect = asyncio.CancelledError if failure == "cancel" else None
    mock.return_value = None
    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await fetch(recording_pk=42)
    else:
        assert await fetch(recording_pk=42) is None
    assert not Path(cache._cache_path(1, 42, 8000)).exists()
    assert not list(recordings.path.glob("download-*.wav"))
    mock.side_effect = original
    assert (await fetch(recording_pk=42)).audio == recordings.rows[0].audio


@pytest.mark.asyncio
async def test_invalidation_during_lookup_does_not_restore_stale_metadata(recordings):
    original = recordings.by_pk.side_effect

    async def stale_lookup(*args):
        stale = SimpleNamespace(**vars(recordings.rows[0]))
        recordings.rows[0].is_active = False
        cache._clear_recording_metadata(1)
        recordings.by_pk.side_effect = original
        return stale

    recordings.by_pk.side_effect = stale_lookup
    assert await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42) is None
    assert not cache._metadata_cache
    recordings.download.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalidation_broadcasts_and_clears_local_metadata(
    recordings, monkeypatch
):
    await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42)
    manager = SimpleNamespace(broadcast=AsyncMock())
    monkeypatch.setattr(cache, "get_worker_sync_manager", lambda: manager)
    await cache.invalidate_recording_cache(1)
    assert not cache._metadata_cache
    manager.broadcast.assert_awaited_once_with(
        WorkerSyncEventType.RECORDINGS, action="invalidate", org_id="1"
    )


@pytest.mark.asyncio
async def test_unavailable_sync_does_not_fail_committed_mutation(
    recordings, monkeypatch
):
    await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42)
    manager = SimpleNamespace(
        broadcast=AsyncMock(side_effect=ConnectionError("redis unavailable"))
    )
    monkeypatch.setattr(cache, "get_worker_sync_manager", lambda: manager)
    await cache.invalidate_recording_cache(1)
    assert not cache._metadata_cache


@pytest.mark.asyncio
async def test_warm_cache_checks_do_not_read_pcm(recordings, monkeypatch):
    await cache.warm_recording_cache(1, 8000)
    read = Mock(side_effect=AssertionError("warming should only stat cached PCM"))
    monkeypatch.setattr(cache, "read_cached_file", read)
    await cache.warm_recording_cache(1, 8000)
    read.assert_not_called()
    recordings.download.assert_awaited_once()


@pytest.mark.asyncio
async def test_cache_file_io_runs_off_the_event_loop(recordings, monkeypatch):
    loop_thread = threading.get_ident()
    read = cache.read_cached_file
    write = cache.write_cache_file

    def checked_read(path):
        assert threading.get_ident() != loop_thread
        return read(path)

    def checked_write(path, data):
        assert threading.get_ident() != loop_thread
        return write(path, data)

    monkeypatch.setattr(cache, "read_cached_file", checked_read)
    monkeypatch.setattr(cache, "write_cache_file", checked_write)
    fetch = cache.create_recording_audio_fetcher(1, 8000)
    expected = cache.RecordingAudio(recordings.rows[0].audio, "Hello")
    assert await fetch(recording_pk=42) == expected
    assert await fetch(recording_pk=42) == expected


@pytest.mark.asyncio
async def test_reassigned_name_cannot_inherit_legacy_audio(recordings):
    row = recordings.rows[0]
    legacy = recordings.path / "1_welcome_8000.pcm"
    legacy.write_bytes(row.audio)
    other_org = recordings.path / "2_welcome_8000.pcm"
    other_org.write_bytes(b"other org")
    replacement = recording(43, name="welcome", transcript="Replacement")
    replacement.audio = b"\x20\x27" * 80
    row.recording_id = "renamed"
    recordings.rows.append(replacement)
    await cache.handle_recording_cache_sync(
        WorkerSyncEvent(
            event_type=WorkerSyncEventType.RECORDINGS, action="invalidate", org_id="1"
        )
    )
    result = await cache.create_recording_audio_fetcher(1, 8000)(recording_id="welcome")
    assert result == cache.RecordingAudio(replacement.audio, replacement.transcript)
    assert other_org.exists()
    recordings.download.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_disk_entry_is_refilled(recordings):
    Path(cache._cache_path(1, 42, 8000)).write_bytes(b"")
    result = await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42)
    assert result.audio == recordings.rows[0].audio
    recordings.download.assert_awaited_once()


@pytest.mark.asyncio
async def test_restart_reuses_disk_after_one_metadata_lookup(recordings):
    fetch = cache.create_recording_audio_fetcher(1, 8000)
    await fetch(recording_pk=42)
    cache._metadata_cache.clear()  # Process restart leaves only the disk cache.
    assert (
        await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42)
    ).audio == recordings.rows[0].audio
    assert recordings.by_pk.await_count == 2
    recordings.download.assert_awaited_once()


@pytest.mark.asyncio
async def test_warm_query_overlapping_invalidation_cannot_restore_metadata(recordings):
    async def stale_list(**_):
        stale = SimpleNamespace(**vars(recordings.rows[0]))
        recordings.rows[0].is_active = False
        cache._clear_recording_metadata(1)
        return [stale]

    recordings.listing.side_effect = stale_list
    await cache.warm_recording_cache(1, 8000)
    assert not cache._metadata_cache
    assert await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42) is None
    recordings.download.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["fetch", "warm"])
async def test_other_organization_mutation_does_not_restart_or_abort_query(
    recordings, path
):
    query = recordings.by_pk if path == "fetch" else recordings.listing
    original = query.side_effect

    async def lookup(*args, **kwargs):
        cache._clear_recording_metadata(2)
        if path == "fetch":
            return await original(*args, **kwargs)
        return original(*args, **kwargs)

    query.side_effect = lookup
    if path == "fetch":
        result = await asyncio.wait_for(
            cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42), 1
        )
        assert result.audio == recordings.rows[0].audio
    else:
        await cache.warm_recording_cache(1, 8000)
    query.assert_awaited_once()
    recordings.download.assert_awaited_once()
    assert cache._cached_recording(1, 42) is not None
    assert not cache._metadata_invalidations


@pytest.mark.asyncio
async def test_cancelled_fill_releases_waiting_fetch(recordings):
    entered = asyncio.Event()
    original = recordings.convert.side_effect

    async def held_conversion(*args, **kwargs):
        if recordings.convert.await_count == 1:
            entered.set()
            await asyncio.Event().wait()
        return await original(*args, **kwargs)

    recordings.convert.side_effect = held_conversion
    fetch = cache.create_recording_audio_fetcher(1, 8000)
    owner = asyncio.create_task(fetch(recording_pk=42))
    waiter = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        waiter = asyncio.create_task(fetch(recording_pk=42))
        await asyncio.sleep(0)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert (await asyncio.wait_for(waiter, 1)).audio == recordings.rows[0].audio
        assert recordings.download.await_count == 2
        assert not list(recordings.path.glob("download-*.wav"))
    finally:
        tasks = [owner] + ([waiter] if waiter else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["create", "rename", "delete"])
async def test_mutation_routes_invalidate_after_committing(
    recordings, monkeypatch, mutation
):
    from api.routes import workflow_recording as routes
    from api.schemas.workflow_recording import (
        BatchRecordingCreateRequestSchema,
        RecordingCreateRequestSchema,
        RecordingUpdateRequestSchema,
    )

    await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42)
    events = []

    async def broadcast(*args, **kwargs):
        events.append("broadcast")

    manager = SimpleNamespace(broadcast=AsyncMock(side_effect=broadcast))
    monkeypatch.setattr(cache, "get_worker_sync_manager", lambda: manager)
    user = SimpleNamespace(id=1, selected_organization_id=1)
    row = recordings.rows[0]

    async def write(**kwargs):
        assert kwargs["organization_id"] == 1
        events.append("commit")
        if mutation == "delete":
            row.is_active = False
            return True
        if mutation == "rename":
            row.recording_id = kwargs["new_recording_id"]
        return row

    if mutation == "create":
        monkeypatch.setattr(db_client, "create_recording", AsyncMock(side_effect=write))
        await routes.create_recordings(
            BatchRecordingCreateRequestSchema(
                recordings=[
                    RecordingCreateRequestSchema(
                        recording_id="welcome",
                        transcript="Hello",
                        storage_key=row.storage_key,
                    )
                ]
            ),
            user=user,
        )
    elif mutation == "rename":
        monkeypatch.setattr(
            db_client, "check_recording_id_exists", AsyncMock(return_value=False)
        )
        monkeypatch.setattr(
            db_client, "update_recording_id", AsyncMock(side_effect=write)
        )
        await routes.update_recording(
            42, RecordingUpdateRequestSchema(recording_id="renamed"), user=user
        )
    else:
        monkeypatch.setattr(db_client, "delete_recording", AsyncMock(side_effect=write))
        await routes.delete_recording("welcome", user=user)
    assert not cache._metadata_cache
    assert events == ["commit", "broadcast"]
    manager.broadcast.assert_awaited_once_with(
        WorkerSyncEventType.RECORDINGS, action="invalidate", org_id="1"
    )


@pytest.mark.asyncio
async def test_failed_rename_preserves_cached_alias(recordings, monkeypatch):
    from fastapi import HTTPException

    from api.routes import workflow_recording as routes
    from api.schemas.workflow_recording import RecordingUpdateRequestSchema

    await cache.create_recording_audio_fetcher(1, 8000)(recording_pk=42)
    manager = SimpleNamespace(broadcast=AsyncMock())
    monkeypatch.setattr(cache, "get_worker_sync_manager", lambda: manager)
    monkeypatch.setattr(
        db_client, "check_recording_id_exists", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        db_client,
        "update_recording_id",
        AsyncMock(side_effect=RuntimeError("workflow update failed")),
    )
    with pytest.raises(HTTPException) as error:
        await routes.update_recording(
            42,
            RecordingUpdateRequestSchema(recording_id="renamed"),
            user=SimpleNamespace(selected_organization_id=1),
        )
    assert error.value.status_code == 500
    assert cache._cached_recording(1, "welcome") is not None
    manager.broadcast.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after", [None, 0, 6])
async def test_batch_create_invalidates_once_including_partial_commits(
    recordings, monkeypatch, fail_after
):
    from fastapi import HTTPException

    from api.routes import workflow_recording as routes
    from api.schemas.workflow_recording import BatchRecordingCreateRequestSchema

    events = []

    async def create(**kwargs):
        assert kwargs["organization_id"] == 1
        if len(events) == fail_after:
            raise RuntimeError("recording write failed")
        events.append("commit")
        return recordings.rows[0]

    async def invalidate(organization_id):
        assert organization_id == 1
        events.append("invalidate")

    monkeypatch.setattr(db_client, "create_recording", AsyncMock(side_effect=create))
    monkeypatch.setattr(routes, "invalidate_recording_cache", invalidate)
    request = BatchRecordingCreateRequestSchema(
        recordings=[
            {
                "recording_id": f"greeting-{i}",
                "storage_key": f"recordings/1/{i}.wav",
                "transcript": "Hello",
            }
            for i in range(20)
        ]
    )
    user = SimpleNamespace(id=1, selected_organization_id=1)
    if fail_after is None:
        response = await routes.create_recordings(request, user=user)
        assert len(response.recordings) == 20
    else:
        with pytest.raises(HTTPException) as error:
            await routes.create_recordings(request, user=user)
        assert error.value.status_code == 500
    commits = 20 if fail_after is None else fail_after
    assert events == ["commit"] * commits + (["invalidate"] if commits else [])


@pytest.mark.asyncio
async def test_batch_create_invalidates_if_response_conversion_fails_after_commit(
    recordings, monkeypatch
):
    from fastapi import HTTPException

    from api.routes import workflow_recording as routes
    from api.schemas.workflow_recording import BatchRecordingCreateRequestSchema

    monkeypatch.setattr(
        db_client, "create_recording", AsyncMock(return_value=recordings.rows[0])
    )
    monkeypatch.setattr(
        routes, "_build_response", Mock(side_effect=ValueError("response invalid"))
    )
    invalidate = AsyncMock()
    monkeypatch.setattr(routes, "invalidate_recording_cache", invalidate)
    with pytest.raises(HTTPException):
        await routes.create_recordings(
            BatchRecordingCreateRequestSchema(
                recordings=[
                    {
                        "recording_id": "hello",
                        "storage_key": "recording.wav",
                        "transcript": "Hello",
                    }
                ]
            ),
            user=SimpleNamespace(id=1, selected_organization_id=1),
        )
    invalidate.assert_awaited_once_with(1)
