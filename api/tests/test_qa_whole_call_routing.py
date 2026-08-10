"""Regression test for the whole-call QA grading toggle routing.

When ``qa_grade_whole_call`` is set on the QA node, ``run_per_node_qa_analysis``
must short-circuit to ``_run_whole_call_qa_analysis`` and return its result
*without* splitting the transcript by node. Existing tests cover the per-node
flow and the whole-call helper in isolation, but not this routing decision — so
a change that skipped the branch, altered its arguments, or fell through to node
splitting would go undetected.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.workflow.qa import analysis as qa_analysis


@pytest.mark.asyncio
async def test_whole_call_toggle_routes_and_bypasses_node_splitting():
    """qa_grade_whole_call -> whole-call helper is returned, node splitting skipped."""
    qa_data = SimpleNamespace(
        qa_grade_whole_call=True,
        qa_system_prompt="grade: {transcript}",
    )
    workflow_run = SimpleNamespace(
        logs={
            "realtime_feedback_events": [
                {"role": "user", "content": "hello", "node_id": "1"},
                {"role": "assistant", "content": "hi there", "node_id": "1"},
            ]
        },
    )
    sentinel = {"node_results": {"whole_call": {"summary": "ok"}}, "model": "test-model"}

    with (
        patch.object(
            qa_analysis,
            "_run_whole_call_qa_analysis",
            new=AsyncMock(return_value=sentinel),
        ) as whole_call_mock,
        patch.object(qa_analysis, "split_events_by_node") as split_mock,
    ):
        result = await qa_analysis.run_per_node_qa_analysis(
            qa_data,
            workflow_run,
            workflow_run_id=123,
            workflow_definition={"nodes": []},
            definition_id=7,
        )

    # the whole-call result is returned verbatim ...
    assert result is sentinel
    whole_call_mock.assert_awaited_once_with(qa_data, workflow_run, 123)
    # ... and node splitting never ran
    split_mock.assert_not_called()


@pytest.mark.asyncio
async def test_default_reaches_node_splitting():
    """Without the toggle, the per-node path calls node splitting (not the toggle route)."""
    qa_data = SimpleNamespace(
        qa_grade_whole_call=False,
        qa_system_prompt="grade: {transcript}",
    )
    workflow_run = SimpleNamespace(
        logs={
            "realtime_feedback_events": [
                {"role": "user", "content": "hello", "node_id": "1"},
            ]
        },
    )

    # Return no splits: node splitting IS reached (proving the toggle branch was
    # skipped), and the documented "no node_id -> whole-call" fallback then runs.
    with patch.object(
        qa_analysis, "split_events_by_node", return_value=[]
    ) as split_mock:
        with patch.object(
            qa_analysis,
            "_run_whole_call_qa_analysis",
            new=AsyncMock(return_value={"node_results": {}}),
        ):
            await qa_analysis.run_per_node_qa_analysis(
                qa_data,
                workflow_run,
                workflow_run_id=124,
                workflow_definition={"nodes": []},
                definition_id=8,
            )

    split_mock.assert_called_once()
