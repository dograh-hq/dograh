import pytest

from api.services.mps_service_key_client import MPSServiceKeyClient


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.request = object()

    def json(self):
        return self._payload


def test_validate_service_key_uses_bearer_self_usage(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def get(self, url, headers):
            calls.append(("GET", url, headers))
            return _Response(200)

    monkeypatch.setattr("api.services.mps_service_key_client.httpx.Client", FakeClient)

    client = MPSServiceKeyClient()

    assert client.validate_service_key("mps_sk_paid") is True
    assert calls == [
        (
            "GET",
            f"{client.base_url}/api/v1/service-keys/usage/self",
            {
                "Authorization": "Bearer mps_sk_paid",
                "Content-Type": "application/json",
            },
        )
    ]


@pytest.mark.asyncio
async def test_check_service_key_usage_uses_bearer_self_usage(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers):
            calls.append(("GET", url, headers))
            return _Response(
                200,
                {"total_credits_used": 12.5, "remaining_credits": 87.5},
            )

    monkeypatch.setattr(
        "api.services.mps_service_key_client.httpx.AsyncClient", FakeAsyncClient
    )

    client = MPSServiceKeyClient()

    assert await client.check_service_key_usage("mps_sk_paid") == {
        "total_credits_used": 12.5,
        "remaining_credits": 87.5,
    }
    assert calls[0] == (
        "GET",
        f"{client.base_url}/api/v1/service-keys/usage/self",
        {
            "Authorization": "Bearer mps_sk_paid",
            "Content-Type": "application/json",
        },
    )


@pytest.mark.asyncio
async def test_create_correlation_id_uses_bearer_auth(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            calls.append(("POST", url, json, headers))
            return _Response(200, {"correlation_id": "mps-corr-123"})

    monkeypatch.setattr(
        "api.services.mps_service_key_client.httpx.AsyncClient", FakeAsyncClient
    )

    client = MPSServiceKeyClient()

    assert await client.create_correlation_id(
        service_key="mps_sk_paid",
        workflow_run_id=42,
    ) == {"correlation_id": "mps-corr-123"}
    assert calls == [
        (
            "POST",
            f"{client.base_url}/api/v1/service-keys/correlation-id/self",
            {"workflow_run_id": 42},
            {
                "Authorization": "Bearer mps_sk_paid",
                "Content-Type": "application/json",
            },
        )
    ]


@pytest.mark.asyncio
async def test_get_billing_account_status_uses_hosted_org_auth(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers):
            calls.append(("GET", url, headers))
            return _Response(200, {"organization_id": 42, "billing_mode": "v2"})

    monkeypatch.setattr(
        "api.services.mps_service_key_client.httpx.AsyncClient", FakeAsyncClient
    )
    monkeypatch.setattr("api.services.mps_service_key_client.DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        "api.services.mps_service_key_client.DOGRAH_MPS_SECRET_KEY", "mps-secret"
    )

    client = MPSServiceKeyClient()

    assert await client.get_billing_account_status(organization_id=42) == {
        "organization_id": 42,
        "billing_mode": "v2",
    }
    assert calls == [
        (
            "GET",
            f"{client.base_url}/api/v1/billing/accounts/42/status",
            {
                "Content-Type": "application/json",
                "X-Secret-Key": "mps-secret",
                "X-Organization-Id": "42",
            },
        )
    ]


@pytest.mark.asyncio
async def test_report_platform_usage_uses_hosted_secret_auth(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            calls.append(("POST", url, json, headers))
            return _Response(200, {"metered": True})

    monkeypatch.setattr(
        "api.services.mps_service_key_client.httpx.AsyncClient", FakeAsyncClient
    )
    monkeypatch.setattr("api.services.mps_service_key_client.DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        "api.services.mps_service_key_client.DOGRAH_MPS_SECRET_KEY", "mps-secret"
    )

    client = MPSServiceKeyClient()

    assert await client.report_platform_usage(
        organization_id=42,
        correlation_id="mps-corr-123",
        workflow_run_id=123,
        metadata={"source": "workflow_run_completion"},
    ) == {"metered": True}
    assert calls == [
        (
            "POST",
            f"{client.base_url}/api/v1/billing/accounts/42/platform-usage",
            {
                "correlation_id": "mps-corr-123",
                "workflow_run_id": 123,
                "metadata": {"source": "workflow_run_completion"},
            },
            {
                "Content-Type": "application/json",
                "X-Secret-Key": "mps-secret",
                "X-Organization-Id": "42",
            },
        )
    ]


@pytest.mark.asyncio
async def test_report_platform_usage_sends_duration_without_correlation(monkeypatch):
    calls = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, json, headers):
            calls.append(("POST", url, json, headers))
            return _Response(200, {"metered": True})

    monkeypatch.setattr(
        "api.services.mps_service_key_client.httpx.AsyncClient", FakeAsyncClient
    )
    monkeypatch.setattr("api.services.mps_service_key_client.DEPLOYMENT_MODE", "saas")
    monkeypatch.setattr(
        "api.services.mps_service_key_client.DOGRAH_MPS_SECRET_KEY", "mps-secret"
    )

    client = MPSServiceKeyClient()

    assert await client.report_platform_usage(
        organization_id=42,
        duration_seconds=87.0,
        workflow_run_id=123,
        metadata={"source": "workflow_run_completion"},
    ) == {"metered": True}
    assert calls == [
        (
            "POST",
            f"{client.base_url}/api/v1/billing/accounts/42/platform-usage",
            {
                "duration_seconds": 87.0,
                "workflow_run_id": 123,
                "metadata": {"source": "workflow_run_completion"},
            },
            {
                "Content-Type": "application/json",
                "X-Secret-Key": "mps-secret",
                "X-Organization-Id": "42",
            },
        )
    ]
