from types import SimpleNamespace
from typing import Any

import pytest

from api.services.workflow.tools.transfer_resolver import (
    TransferResolutionError,
    resolve_transfer_config,
)


async def _resolve(
    *,
    rules: list[dict[str, Any]],
    initial_context: dict[str, Any] | None = None,
    gathered_context: dict[str, Any] | None = None,
    fallback_destination: str | None = None,
):
    mapping: dict[str, Any] = {"rules": rules}
    if fallback_destination is not None:
        mapping["fallback_destination"] = fallback_destination
    return await resolve_transfer_config(
        tool=SimpleNamespace(tool_uuid="context-transfer"),
        config={
            "destination_source": "context_mapping",
            "context_mapping": mapping,
        },
        arguments={},
        call_context_vars=initial_context,
        gathered_context_vars=gathered_context,
        organization_id=None,
        workflow_run_id=11,
    )


def _rule(path: str, *routes: tuple[str, str]) -> dict[str, Any]:
    return {
        "context_path": path,
        "routes": [
            {"context_value": context_value, "destination": destination}
            for context_value, destination in routes
        ],
    }


@pytest.mark.asyncio
async def test_unprefixed_path_prefers_gathered_context_over_initial_context():
    resolved = await _resolve(
        rules=[
            _rule(
                "department",
                ("support", "PJSIP/1001"),
                ("sales", "PJSIP/1002"),
            )
        ],
        initial_context={"department": "sales"},
        gathered_context={"department": "support"},
    )

    assert resolved.destination == "PJSIP/1001"


@pytest.mark.asyncio
async def test_unprefixed_path_falls_back_to_initial_context():
    resolved = await _resolve(
        rules=[_rule("routing.department", ("sales", "+14155550123"))],
        initial_context={"routing": {"department": "sales"}},
        gathered_context={},
    )

    assert resolved.destination == "+14155550123"


@pytest.mark.asyncio
async def test_unprefixed_extracted_variable_still_precedes_initial_context():
    resolved = await _resolve(
        rules=[
            _rule(
                "department",
                ("support", "PJSIP/1001"),
                ("sales", "PJSIP/1002"),
            )
        ],
        initial_context={"department": "sales"},
        gathered_context={"extracted_variables": {"department": "support"}},
    )

    assert resolved.destination == "PJSIP/1001"


@pytest.mark.asyncio
async def test_explicit_gathered_context_path_does_not_fall_back_to_initial_context():
    with pytest.raises(TransferResolutionError) as exc_info:
        await _resolve(
            rules=[_rule("gathered_context.department", ("sales", "+14155550123"))],
            initial_context={"department": "sales"},
            gathered_context={},
        )

    assert exc_info.value.reason == "no_context_mapping_match"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination",
    ["sales", "PJSIP/1001", "+14155550123"],
    ids=["vicidial-ingroup", "sip-extension", "pstn-number"],
)
async def test_context_mapping_accepts_provider_destination_formats(destination: str):
    resolved = await _resolve(
        rules=[_rule("department", ("sales", destination))],
        gathered_context={"department": "sales"},
    )

    assert resolved.destination == destination


@pytest.mark.asyncio
async def test_matched_destination_can_be_rendered_from_initial_context():
    resolved = await _resolve(
        rules=[
            _rule(
                "department",
                ("sales", "{{initial_context.transfer_destination}}"),
            )
        ],
        initial_context={"transfer_destination": "+14155550123"},
        gathered_context={"department": "sales"},
    )

    assert resolved.destination == "+14155550123"


@pytest.mark.asyncio
async def test_fallback_destination_can_be_rendered_from_initial_context():
    resolved = await _resolve(
        rules=[_rule("department", ("sales", "+14155550123"))],
        initial_context={"fallback_destination": "PJSIP/1001"},
        gathered_context={"department": "support"},
        fallback_destination="{{initial_context.fallback_destination}}",
    )

    assert resolved.destination == "PJSIP/1001"
    assert resolved.metadata["fallback"] is True


@pytest.mark.asyncio
async def test_empty_rendered_destination_fails_instead_of_dialing_blank_value():
    with pytest.raises(TransferResolutionError) as exc_info:
        await _resolve(
            rules=[
                _rule(
                    "department",
                    ("sales", "{{initial_context.transfer_destination}}"),
                )
            ],
            gathered_context={"department": "sales"},
        )

    assert exc_info.value.reason == "no_destination"
