"""Call tags survive two writers meeting on the same finishing run.

A run being torn down is written twice from two different snapshots: the engine
persists its own ``_gathered_context`` from ``end_call_with_reason``, and
``on_pipeline_finished`` persists the copy it took via ``get_gathered_context``
after adding ``user_speech`` and any ``tag_*`` keys. ``update_workflow_run``
merges gathered context by key, which is right for scalars but replaced
``call_tags`` wholesale -- so whichever write landed second dropped the other's
tags. Run 1091 lost ``user_speech`` exactly that way.
"""

from api.db.workflow_run_client import append_unique_tags


def test_tags_from_both_writers_survive():
    # on_pipeline_finished stored user_speech; the engine's later write carried
    # only the disposition. Before the union that second write won outright.
    assert append_unique_tags(["user_speech"], ["user_hangup"]) == [
        "user_speech",
        "user_hangup",
    ]


def test_order_is_preserved_and_duplicates_dropped():
    assert append_unique_tags(
        ["end_call_tool", "do_not_call"], ["do_not_call", "user_speech"]
    ) == ["end_call_tool", "do_not_call", "user_speech"]


def test_absent_tags_on_either_side_are_not_an_error():
    assert append_unique_tags(None, ["retry"]) == ["retry"]
    assert append_unique_tags(["retry"], None) == ["retry"]
    assert append_unique_tags(None, None) == []


def test_a_non_list_value_is_treated_as_no_tags():
    # gathered_context is caller-supplied JSON; a malformed value must not raise
    # in the middle of persisting a finished call.
    assert append_unique_tags("user_speech", ["user_hangup"]) == ["user_hangup"]
    assert append_unique_tags(["user_speech"], "user_hangup") == ["user_speech"]


def test_the_stored_list_is_not_mutated_in_place():
    stored = ["user_speech"]
    append_unique_tags(stored, ["user_hangup"])
    assert stored == ["user_speech"]
