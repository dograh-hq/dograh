"""Test that add_call_disposition_code correctly persists changes.

The bug: `codes` is a reference to the list inside the JSON column value.
Calling `codes.append()` mutates the in-memory column value in-place.
When SQLAlchemy compares old vs new on commit, it sees them as equal
because the old value was already mutated — so the change is silently dropped.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.db.workflow_client import WorkflowClient


def _make_workflow_stub(initial_disposition_codes):
    """Create a mock workflow that behaves like a SQLAlchemy model instance.

    Tracks attribute assignments so we can verify the new value is genuinely
    different from the original (which is what SQLAlchemy needs to detect a change).
    """
    workflow = MagicMock()
    # Store the initial value and track what gets assigned
    workflow.call_disposition_codes = initial_disposition_codes
    workflow._assigned_values = {}

    original_setattr = type(workflow).__setattr__

    def tracking_setattr(self, name, value):
        if name == "call_disposition_codes":
            self._assigned_values[name] = value
        original_setattr(self, name, value)

    type(workflow).__setattr__ = tracking_setattr
    return workflow


@pytest.fixture
def client():
    with patch("api.db.workflow_client.BaseDBClient.__init__", return_value=None):
        c = WorkflowClient()
        c.async_session = MagicMock()
        return c


def test_disposition_code_new_value_is_not_same_reference(client):
    """The assigned list must NOT be the same object as the original.

    If it is, SQLAlchemy won't detect the change because old == new
    (the old was mutated in-place).
    """
    initial_codes = {"disposition_codes": ["existing_code"]}
    original_list = initial_codes["disposition_codes"]

    workflow = MagicMock()
    workflow.call_disposition_codes = initial_codes

    # Mock the session and query
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = workflow
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    client.async_session = MagicMock(return_value=mock_session)

    asyncio.get_event_loop().run_until_complete(
        client.add_call_disposition_code(workflow_id=1, disposition_code="new_code")
    )

    # Verify the disposition code was added
    assigned = workflow.call_disposition_codes
    assert "new_code" in assigned["disposition_codes"]

    # THE CRITICAL CHECK: the list inside the assigned value must be a *different*
    # object from the original list. If it's the same object, SQLAlchemy's change
    # detection won't work because the "old" value was mutated in-place.
    assert assigned["disposition_codes"] is not original_list, (
        "The assigned disposition_codes list is the same object as the original. "
        "This means SQLAlchemy won't detect the change because the old value "
        "was mutated in-place via list.append()."
    )
