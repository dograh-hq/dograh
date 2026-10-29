"""MPS utilities use existing organization-owned Dograh connections."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from api.services import mps_service_key_client as client_module
from api.services.configuration import ai_model_configuration
from api.services.mps_service_key_client import MPSServiceKeyClient


@pytest.fixture
def custom_models(monkeypatch):
    effective = SimpleNamespace(
        llm=SimpleNamespace(provider="openai"),
        tts=SimpleNamespace(provider="elevenlabs"),
        stt=SimpleNamespace(provider="deepgram"),
        embeddings=SimpleNamespace(provider="openai"),
    )
    monkeypatch.setattr(
        ai_model_configuration,
        "get_resolved_ai_model_configuration",
        AsyncMock(return_value=SimpleNamespace(effective=effective)),
    )
    return effective


@pytest.fixture
def connections(monkeypatch):
    lookup = AsyncMock(
        return_value=[
            SimpleNamespace(provider="openai", credentials={"api_key": "other-key"}),
            SimpleNamespace(provider="dograh", credentials={"api_key": "mps_sk_saved"}),
        ]
    )
    monkeypatch.setattr(client_module.db_client, "list_provider_connections", lookup)
    return lookup


@pytest.mark.parametrize("mode", ["saas", "oss"])
@pytest.mark.parametrize("operation", ["transcribe", "document", "workflow"])
async def test_utilities_work_with_custom_defaults(
    monkeypatch, tmp_path, custom_models, connections, mode, operation
):
    monkeypatch.setattr(client_module, "DEPLOYMENT_MODE", mode)
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("correlation-id/self"):
            return httpx.Response(200, json={"correlation_id": "server-correlation"})
        return httpx.Response(200, json={"ok": True})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = MPSServiceKeyClient()
    mint = AsyncMock()
    monkeypatch.setattr(client, "create_service_key", mint)
    if operation == "transcribe":
        result = await client.transcribe_audio(b"audio", organization_id=42)
        assert len(requests) == 2
        assert b"server-correlation" in requests[-1].content
    elif operation == "document":
        document = tmp_path / "document.txt"
        document.write_text("Test document")
        result = await client.process_document(
            str(document), "document.txt", "text/plain", organization_id=42
        )
    else:
        result = await client.call_workflow_api(
            "INBOUND", "support", "Answer questions", organization_id=42
        )
    assert result == {"ok": True}
    connections.assert_awaited_once_with(42, active_only=True)
    assert all(
        request.headers["Authorization"] == "Bearer mps_sk_saved"
        and "X-Secret-Key" not in request.headers
        and "X-Organization-Id" not in request.headers
        for request in requests
    )
    mint.assert_not_awaited()


async def test_selected_dograh_key_takes_precedence(custom_models, connections):
    custom_models.llm = SimpleNamespace(provider="dograh", api_key="mps_sk_selected")
    assert await MPSServiceKeyClient()._runtime_service_key(42) == "mps_sk_selected"
    connections.assert_not_awaited()


async def test_oss_workflow_without_organization_uses_legacy_owner_headers(
    monkeypatch,
):
    monkeypatch.setattr(client_module, "DEPLOYMENT_MODE", "oss")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200, json={"workflow_definition": {"nodes": [], "edges": []}}
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        client_module.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    client = MPSServiceKeyClient()
    lookup = AsyncMock()
    monkeypatch.setattr(client, "_runtime_service_key", lookup)
    result = await client.call_workflow_api(
        "INBOUND", "support", "Answer questions", created_by="oss_1_user"
    )
    assert result == {"workflow_definition": {"nodes": [], "edges": []}}
    assert len(requests) == 1
    assert requests[0].headers["X-Created-By"] == "oss_1_user"
    assert "Authorization" not in requests[0].headers
    assert "X-Secret-Key" not in requests[0].headers
    assert "X-Stack-Team-Id" not in requests[0].headers
    lookup.assert_not_awaited()


@pytest.mark.parametrize("mode,created_by", [("saas", "stack-user"), ("oss", None)])
async def test_workflow_without_organization_rejects_missing_oss_identity(
    monkeypatch, mode, created_by
):
    monkeypatch.setattr(client_module, "DEPLOYMENT_MODE", mode)
    with pytest.raises(HTTPException) as error:
        await MPSServiceKeyClient().call_workflow_api(
            "INBOUND", "support", "Answer questions", created_by=created_by
        )
    assert error.value.status_code == 400
    assert error.value.detail == "No organization selected"


async def test_utility_fallback_reads_current_credentials(custom_models, connections):
    connection = connections.return_value[-1]
    connection.credentials["api_key"] = ["mps_sk_first", "mps_sk_second"]
    client = MPSServiceKeyClient()
    assert await client._runtime_service_key(42) == "mps_sk_first"
    connection.credentials["api_key"] = "mps_sk_rotated"
    assert await client._runtime_service_key(42) == "mps_sk_rotated"
    assert connections.await_count == 2


async def test_utility_fallback_is_scoped_to_each_organization(
    custom_models, connections
):
    async def scoped_connections(organization_id, *, active_only):
        assert active_only is True
        return [
            SimpleNamespace(
                provider="dograh",
                credentials={"api_key": f"mps_sk_team_{organization_id}"},
            )
        ]

    connections.side_effect = scoped_connections
    client = MPSServiceKeyClient()
    assert await client._runtime_service_key(42) == "mps_sk_team_42"
    assert await client._runtime_service_key(43) == "mps_sk_team_43"


@pytest.mark.parametrize("keys", [None, "", "   ", [], [None, ""]])
async def test_utilities_require_an_existing_usable_key(
    custom_models, connections, keys
):
    connections.return_value[-1].credentials = {"api_key": keys}
    with pytest.raises(HTTPException) as error:
        await MPSServiceKeyClient()._runtime_service_key(42)
    assert error.value.status_code == 400
    assert "Add a Dograh provider" in error.value.detail
    assert "default" not in error.value.detail


async def test_archived_connection_cannot_be_used(custom_models, connections):
    # The active-only catalog read excludes the archived Dograh connection.
    connections.return_value = []
    with pytest.raises(HTTPException) as error:
        await MPSServiceKeyClient()._runtime_service_key(42)
    assert error.value.status_code == 400
    connections.assert_awaited_once_with(42, active_only=True)
