import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import workflow_run_artifacts as artifacts
from api.services.s3_secondary_replication import _artifact_refs
from api.services.sakinah.live_calm import LiveCalmSession


@pytest.mark.asyncio
async def test_revision_bundle_preserves_scores_prompts_and_replication_bytes(monkeypatch):
    writes = {}
    async def upload(_run, body, key, _label):
        writes[key] = body
        return True
    monkeypatch.setattr(artifacts, "_upload_bytes", upload)
    monkeypatch.setattr(artifacts, "_persist_run_fields", AsyncMock(return_value=True))
    monkeypatch.setattr(artifacts.db_client, "get_workflow_run_by_id", AsyncMock(return_value=SimpleNamespace(started_at=datetime(2026, 10, 4, tzinfo=UTC), service_user_id="u1", call_id="c1")))
    replicate = AsyncMock()
    monkeypatch.setattr(artifacts, "schedule_s3_replication", replicate)
    session = LiveCalmSession(42)
    session.analyse_user("I am anxious", [], source_turn_key=1)
    session.record_sakinah("That sounds difficult")
    metadata = await artifacts.persist_calm_scoring_artifact(42, session.payload(), replicate=True)
    refs = _artifact_refs(replicate.await_args.args[1])
    assert {ref['type'] for ref in refs} == {'turn-by-turn', 'scoring-table', 'prompt-engineering'}
    original = {ref['object_key']: writes[ref['object_key']] for ref in refs}
    assert all('/revisions/' in key for key in original)
    for ref in refs:
        assert hashlib.sha256(writes[ref['object_key']]).hexdigest() == ref['checksum_sha256']
    prompt_ref = next(ref for ref in refs if ref['type'] == 'prompt-engineering')
    turns = json.loads(writes[prompt_ref['object_key']])['turns']
    assert turns[0]['prompt_sent_to_llm']
    assert turns[0]['response_delivered'] == "That sounds difficult"
    table_ref = next(ref for ref in refs if ref['type'] == 'scoring-table')
    assert len(json.loads(writes[table_ref['object_key']])['rows']) == 2
    session.analyse_user("Now I feel calmer", [], source_turn_key=3)
    new = await artifacts.persist_calm_scoring_artifact(42, session.payload(), replicate=True)
    assert metadata['objects'][0]['object_key'] != new['objects'][0]['object_key']
    assert all(writes[key] == body for key, body in original.items())


@pytest.mark.asyncio
async def test_finalization_flushes_without_evaluator(monkeypatch):
    session = LiveCalmSession(42)
    session.persist = AsyncMock()
    await session.wait_for_evaluations()
    session.persist.assert_awaited_once()
