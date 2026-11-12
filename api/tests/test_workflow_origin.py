"""`created_via` is stamped at creation and never derived from builder_sessions.

These guard the exact edge case that motivated the field: a blank/template
agent later *edited* with the builder must keep its origin and surface as
``builder_edited`` instead of flipping to ``builder``.
"""

from api.utils.workflow_origin import (
    CREATED_VIA_BLANK,
    CREATED_VIA_BUILDER,
    CREATED_VIA_TEMPLATE,
    CREATED_VIA_UNKNOWN,
    resolve_workflow_origin,
)


def test_builder_created_agent():
    assert resolve_workflow_origin("builder", True) == (CREATED_VIA_BUILDER, False)


def test_blank_agent_edited_with_builder_keeps_origin():
    # Created blank, then opened in the builder (which wrote builder_sessions).
    assert resolve_workflow_origin("blank", True) == (CREATED_VIA_BLANK, True)


def test_template_agent_never_touched_by_builder():
    assert resolve_workflow_origin("template", False) == (CREATED_VIA_TEMPLATE, False)


def test_blank_agent_never_touched_by_builder():
    assert resolve_workflow_origin("blank", False) == (CREATED_VIA_BLANK, False)


def test_historical_builder_workflow_without_stamp():
    # Pre-stamp builder workflow: no created_via, but has a builder session.
    assert resolve_workflow_origin(None, True) == (CREATED_VIA_BUILDER, False)


def test_historical_plain_workflow_without_stamp():
    assert resolve_workflow_origin(None, False) == (CREATED_VIA_UNKNOWN, False)
