from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.services.workflow.qa import analysis, node_summary


@pytest.mark.asyncio
async def test_per_node_qa_uses_qa_analysis_context():
    qa_data = SimpleNamespace(qa_system_prompt="Review the call")
    workflow_run = SimpleNamespace(
        logs={"realtime_feedback_events": [{"type": "transcript"}]},
        initial_context={},
    )
    llm_factory = Mock(return_value=object())

    with (
        patch.object(
            analysis,
            "split_events_by_node",
            return_value=[("start", "Start", [{"type": "transcript"}])],
        ),
        patch.object(
            analysis,
            "build_conversation_structure",
            return_value=[{"role": "user", "content": "hello"}],
        ),
        patch.object(analysis, "format_transcript", return_value="user: hello"),
        patch.object(analysis, "compute_call_metrics", return_value={}),
        patch.object(
            analysis,
            "resolve_llm_config",
            new=AsyncMock(return_value=("dograh", "default", "mps-key", {})),
        ),
        patch.object(
            analysis,
            "ensure_node_summaries",
            new=AsyncMock(return_value={"start": {"summary": "Start node"}}),
        ),
        patch.object(analysis, "create_llm_service_from_provider", llm_factory),
        patch.object(
            analysis,
            "_run_llm_inference",
            new=AsyncMock(return_value='{"tags": [], "summary": "ok"}'),
        ),
        patch.object(analysis, "setup_langfuse_parent_context", return_value=None),
        patch.object(analysis, "add_qa_span_to_trace"),
    ):
        await analysis.run_per_node_qa_analysis(
            qa_data,
            workflow_run,
            workflow_run_id=123,
            workflow_definition={},
            definition_id=None,
        )

    assert llm_factory.call_args.kwargs["usage_context"] == "qa_analysis"


@pytest.mark.asyncio
async def test_node_summary_uses_qa_analysis_context():
    qa_data = SimpleNamespace()
    workflow_run = SimpleNamespace(initial_context={}, workflow=None)
    workflow_definition = {
        "nodes": [
            {
                "id": "start",
                "type": "startCall",
                "data": {
                    "name": "Start",
                    "prompt": "Help the caller",
                    "tool_uuids": [],
                },
            }
        ],
        "edges": [],
    }
    llm = SimpleNamespace(run_inference=AsyncMock(return_value="A helpful agent"))
    llm_factory = Mock(return_value=llm)

    with (
        patch.object(
            node_summary,
            "resolve_llm_config",
            new=AsyncMock(return_value=("dograh", "default", "mps-key", {})),
        ),
        patch.object(node_summary, "create_llm_service_from_provider", llm_factory),
        patch.object(node_summary, "create_node_summary_trace", return_value=None),
    ):
        result = await node_summary.ensure_node_summaries(
            workflow_definition,
            definition_id=None,
            workflow_run=workflow_run,
            qa_data=qa_data,
        )

    assert result["start"]["summary"] == "A helpful agent"
    assert llm_factory.call_args.kwargs["usage_context"] == "qa_analysis"
