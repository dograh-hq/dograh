import httpx
import pytest

from api.services.pipecat import pre_call_fetch as service
from api.services.pipecat.pre_call_fetch import _extract_initial_context


@pytest.mark.asyncio
@pytest.mark.parametrize("nested", [False, True])
async def test_hook_keeps_model_overrides_separate_and_sends_stable_run_key(
    monkeypatch, nested
):
    response_data = {
        "initial_context": {"customer": "Jane", "model_overrides": {"injected": True}},
        "model_overrides": {"tts": {"settings": {"voice": "Alice"}}},
    }
    if nested:
        response_data = {"call_inbound": response_data}
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_data)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: client)
    result = await service.execute_pre_call_fetch_result(
        url="https://hook.example/lookup",
        credential_uuid=None,
        call_context_vars={
            "caller_number": "+15550001111",
            "called_number": "+15550002222",
        },
        workflow_id=7,
        workflow_run_id=51,
        organization_id=1,
    )
    assert result.initial_context == {"customer": "Jane"}
    assert result.model_overrides == {"tts": {"settings": {"voice": "Alice"}}}
    assert result.outcome == "completed"
    assert requests[0].headers["Idempotency-Key"] == "dograh-pre-call-51"
    assert b'"workflow_run_id":51' in requests[0].content


@pytest.mark.asyncio
async def test_invalid_hook_model_envelope_does_not_become_best_effort_success(
    monkeypatch,
):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"model_overrides": "invalid"})
        )
    )
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: client)
    with pytest.raises(service.PreCallFetchConfigurationError):
        await service.execute_pre_call_fetch_result(
            url="https://hook.example/lookup",
            credential_uuid=None,
            call_context_vars={},
            workflow_id=7,
            workflow_run_id=51,
            organization_id=1,
        )


@pytest.mark.asyncio
async def test_hook_timeout_preserves_best_effort_context_policy(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: client)
    result = await service.execute_pre_call_fetch_result(
        url="https://hook.example/lookup",
        credential_uuid=None,
        call_context_vars={},
        workflow_id=7,
        workflow_run_id=51,
        organization_id=1,
    )
    assert result.outcome == "unavailable"
    assert result.model_overrides is None


class TestExtractInitialContext:
    """Tests for _extract_initial_context, the pre-call fetch response parser."""

    def test_initial_context_nested_under_call_inbound(self):
        """The canonical `initial_context` key nested under `call_inbound`."""
        response = {"call_inbound": {"initial_context": {"customer_name": "Jane"}}}
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_initial_context_at_top_level(self):
        """The canonical `initial_context` key at the top level."""
        response = {"initial_context": {"customer_name": "Jane"}}
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_legacy_dynamic_variables_nested(self):
        """The legacy `dynamic_variables` key still works nested under `call_inbound`."""
        response = {"call_inbound": {"dynamic_variables": {"customer_name": "Jane"}}}
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_legacy_dynamic_variables_at_top_level(self):
        """The legacy `dynamic_variables` key still works at the top level."""
        response = {"dynamic_variables": {"customer_name": "Jane"}}
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_initial_context_takes_precedence_over_legacy(self):
        """When both keys are present, `initial_context` wins."""
        response = {
            "call_inbound": {
                "initial_context": {"source": "new"},
                "dynamic_variables": {"source": "legacy"},
            }
        }
        assert _extract_initial_context(response) == {"source": "new"}

    def test_falls_back_to_legacy_when_initial_context_not_a_dict(self):
        """A non-dict `initial_context` falls back to `dynamic_variables`."""
        response = {
            "initial_context": None,
            "dynamic_variables": {"customer_name": "Jane"},
        }
        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_nested_values_preserved(self):
        """Nested objects pass through untouched for dot-notation access."""
        response = {
            "call_inbound": {
                "initial_context": {"customer": {"address": {"city": "LA"}}}
            }
        }
        assert _extract_initial_context(response) == {
            "customer": {"address": {"city": "LA"}}
        }

    def test_reserved_run_metadata_is_dropped(self):
        response = {
            "initial_context": {
                "customer_name": "Jane",
                "provider": "external-provider",
                "runtime_configuration": {"llm_model": "external-model"},
                "mps_correlation_id": "external-correlation-id",
            }
        }

        assert _extract_initial_context(response) == {"customer_name": "Jane"}

    def test_greeting_override_is_accepted_from_pre_call_fetch(self):
        response = {
            "initial_context": {
                "account_id": "ACC-123",
                "greeting_override": {
                    "type": "text",
                    "text": "Welcome back to account {{account_id}}",
                },
            }
        }

        assert _extract_initial_context(response) == response["initial_context"]

    def test_empty_when_no_known_keys(self):
        """A response with neither key yields an empty dict."""
        assert _extract_initial_context({"call_inbound": {"agent_id": 1}}) == {}

    def test_empty_when_call_inbound_missing(self):
        """No `call_inbound` and no top-level keys yields an empty dict."""
        assert _extract_initial_context({}) == {}

    def test_non_dict_vars_yield_empty(self):
        """A non-dict value under a known key yields an empty dict."""
        assert _extract_initial_context({"initial_context": "nope"}) == {}
