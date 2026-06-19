"""Regression test for QA analysis when the LLM returns a non-dict JSON value.

``parse_llm_json`` is explicitly designed to return a list when the model emits
a top-level JSON array (see ``test_json_parser.py``). The QA analyzers then call
``parsed.get(...)`` on the result. For a list that raises ``AttributeError``,
which is NOT caught by the surrounding ``except (json.JSONDecodeError, ValueError)``
— so a stray array response crashed the whole QA run instead of degrading to
empty results.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.services.workflow.qa import analysis as qa_analysis


@pytest.mark.asyncio
async def test_whole_call_qa_tolerates_array_llm_response():
    """A top-level JSON array from the QA LLM degrades to empty results."""
    qa_data = SimpleNamespace(qa_system_prompt="Summarize: {transcript}")
    workflow_run = SimpleNamespace(
        logs={
            "realtime_feedback_events": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ]
        },
        usage_info={"call_duration_seconds": 12},
    )
    warning_mock = Mock()

    with (
        patch.object(
            qa_analysis, "build_conversation_structure", return_value=[{"x": 1}]
        ),
        patch.object(qa_analysis, "format_transcript", return_value="user: hello"),
        patch.object(qa_analysis, "compute_call_metrics", return_value={}),
        patch.object(
            qa_analysis,
            "resolve_llm_config",
            new=AsyncMock(return_value=("openai", "gpt-4o", "sk-test", {})),
        ),
        patch.object(
            qa_analysis, "create_llm_service_from_provider", return_value=object()
        ),
        patch.object(
            qa_analysis,
            "_run_llm_inference",
            new=AsyncMock(return_value='["tag1", "tag2"]'),
        ),
        patch.object(qa_analysis, "setup_langfuse_parent_context", return_value=None),
        patch.object(qa_analysis, "add_qa_span_to_trace", return_value=None),
        patch.object(qa_analysis.logger, "warning", warning_mock),
    ):
        # Before the fix this raised AttributeError: 'list' object has no
        # attribute 'get'.
        result = await qa_analysis._run_whole_call_qa_analysis(
            qa_data, workflow_run, workflow_run_id=99
        )

    node_result = result["node_results"]["whole_call"]
    assert node_result["tags"] == []
    assert node_result["summary"] == ""
    assert node_result["score"] is None
    warning_mock.assert_called_once()
    warning_message = warning_mock.call_args.args[0]
    assert "non-object JSON" in warning_message
    assert "run 99" in warning_message
    assert "list" in warning_message
    assert "tag1" not in warning_message
