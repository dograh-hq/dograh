"""Tests for the direction / channel filters used by the usage page."""

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from api.db.filters import apply_workflow_run_filters
from api.db.models import WorkflowRunModel
from api.enums import WORKFLOW_RUN_MODES_BY_CHANNEL, WorkflowRunMode


def _where(filters) -> str:
    query = apply_workflow_run_filters(select(WorkflowRunModel.id), filters)
    sql = str(
        query.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).replace("\n", " ")
    return sql[sql.index("WHERE") :] if "WHERE" in sql else ""


def _radio(attribute: str, status: str) -> list[dict]:
    return [{"attribute": attribute, "type": "radio", "value": {"status": status}}]


def test_every_run_mode_belongs_to_exactly_one_channel():
    """An unmapped mode would silently disappear from every channel filter."""
    mapped = [
        mode for modes in WORKFLOW_RUN_MODES_BY_CHANNEL.values() for mode in modes
    ]

    assert {mode.value for mode in WorkflowRunMode} == set(mapped)
    assert len(mapped) == len(set(mapped))


def test_direction_filter_matches_call_type():
    assert _where(_radio("callDirection", "inbound")) == (
        "WHERE workflow_runs.call_type = 'inbound'"
    )
    assert _where(_radio("callDirection", "outbound")) == (
        "WHERE workflow_runs.call_type = 'outbound'"
    )


def test_channel_filter_expands_to_that_channels_modes():
    where = _where(_radio("callChannel", "telephony"))

    assert "workflow_runs.mode IN" in where
    assert "'twilio'" in where
    assert "'smallwebrtc'" not in where
    assert "'textchat'" not in where


def test_channel_and_direction_combine():
    where = _where(
        _radio("callChannel", "telephony") + _radio("callDirection", "outbound")
    )

    assert "workflow_runs.mode IN" in where
    assert "workflow_runs.call_type = 'outbound'" in where


def test_unrecognized_values_do_not_constrain_the_query():
    """'all' means no filter; anything unexpected must not reach the SQL."""
    assert _where(_radio("callDirection", "all")) == ""
    assert _where(_radio("callChannel", "all")) == ""
    assert _where(_radio("callDirection", "'; DROP TABLE workflow_runs;--")) == ""
    assert _where(_radio("callChannel", "voip")) == ""
