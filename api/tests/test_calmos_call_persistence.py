import io
import wave
from types import SimpleNamespace

import pytest

from api.db.call_persistence_client import (
    _utterance_events,
    caller_identifier_hash,
    normalize_caller_identifier,
)
from api.services.call_persistence import persist_call_data_with_retry
from api.services.memory import orchestrator
from api.services.workflow_run_artifacts import _recording_metadata
from api.db.models import MemoryModel


def test_caller_identifier_is_normalized_and_hashed_without_using_phone_as_id():
    assert normalize_caller_identifier(" +44 20 1234 ") == "+44201234"
    assert caller_identifier_hash(" +44 20 1234 ") == caller_identifier_hash("+44201234")
    assert caller_identifier_hash("+44201234") != "+44201234"


def test_feedback_events_become_ordered_speaker_utterances():
    events = [
        {
            "type": "rtf-user-transcription",
            "timestamp": "2026-09-05T10:00:01+00:00",
            "payload": {"text": "Hello", "final": True},
        },
        {
            "type": "rtf-bot-text",
            "timestamp": "2026-09-05T10:00:02+00:00",
            "payload": {"text": "How can I help?"},
        },
        {
            "type": "rtf-user-transcription",
            "timestamp": "2026-09-05T10:00:03+00:00",
            "payload": {"text": "partial", "final": False},
        },
    ]
    utterances = _utterance_events(events, __import__("datetime").datetime.fromisoformat("2026-09-05T10:00:00+00:00"))
    assert [(item["speaker"], item["sequence_number"], item["transcript"]) for item in utterances] == [
        ("user", 1, "Hello"),
        ("assistant", 2, "How can I help?"),
    ]


def test_recording_metadata_persists_wav_size_and_duration_without_blob_storage():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8_000)
        wav_file.writeframes(b"\x00\x00" * 8_000)
    data = buffer.getvalue()
    metadata = _recording_metadata(
        "recordings/2026/09/service-user/call/call.wav", "s3", "mixed", data
    )
    assert metadata["format"] == "wav"
    assert metadata["size_bytes"] == len(data)
    assert metadata["duration_seconds"] == pytest.approx(1.0)


def test_memory_embedding_column_uses_pgvector_dimension():
    assert MemoryModel.embedding.type.dim == 1536


class _MemoryDB:
    def __init__(self, *, created: bool, memories: list[dict]):
        self.created = created
        self.memories = memories
        self.marked = []

    async def get_or_create_service_user(self, *_args, **_kwargs):
        return SimpleNamespace(
            id="service-user-1",
            preferred_name="Alex",
            first_use_explanation_shown=not self.created,
        ), self.created

    async def get_permitted_memories(self, *_args, **_kwargs):
        return self.memories

    async def mark_first_use_explanation_shown(self, service_user_id):
        self.marked.append(service_user_id)


@pytest.mark.asyncio
async def test_unknown_caller_does_not_require_identity_lookup(monkeypatch):
    monkeypatch.setattr(orchestrator, "MEMORY_ENABLED", True)
    result = await orchestrator.prepare_memory_context(
        organization_id=1,
        call_context={"direction": "inbound"},
    )
    assert result["caller_status"] == "UNKNOWN"
    assert result["relevant_memories"] == []


@pytest.mark.asyncio
async def test_first_time_caller_gets_one_time_explanation(monkeypatch):
    fake_db = _MemoryDB(created=True, memories=[])
    monkeypatch.setattr(orchestrator, "db_client", fake_db)
    monkeypatch.setattr(orchestrator, "MEMORY_ENABLED", True)
    result = await orchestrator.prepare_memory_context(
        organization_id=1,
        call_context={"caller_number": "+441234"},
    )
    assert result["caller_status"] == "FIRST_TIME"
    assert "remember useful information" in result["greeting_override"]
    assert fake_db.marked == ["service-user-1"]


@pytest.mark.asyncio
async def test_unverified_recognised_caller_cannot_receive_high_sensitivity_memory(monkeypatch):
    fake_db = _MemoryDB(
        created=False,
        memories=[
            {
                "memory_type": "preference",
                "memory_text": "Prefers mornings",
                "sensitivity": "low",
                "internal_context_allowed": True,
                "verbal_reference_allowed": False,
                "explicit_detail_allowed": False,
            },
            {
                "memory_type": "clinical_context",
                "memory_text": "Highly sensitive historic detail",
                "sensitivity": "high",
                "internal_context_allowed": True,
                "verbal_reference_allowed": True,
                "explicit_detail_allowed": True,
            },
        ],
    )
    monkeypatch.setattr(orchestrator, "db_client", fake_db)
    monkeypatch.setattr(orchestrator, "MEMORY_ENABLED", True)
    result = await orchestrator.prepare_memory_context(
        organization_id=1,
        call_context={"caller_number": "+441234"},
    )
    assert result["caller_status"] == "RECOGNISED"
    assert [item["memory_text"] for item in result["relevant_memories"]] == ["Prefers mornings"]
    assert "Highly sensitive historic detail" not in result["prompt_context"]


@pytest.mark.asyncio
async def test_persistence_failure_is_retried_and_queued_without_raising(monkeypatch):
    class _FailingDB:
        attempts = 0

        async def persist_call_snapshot(self, *_args, **_kwargs):
            self.attempts += 1
            raise RuntimeError("temporary database outage")

    queued = []

    async def fake_enqueue(function_name, workflow_run_id):
        queued.append((function_name, workflow_run_id))

    fake_db = _FailingDB()
    monkeypatch.setattr("api.services.call_persistence.db_client", fake_db)
    monkeypatch.setattr("api.services.call_persistence._enqueue_job", fake_enqueue)
    monkeypatch.setattr("api.services.call_persistence.asyncio.sleep", _no_sleep)
    await persist_call_data_with_retry(44, transcript_text="safe test transcript")
    assert fake_db.attempts == 3
    assert queued[-1][0] == "persist_workflow_run_call_data"


async def _no_sleep(_seconds):
    return None
