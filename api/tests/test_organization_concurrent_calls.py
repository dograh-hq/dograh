"""Org isolation and failure behavior for the public concurrency snapshot."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from api.routes import organization_usage
from api.services.auth import depends as auth_depends
from api.services.call_concurrency import service as concurrency_service

PATH = "/api/v1/organizations/concurrent-calls"


@pytest.fixture
def endpoint(monkeypatch):
    # Exercise real API-key auth and the service facade. Both keys have the
    # same owner, whose selected org differs from either token's org.
    keys = {
        "org-a-key": SimpleNamespace(
            organization_id=42, created_by=7, key_prefix="org-a"
        ),
        "org-b-key": SimpleNamespace(
            organization_id=43, created_by=7, key_prefix="org-b"
        ),
    }
    validate_key = AsyncMock(side_effect=lambda key: keys.get(key))
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(auth_depends.db_client, "validate_api_key", validate_key)
    monkeypatch.setattr(
        auth_depends.db_client,
        "get_user_by_id",
        AsyncMock(
            side_effect=lambda user_id: SimpleNamespace(
                id=user_id, selected_organization_id=999
            )
        ),
    )
    count = AsyncMock(return_value=0)
    monkeypatch.setattr(concurrency_service.rate_limiter, "get_concurrent_count", count)
    app = FastAPI()
    app.include_router(organization_usage.router, prefix="/api/v1")
    with TestClient(app) as client:
        yield client, count, validate_key


def test_api_key_org_wins_over_owner_selection_and_caller_supplied_org(endpoint):
    client, count, _ = endpoint
    count.side_effect = lambda organization_id, **kwargs: {42: 3, 43: 7}[
        organization_id
    ]

    first = client.get(
        PATH + "?organization_id=43",
        headers={"X-API-Key": "org-a-key", "X-Organization-Id": "43"},
    )
    second = client.get(PATH, headers={"X-API-Key": "org-b-key"})

    assert first.status_code == second.status_code == 200
    assert first.json() == {"organization_id": 42, "active_calls": 3}
    assert second.json() == {"organization_id": 43, "active_calls": 7}
    assert first.headers["Cache-Control"] == "no-store"
    assert count.await_args_list == [
        call(42, raise_on_error=True),
        call(43, raise_on_error=True),
    ]


def test_empty_org_reports_zero(endpoint):
    client, _, _ = endpoint
    response = client.get(PATH, headers={"X-API-Key": "org-a-key"})
    assert response.status_code == 200
    assert response.json() == {"organization_id": 42, "active_calls": 0}


@pytest.mark.parametrize("key", [None, "", "invalid-key", "archived-key"])
def test_missing_or_invalid_key_cannot_read_concurrency(endpoint, key):
    client, count, _ = endpoint
    response = client.get(PATH, headers={"X-API-Key": key} if key is not None else {})
    assert response.status_code == 401
    count.assert_not_awaited()


@pytest.mark.parametrize("error", [RedisConnectionError("redis down"), TimeoutError()])
def test_unavailable_count_returns_503_instead_of_zero(endpoint, error):
    client, count, _ = endpoint
    count.side_effect = error
    response = client.get(PATH, headers={"X-API-Key": "org-a-key"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Concurrent call count unavailable"}
    assert response.headers["Cache-Control"] == "no-store"


def test_token_without_org_cannot_read_concurrency(endpoint):
    client, count, validate_key = endpoint
    validate_key.side_effect = None
    validate_key.return_value = SimpleNamespace(
        organization_id=None, created_by=7, key_prefix="missing-org"
    )
    response = client.get(PATH, headers={"X-API-Key": "org-a-key"})
    assert response.status_code == 400
    count.assert_not_awaited()
