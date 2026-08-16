from types import SimpleNamespace

import pytest

from api.services.workflow.pipecat_engine_context_composer import (
    RECORDING_RESPONSE_MODE_INSTRUCTIONS,
    compose_functions_for_node,
    compose_system_prompt_for_node,
)


TRANSITION_GUIDANCE = """\
NODE TRANSITION INSTRUCTIONS - MANDATORY:
Evaluate the live conversation against the conditions described by the available transition tools. As soon as a condition is met, immediately call the matching transition tool."""


def make_edge(name: str, condition: str) -> SimpleNamespace:
    return SimpleNamespace(
        condition=condition,
        get_function_name=lambda: name,
    )


def make_node(
    prompt: str,
    *,
    out_edges: list[SimpleNamespace],
    add_global_prompt: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        prompt=prompt,
        out_edges=out_edges,
        add_global_prompt=add_global_prompt,
        document_uuids=None,
        tool_uuids=None,
    )


def make_workflow(global_prompt: str | None = None) -> SimpleNamespace:
    global_node_id = "global" if global_prompt else None
    nodes = {"global": SimpleNamespace(prompt=global_prompt)} if global_prompt else {}
    return SimpleNamespace(global_node_id=global_node_id, nodes=nodes)


def test_prompt_with_outgoing_edge_includes_mandatory_transition_guidance():
    node = make_node(
        "Help the caller schedule an appointment.",
        out_edges=[make_edge("confirm_appointment", "The appointment is confirmed")],
    )

    prompt = compose_system_prompt_for_node(
        node=node,
        workflow=make_workflow(),
        format_prompt=lambda value: value,
        has_recordings=False,
    )

    assert prompt == (
        f"Help the caller schedule an appointment.\n\n{TRANSITION_GUIDANCE}"
    )


@pytest.mark.asyncio
async def test_multiple_edges_keep_conditions_in_transition_tool_schemas():
    conditions = [
        "The caller wants help with billing",
        "The caller wants technical support",
    ]
    node = make_node(
        "Determine why the caller contacted us.",
        out_edges=[
            make_edge("billing", conditions[0]),
            make_edge("technical_support", conditions[1]),
        ],
    )

    prompt = compose_system_prompt_for_node(
        node=node,
        workflow=make_workflow(),
        format_prompt=lambda value: value,
        has_recordings=False,
    )
    functions = await compose_functions_for_node(
        node=node,
        custom_tool_manager=None,
    )

    assert TRANSITION_GUIDANCE in prompt
    assert all(condition not in prompt for condition in conditions)
    assert [function.description for function in functions] == conditions


def test_prompt_without_outgoing_edges_omits_transition_guidance():
    node = make_node("Thank the caller and end the call.", out_edges=[])

    prompt = compose_system_prompt_for_node(
        node=node,
        workflow=make_workflow(),
        format_prompt=lambda value: value,
        has_recordings=False,
    )

    assert prompt == "Thank the caller and end the call."
    assert TRANSITION_GUIDANCE not in prompt


def test_transition_guidance_preserves_global_node_and_recording_order():
    node = make_node(
        "Play RECORDING_ID: greeting when the caller answers.",
        out_edges=[make_edge("continue", "The greeting is complete")],
        add_global_prompt=True,
    )

    prompt = compose_system_prompt_for_node(
        node=node,
        workflow=make_workflow("Always be concise."),
        format_prompt=lambda value: value,
        has_recordings=True,
    )

    global_index = prompt.index("Always be concise.")
    node_index = prompt.index("Play RECORDING_ID: greeting when the caller answers.")
    transition_index = prompt.index(TRANSITION_GUIDANCE)
    recording_index = prompt.index(RECORDING_RESPONSE_MODE_INSTRUCTIONS)

    assert global_index < node_index < transition_index < recording_index
