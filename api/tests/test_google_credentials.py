"""Authentication construction, reuse, and async preparation contracts."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
from pipecat.services.google.llm import GoogleLLMService

from api.services.pipecat import google_credentials as auth
from api.services.pipecat.realtime.gemini_live_vertex import (
    DograhGeminiLiveVertexLLMService,
)
from api.services.pipecat.service_factory import DograhGoogleVertexLLMService


@pytest.fixture(autouse=True)
def clear_credentials():
    auth._json_credentials.cache_clear()
    yield
    auth._json_credentials.cache_clear()


@pytest.fixture
def delegate(monkeypatch):
    value = SimpleNamespace(
        token=None,
        expiry=None,
        quota_project_id="billing",
        universe_domain="googleapis.com",
    )

    def refresh(_request):
        value.token = "test-token"
        value.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)

    value.refresh = Mock(side_effect=refresh)
    monkeypatch.setattr(
        auth.service_account.Credentials,
        "from_service_account_info",
        Mock(return_value=value),
    )
    monkeypatch.setattr(
        auth.service_account.Credentials,
        "from_service_account_file",
        Mock(return_value=value),
    )
    monkeypatch.setattr(
        auth.google.auth, "default", Mock(return_value=(value, "project"))
    )
    return value


@pytest.mark.parametrize(
    "service_class", [DograhGoogleVertexLLMService, DograhGeminiLiveVertexLLMService]
)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"credentials": '{"key": "a"}'},
        {"credentials_path": "/not/read/during/construction"},
        {},
    ],
)
async def test_construction_never_loads_or_refreshes_credentials(
    service_class, kwargs, delegate
):
    service = service_class(project_id="test", location="global", **kwargs)
    try:
        delegate.refresh.assert_not_called()
        auth.service_account.Credentials.from_service_account_info.assert_not_called()
        auth.service_account.Credentials.from_service_account_file.assert_not_called()
        auth.google.auth.default.assert_not_called()
    finally:
        service._client.close()
        await service._client.aio.aclose()


@pytest.mark.asyncio
async def test_text_and_live_share_auth_but_keep_separate_clients(delegate):
    services = [
        cls(project_id="test", location="global", credentials='{"key": "shared"}')
        for cls in (DograhGoogleVertexLLMService, DograhGeminiLiveVertexLLMService)
    ]
    try:
        text, live = services
        assert text._credentials is live._credentials
        assert text._client is not live._client
        await asyncio.gather(
            *(auth.prepare_google_credentials(s._credentials) for s in services)
        )
        delegate.refresh.assert_called_once()
    finally:
        for service in services:
            service._client.close()
            await service._client.aio.aclose()


@pytest.mark.asyncio
async def test_shared_preparation_keeps_loop_live_and_refreshes_once(delegate):
    entered, release = threading.Event(), threading.Event()
    refresh = delegate.refresh.side_effect

    def blocked(request):
        entered.set()
        assert release.wait(3)
        refresh(request)

    delegate.refresh.side_effect = blocked
    credentials = [auth.get_google_credentials('{"key": "a"}', None) for _ in range(4)]
    assert all(c is credentials[0] for c in credentials)
    pending = [
        asyncio.create_task(auth.prepare_google_credentials(c)) for c in credentials
    ]
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.001)
        # Reaching here while refresh is blocked proves the loop remains live.
        assert not any(t.done() for t in pending)
        release.set()
        await asyncio.gather(*pending)
        delegate.refresh.assert_called_once()
        assert credentials[0].quota_project_id == "billing"
        await auth.prepare_google_credentials(credentials[0])
        delegate.refresh.assert_called_once()
        credentials[0].expiry = datetime(2000, 1, 1)
        await asyncio.gather(*(auth.prepare_google_credentials(c) for c in credentials))
        assert delegate.refresh.call_count == 2
    finally:
        release.set()
        await asyncio.gather(*pending, return_exceptions=True)


def test_distinct_credentials_and_external_identity_are_not_reused():
    a = auth.get_google_credentials('{"key": "a"}', None)
    b = auth.get_google_credentials('{"key": "b"}', None)
    assert a is not b
    assert auth.get_google_credentials(None, "file") is not auth.get_google_credentials(
        None, "file"
    )
    assert auth.get_google_credentials(None, None) is not auth.get_google_credentials(
        None, None
    )


@pytest.mark.asyncio
async def test_failed_preparation_can_retry(delegate):
    refresh = delegate.refresh.side_effect
    delegate.refresh.side_effect = RuntimeError("auth failed")
    credentials = auth.get_google_credentials("{}", None)
    with pytest.raises(RuntimeError, match="auth failed"):
        await auth.prepare_google_credentials(credentials)
    assert not credentials.valid
    delegate.refresh.side_effect = refresh
    await auth.prepare_google_credentials(credentials)
    assert credentials.valid


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["run_inference", "_stream_content"])
async def test_text_requests_prepare_without_pipeline_setup(
    delegate, monkeypatch, method
):
    monkeypatch.setattr(DograhGoogleVertexLLMService, "create_client", Mock())
    service = DograhGoogleVertexLLMService(project_id="test", credentials="{}")

    async def request(*args, **kwargs):
        assert service._credentials.valid
        return "response"

    monkeypatch.setattr(GoogleLLMService, method, request)
    assert await getattr(service, method)(object()) == "response"
    delegate.refresh.assert_called_once()


@pytest.mark.asyncio
async def test_live_prepares_each_connection_before_entering_sdk(delegate, monkeypatch):
    monkeypatch.setattr(DograhGeminiLiveVertexLLMService, "create_client", Mock())
    service = DograhGeminiLiveVertexLLMService(
        project_id="test", location="global", credentials="{}"
    )

    async def connect(*args, **kwargs):
        assert service._credentials.valid

    monkeypatch.setattr(GeminiLiveLLMService, "_connection_task_handler", connect)
    await service._connection_task_handler(object())
    service._credentials.expiry = datetime(2000, 1, 1)
    await service._connection_task_handler(object())
    assert delegate.refresh.call_count == 2


@pytest.mark.asyncio
async def test_live_auth_failure_uses_connection_error_policy(delegate, monkeypatch):
    monkeypatch.setattr(DograhGeminiLiveVertexLLMService, "create_client", Mock())
    service = DograhGeminiLiveVertexLLMService(
        project_id="test", location="global", credentials="{}"
    )
    delegate.refresh.side_effect = RuntimeError("auth failed")
    service._handle_connection_error = AsyncMock(return_value=False)
    connect = AsyncMock()
    monkeypatch.setattr(GeminiLiveLLMService, "_connection_task_handler", connect)
    await service._connection_task_handler(object())
    service._handle_connection_error.assert_awaited_once()
    connect.assert_not_awaited()
