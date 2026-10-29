"""Tuner consumes persisted SIP metadata and only exports correlation headers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.integrations.base import (
    IntegrationCompletionContext,
    IntegrationRuntimeContext,
)
from api.services.integrations.tuner.completion import run_completion
from api.services.integrations.tuner.runtime import create_runtime_sessions
from api.services.integrations.tuner.sip import get_sip_metadata

SIP_CALL_ID = "caller-sip-id"
CORRELATION_ID = "simulation-correlation-id"


def _run(*, sip_call_id=SIP_CALL_ID, headers=None, mode="twilio"):
    return SimpleNamespace(
        id=42,
        mode=mode,
        gathered_context={"call_id": "carrier-id", "sip_call_id": sip_call_id},
        logs={"inbound_webhook": {"sip_headers": headers or {}}},
        recording_url=None,
        usage_info=None,
    )


def _session(run):
    return create_runtime_sessions(
        IntegrationRuntimeContext(
            workflow_run_id=run.id,
            workflow_run=run,
            workflow_graph=SimpleNamespace(
                nodes={
                    "tuner": SimpleNamespace(
                        node_type="tuner", data=SimpleNamespace(tuner_enabled=True)
                    )
                }
            ),
            run_definition=SimpleNamespace(version_number=1),
            user_config=SimpleNamespace(stt=None, llm=None, tts=None),
            is_realtime=False,
            context_messages_provider=lambda: [],
        )
    )[0]


@pytest.mark.parametrize("mode", ["twilio", "cloudonix", "another-provider"])
def test_tuner_reads_normalized_metadata_without_provider_dispatch(mode):
    run = _run(mode=mode, headers={"X-Correlation-Id": CORRELATION_ID})
    assert get_sip_metadata(run) == (SIP_CALL_ID, {"X-Correlation-Id": CORRELATION_ID})


def test_tuner_exports_only_non_empty_correlation_headers():
    run = _run(
        headers={
            "CID": SIP_CALL_ID,
            "CORRELATION-ID": CORRELATION_ID,
            "Call-ID": "",
            "x-correlation-id": None,
            "Cloudonix-Signature": "provider-secret",
            "Cloudonix-IP": "203.0.113.10",
            "Twilio-AccountSid": "AC123",
            "X-LiveKit-Room": "room-id",
        }
    )
    assert get_sip_metadata(run) == (
        SIP_CALL_ID,
        {"CID": SIP_CALL_ID, "CORRELATION-ID": CORRELATION_ID},
    )


@pytest.mark.parametrize("sip_call_id", [None, "", 42, [SIP_CALL_ID]])
def test_tuner_does_not_substitute_carrier_id_or_header_for_missing_sip_id(sip_call_id):
    run = _run(sip_call_id=sip_call_id, headers={"X-Correlation-Id": CORRELATION_ID})
    assert get_sip_metadata(run) == (None, {"X-Correlation-Id": CORRELATION_ID})


@pytest.mark.parametrize(
    "run",
    [
        SimpleNamespace(),
        SimpleNamespace(gathered_context=None, logs=None),
        SimpleNamespace(gathered_context={"call_id": "CA123"}, logs={}),
        SimpleNamespace(
            logs={
                "inbound_webhook": {
                    "raw_webhook_data": {
                        "SipCallId": SIP_CALL_ID,
                        "SessionData": {"callIds": [SIP_CALL_ID]},
                    }
                }
            }
        ),
    ],
)
def test_runs_without_normalized_metadata_pass_through(run):
    # No provider-payload fallback: older snapshots remain deliverable as stored.
    assert get_sip_metadata(run) == (None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("sip_call_id", [SIP_CALL_ID, None])
async def test_runtime_snapshot_uses_stored_sip_identifier(sip_call_id):
    run = _run(sip_call_id=sip_call_id)
    run.logs["inbound_webhook"]["raw_webhook_data"] = {"SipCallId": "ignored-raw-id"}
    snapshot = await asyncio.wait_for(
        _session(run).on_call_finished(
            gathered_context={"sip_call_id": "ignored-extracted-id"}
        ),
        timeout=5,
    )
    payload = snapshot["tuner_payload"]
    assert payload["call_id"] == "42"
    if sip_call_id:
        assert payload["sip_call_id"] == sip_call_id
    else:
        assert "sip_call_id" not in payload
    assert "sip_headers" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["twilio", "cloudonix", "plivo"])
async def test_inbound_metadata_survives_run_creation_and_tuner_delivery(provider_name):
    from api.routes.telephony import _create_inbound_workflow_run
    from api.services.telephony import registry

    webhook = {
        "CallSid": "carrier-id",
        "CallUUID": "carrier-id",
        "Direction": "inbound",
        "SipCallId": SIP_CALL_ID,
        "SipHeader_X-Correlation-Id": CORRELATION_ID,
        "SipHeader_X-Private": "provider-secret",
        "SessionData": {
            "callIds": [SIP_CALL_ID],
            "profile": {
                "trunk-sip-headers": {
                    "Correlation-Id": CORRELATION_ID,
                    "Cloudonix-Signature": "provider-secret",
                }
            },
        },
    }
    normalized = registry.get(provider_name).provider_cls.parse_inbound_webhook(webhook)
    workflow = SimpleNamespace(id=33, organization_id=11)
    run_inputs = SimpleNamespace(definition_id=77, use_draft=False)
    create_run = AsyncMock(return_value=SimpleNamespace(id=42))
    with (
        patch(
            "api.routes.telephony.db_client.get_workflow",
            AsyncMock(return_value=workflow),
        ),
        patch(
            "api.routes.telephony.prepare_workflow_run_inputs",
            AsyncMock(return_value=run_inputs),
        ),
        patch("api.routes.telephony.db_client.create_workflow_run", create_run),
    ):
        run_id = await asyncio.wait_for(
            _create_inbound_workflow_run(
                workflow_id=33,
                user_id=7,
                organization_id=11,
                provider=provider_name,
                normalized_data=normalized,
                telephony_configuration_id=55,
            ),
            timeout=5,
        )

    stored = create_run.await_args.kwargs
    assert run_id == 42
    assert stored["gathered_context"]["call_id"] == "carrier-id"
    assert stored["organization_id"] == 11
    assert stored["initial_context"]["telephony_configuration_id"] == 55
    if provider_name == "plivo":
        assert "sip_call_id" not in stored["gathered_context"]
        assert "sip_headers" not in stored["logs"]["inbound_webhook"]
    else:
        assert stored["gathered_context"]["sip_call_id"] == SIP_CALL_ID
        assert (
            stored["logs"]["inbound_webhook"]["sip_headers"] == normalized.sip_headers
        )

    run = _run(mode=provider_name)
    run.gathered_context = stored["gathered_context"]
    run.logs = stored["logs"]
    snapshot = await asyncio.wait_for(
        _session(run).on_call_finished(
            gathered_context={"call_disposition": "completed"}
        ),
        timeout=5,
    )
    run.logs.update(snapshot)
    post_call = AsyncMock(return_value={"status": "delivered"})
    with patch("api.services.integrations.tuner.completion.post_call", post_call):
        result = await asyncio.wait_for(
            run_completion(
                [
                    {
                        "id": "tuner",
                        "data": {
                            "name": "Tuner",
                            "tuner_api_key": "test-key",
                            "tuner_workspace_id": 1,
                            "tuner_agent_id": "agent-1",
                        },
                    }
                ],
                IntegrationCompletionContext(
                    workflow_run_id=42,
                    workflow_run=run,
                    workflow_definition={},
                    definition_id=77,
                    organization_id=11,
                    public_token=None,
                ),
            ),
            timeout=5,
        )

    assert result["tuner_tuner"]["status"] == "delivered"
    post_call.assert_awaited_once()
    payload = post_call.await_args.args[1]
    assert payload["call_id"] == "42"
    if provider_name == "plivo":
        assert "sip_call_id" not in payload
        assert "sip_headers" not in payload
    else:
        assert payload["sip_call_id"] == SIP_CALL_ID
        header = "X-Correlation-Id" if provider_name == "twilio" else "Correlation-Id"
        assert payload["sip_headers"] == {header: CORRELATION_ID}
    assert "provider-secret" not in str(payload)
