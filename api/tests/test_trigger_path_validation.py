from api.services.workflow.trigger_paths import (
    TRIGGER_PATH_MAX_LENGTH,
    validate_trigger_paths,
)


def test_validate_trigger_paths_rejects_invalid_path_segments():
    workflow_definition = {
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "data": {"trigger_path": "support/west"},
            }
        ],
        "edges": [],
    }

    issues = validate_trigger_paths(workflow_definition)

    assert len(issues) == 1
    assert issues[0].node_id == "trigger-1"
    assert "single URL path segment" in issues[0].message


def test_validate_trigger_paths_rejects_long_and_duplicate_paths():
    long_path = "a" * (TRIGGER_PATH_MAX_LENGTH + 1)
    workflow_definition = {
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "data": {"trigger_path": long_path},
            },
            {
                "id": "trigger-2",
                "type": "trigger",
                "data": {"trigger_path": "sales_agent"},
            },
            {
                "id": "trigger-3",
                "type": "trigger",
                "data": {"trigger_path": "sales_agent"},
            },
        ],
        "edges": [],
    }

    issues = validate_trigger_paths(workflow_definition)
    messages = [issue.message for issue in issues]

    assert (
        f"Trigger path must be {TRIGGER_PATH_MAX_LENGTH} characters or fewer."
        in messages
    )
    assert "Trigger path is duplicated in this workflow." in messages


def test_validate_trigger_paths_detects_duplicate_when_first_node_has_no_id():
    """A duplicate trigger path must be flagged even when the first node sharing
    that path has no ``id`` (``node.get("id")`` is None).

    Regression: the duplicate check previously used ``seen_paths.get(path)`` and
    treated a ``None`` result as "not seen yet", so a first node with a missing
    id (stored as None) made every later node with the same path slip through
    undetected.
    """
    workflow_definition = {
        "nodes": [
            # No "id" key -> node_id resolves to None.
            {"type": "trigger", "data": {"trigger_path": "sales_agent"}},
            {
                "id": "trigger-2",
                "type": "trigger",
                "data": {"trigger_path": "sales_agent"},
            },
        ],
        "edges": [],
    }

    issues = validate_trigger_paths(workflow_definition)
    messages = [issue.message for issue in issues]

    assert "Trigger path is duplicated in this workflow." in messages
    duplicate_issue = next(
        issue
        for issue in issues
        if issue.message == "Trigger path is duplicated in this workflow."
    )
    assert duplicate_issue.node_id == "trigger-2"
