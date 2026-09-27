"""Live WebRTC/voice CALM snapshots use the same durable turn contract."""

from unittest.mock import AsyncMock

import pytest

from api.services.sakinah import live_calm


@pytest.mark.asyncio
async def test_live_turns_keep_prompt_and_persist_during_call(monkeypatch):
    update_run = AsyncMock()
    upload = AsyncMock()
    monkeypatch.setattr(live_calm.db_client, "update_workflow_run", update_run)
    monkeypatch.setattr(live_calm, "persist_calm_scoring_artifact", upload)

    session = live_calm.LiveCalmSession(42)
    caller = session.analyse_user("I feel worried today.", [])
    assert caller["prompt_sent_to_llm"]
    await session.persist()
    response = session.record_sakinah("It sounds difficult. What is worrying you?")
    assert response["prompt_sent_to_llm"] == caller["prompt_sent_to_llm"]
    await session.persist()

    payload = update_run.await_args.kwargs["annotations"]["calm_scoring"]
    assert len(payload["caller"]) == 1
    assert len(payload["sakinah"]) == 1
    assert upload.await_args.kwargs["replicate"] is True
    assert session.analyse_user("I feel worried today.", []) is None


def test_identical_words_on_a_new_user_turn_are_scored_again():
    session = live_calm.LiveCalmSession(42)
    assert session.analyse_user("I am worried.", [], source_turn_key=2)
    assert session.analyse_user("I am worried.", [], source_turn_key=2) is None
    assert session.analyse_user("I am worried.", [], source_turn_key=4)
    assert len(session.runtime.turns) == 2


def test_latest_user_turn_accepts_transcribed_multimodal_content():
    messages = [
        {"role": "user", "content": "Earlier words"},
        {"role": "assistant", "content": "Reply"},
        {"role": "user", "content": [{"type": "input_text", "text": "New words"}]},
    ]
    assert live_calm.latest_user_turn(messages) == (2, "New words")
