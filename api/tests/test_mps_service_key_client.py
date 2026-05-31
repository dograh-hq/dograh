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
