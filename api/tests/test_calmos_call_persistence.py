import io
import uuid
import wave
from types import SimpleNamespace

import pytest

from api.db.call_persistence_client import (
    CallerIdentityResolution,
    _latency_metrics,
    _utterance_events,
    caller_identifier_hash,
    legacy_caller_identifier_hash,
    normalize_caller_identifier,
)
from api.db.models import MemoryModel
from api.services.call_persistence import persist_call_data_with_retry
from api.services.memory import orchestrator
from api.services.workflow_run_artifacts import _recording_metadata


def test_caller_identifier_is_normalized_and_hashed_without_using_phone_as_id():
    assert normalize_caller_identifier(" +44 20 1234 ") == "+44201234"
    assert caller_identifier_hash(" +44 20 1234 ") == caller_identifier_hash(
        "+44201234"
    )
    assert caller_identifier_hash("+44201234") != "+44201234"
    assert caller_identifier_hash("+44201234") != legacy_caller_identifier_hash(
        "+44201234"
    )


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
    utterances = _utterance_events(
        events,
        __import__("datetime").datetime.fromisoformat("2026-09-05T10:00:00+00:00"),
    )
    assert [
        (item["speaker"], item["sequence_number"], item["transcript"])
        for item in utterances
    ] == [
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


def test_latency_events_are_persisted_as_bounded_metrics():
    metrics = _latency_metrics(
        [
            {"type": "rtf-latency-measured", "payload": {"latency_seconds": 0.8}},
            {"type": "rtf-latency-measured", "payload": {"latency_seconds": 1.2}},
            {"type": "rtf-ttfb-metric", "payload": {"ttfb_seconds": 0.2}},
        ],
        30.0,
    )
    assert metrics["latency_sample_count"] == 2
    assert metrics["average_latency_seconds"] == 1.0
    assert metrics["maximum_latency_seconds"] == 1.2
    assert metrics["average_ttfb_seconds"] == 0.2


class _MemoryDB:
    def __init__(
        self,
        *,
        created: bool,
        memories: list[dict],
        verified: bool = False,
        memory_permitted: bool = True,
    ):
        self.created = created
        self.memories = memories
        self.verified = verified
        self.memory_permitted = memory_permitted
        self.marked = []

    async def resolve_caller_identity(self, *_args, **_kwargs):
        return CallerIdentityResolution(
            service_user=SimpleNamespace(
                id="service-user-1",
                preferred_name="Alex",
                first_use_explanation_shown=not self.created,
            ),
            caller_identifier=SimpleNamespace(
                id="caller-identifier-1", verified=self.verified
            ),
            created=self.created,
        )

    async def is_memory_permitted(self, *_args, **_kwargs):
        return self.memory_permitted

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
async def test_unverified_recognised_caller_cannot_receive_high_sensitivity_memory(
    monkeypatch,
):
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
    assert [item["memory_text"] for item in result["relevant_memories"]] == [
        "Prefers mornings"
    ]
    assert "Highly sensitive historic detail" not in result["prompt_context"]
    assert "Hello, Alex" not in result["greeting_override"]
    assert result["relevant_memories"][0]["may_verbalize"] is False


@pytest.mark.asyncio
async def test_stored_verified_identifier_enables_explicitly_permitted_memory(
    monkeypatch,
):
    fake_db = _MemoryDB(
        created=False,
        verified=True,
        memories=[
            {
                "memory_type": "preference",
                "memory_text": "Prefers morning calls",
                "sensitivity": "normal",
                "internal_context_allowed": True,
                "verbal_reference_allowed": True,
                "explicit_detail_allowed": True,
            }
        ],
    )
    monkeypatch.setattr(orchestrator, "db_client", fake_db)
    monkeypatch.setattr(orchestrator, "MEMORY_ENABLED", True)
    result = await orchestrator.prepare_memory_context(
        organization_id=1,
        call_context={"caller_number": "+441234"},
    )
    assert result["caller_status"] == "VERIFIED"
    assert result["memory_authorisation_level"] == "verified"
    assert result["relevant_memories"][0]["may_verbalize"] is True
    assert "Hello, Alex" in result["greeting_override"]


@pytest.mark.asyncio
async def test_memory_permission_denial_returns_no_context(monkeypatch):
    fake_db = _MemoryDB(
        created=False,
        memories=[],
        memory_permitted=False,
    )
    monkeypatch.setattr(orchestrator, "db_client", fake_db)
    monkeypatch.setattr(orchestrator, "MEMORY_ENABLED", True)
    result = await orchestrator.prepare_memory_context(
        organization_id=1,
        call_context={"caller_number": "+441234"},
    )
    assert result["memory_authorisation_level"] == "disabled"
    assert result["memory_available"] is False


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


@pytest.mark.asyncio
async def test_service_user_identifier_verification_privacy_and_vector_lookup(
    db_session,
):
    suffix = uuid.uuid4().hex
    user, _ = await db_session.get_or_create_user_by_provider_id(f"calmos-{suffix}")
    organization, _ = await db_session.get_or_create_organization_by_provider_id(
        f"calmos-org-{suffix}", user.id
    )

    first = await db_session.resolve_caller_identity(
        organization.id, "+44 20 7946 0958", preferred_name="Alex"
    )
    assert first.created is True
    assert first.service_user.id != "+442079460958"
    assert first.caller_identifier.verified is False

    recognised = await db_session.resolve_caller_identity(
        organization.id, "+442079460958"
    )
    assert recognised.created is False
    assert recognised.service_user.id == first.service_user.id

    verified = await db_session.set_caller_identifier_verification(
        organization_id=organization.id,
        caller_identifier_id=recognised.caller_identifier.id,
        verified=True,
        verification_level="knowledge_check",
    )
    assert verified.verified is True
    assert verified.verification_level == "knowledge_check"

    await db_session.create_or_confirm_memory(
        {
            "service_user_id": first.service_user.id,
            "memory_type": "preference",
            "memory_text": "Prefers morning calls",
            "embedding": [1.0] + [0.0] * 1535,
            "importance": 0.8,
            "confidence": 0.9,
            "sensitivity": "normal",
            "internal_context_allowed": True,
            "verbal_reference_allowed": True,
            "explicit_detail_allowed": False,
        }
    )
    memories = await db_session.get_permitted_memories(
        first.service_user.id,
        verified=True,
        query_embedding=[1.0] + [0.0] * 1535,
        min_similarity=0.99,
    )
    assert [item["memory_text"] for item in memories] == ["Prefers morning calls"]

    await db_session.record_privacy_permission(
        organization_id=organization.id,
        service_user_id=first.service_user.id,
        permission_type="memory_use",
        granted=False,
        verification_level="knowledge_check",
    )
    assert not await db_session.is_memory_permitted(
        first.service_user.id, permission_type="memory_use"
    )
