"""The engine owns the call's gathered context; callers write through it.

Teardown used to work the other way round: ``on_pipeline_finished`` took a
shallow copy, mutated it, merged it with a separately-read database row and
persisted that. The copy isolated nothing that mattered -- being shallow, an
append to ``call_tags`` reached back into the engine's live list -- and the two
writers ended up reconciling snapshots that had each seen part of the truth.
Writes now go through ``record_context`` / ``record_call_tags``, leaving one
dict to read at the end.
"""

import pytest

from api.services.workflow.pipecat_engine import PipecatEngine


def _engine() -> PipecatEngine:
    return PipecatEngine(workflow=None, call_context_vars={})


def test_recorded_values_land_on_the_engines_context():
    engine = _engine()

    engine.record_context({"trace_url": "https://trace/1", "state": "ME"})

    assert engine._gathered_context["trace_url"] == "https://trace/1"
    assert engine._gathered_context["state"] == "ME"


def test_tags_accumulate_across_separate_writers():
    # The disposition is recorded at teardown, `user_speech` several seconds
    # later from on_pipeline_finished. Both have to survive.
    engine = _engine()

    engine.record_call_tags(["user_hangup"])
    engine.record_call_tags(["user_speech"])

    assert engine._gathered_context["call_tags"] == ["user_hangup", "user_speech"]


def test_recording_the_same_tag_twice_is_a_no_op():
    engine = _engine()

    engine.record_call_tags(["user_speech"])
    engine.record_call_tags(["user_speech"])

    assert engine._gathered_context["call_tags"] == ["user_speech"]


def test_tag_prefixed_context_keys_are_promoted_to_call_tags():
    # A workflow author names an extraction variable `tag_*` to turn its value
    # into a call tag; extraction merges it into this same context.
    engine = _engine()
    engine.record_context({"tag_language": "spanish", "state": "ME"})

    engine.record_call_tags()

    assert "spanish" in engine._gathered_context["call_tags"]
    assert "ME" not in engine._gathered_context["call_tags"]


def test_a_promoted_tag_is_deduplicated_by_value():
    # The previous implementation checked whether the *key* was already tagged
    # while appending the *value*, so a value that was already a tag got added
    # again and every repeat call duplicated it.
    engine = _engine()
    engine.record_context({"tag_language": "spanish"})

    engine.record_call_tags(["spanish"])
    engine.record_call_tags()

    assert engine._gathered_context["call_tags"] == ["spanish"]


def test_a_non_string_tag_key_is_ignored():
    # gathered_context carries arbitrary extracted values; a `tag_` key holding
    # a dict must not end up in a list of tag strings.
    engine = _engine()
    engine.record_context({"tag_scores": {"a": 1}, "tag_language": "spanish"})

    engine.record_call_tags()

    assert engine._gathered_context["call_tags"] == ["spanish"]


@pytest.mark.asyncio
async def test_reading_the_context_does_not_hand_out_write_access():
    engine = _engine()
    engine.record_call_tags(["user_hangup"])

    snapshot = await engine.get_gathered_context()
    snapshot["call_disposition"] = "tampered"

    assert "call_disposition" not in engine._gathered_context
