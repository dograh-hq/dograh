from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.workflow.run_creation import prepare_workflow_run_inputs


def _workflow():
    return SimpleNamespace(
        id=33,
        template_context_variables={"name": "workflow", "workflow_only": "kept"},
        released_definition=SimpleNamespace(
            id=77,
            template_context_variables={
                "name": "published",
                "published_only": "kept",
            },
        ),
        current_definition=None,
    )


@pytest.mark.asyncio
async def test_prepare_inputs_uses_draft_and_merges_template_context():
    draft = SimpleNamespace(
        id=88,
        template_context_variables={"name": "draft", "draft_only": "kept"},
    )
    workflow_client = SimpleNamespace(get_draft_version=AsyncMock(return_value=draft))

    run_inputs = await prepare_workflow_run_inputs(
        workflow_client,
        _workflow(),
        initial_context={"name": "explicit", "explicit_only": "kept"},
        use_draft=True,
        include_template_context=True,
    )

    assert run_inputs.definition_id == 88
    assert run_inputs.initial_context == {
        "name": "explicit",
        "draft_only": "kept",
        "explicit_only": "kept",
    }
    workflow_client.get_draft_version.assert_awaited_once_with(33)


@pytest.mark.asyncio
async def test_prepare_inputs_does_not_check_draft_or_merge_templates_by_default():
    workflow_client = SimpleNamespace(get_draft_version=AsyncMock())

    run_inputs = await prepare_workflow_run_inputs(
        workflow_client,
        _workflow(),
        initial_context={"name": "explicit"},
    )

    assert run_inputs.definition_id == 77
    assert run_inputs.initial_context == {"name": "explicit"}
    workflow_client.get_draft_version.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_inputs_falls_back_to_published_when_draft_missing():
    workflow_client = SimpleNamespace(get_draft_version=AsyncMock(return_value=None))

    run_inputs = await prepare_workflow_run_inputs(
        workflow_client,
        _workflow(),
        initial_context={"name": "explicit"},
        use_draft=True,
        include_template_context=True,
    )

    assert run_inputs.definition_id == 77
    assert run_inputs.initial_context == {
        "name": "explicit",
        "published_only": "kept",
    }
