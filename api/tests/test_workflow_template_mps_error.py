"""Tests for workflow template creation MPS error handling."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.workflow import router
from api.services.auth.depends import get_user


def _make_test_app() -> FastAPI:
    """Create a test app with the workflow router and mocked auth."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1,
        provider_id="provider-1",
        selected_organization_id=11,
    )
    return app


def test_create_workflow_from_template_mps_unreachable_returns_503():
    """Test that connection errors to MPS return 503 with a clear message."""
    app = _make_test_app()
    client = TestClient(app)

    # Mock call_workflow_api to raise RequestError (connection failure)
    with patch(
        "api.routes.workflow.mps_service_key_client.call_workflow_api",
        AsyncMock(
            side_effect=httpx.ConnectError(
                "All connection attempts failed",
            )
        ),
    ):
        response = client.post(
            "/workflow/create/template",
            json={
                "call_type": "inbound",
                "use_case": "customer support",
                "activity_description": "Answer customer questions about billing",
            },
        )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "Dograh cloud service" in detail
    assert "could not be reached" in detail
    assert "MPS_API_URL" in detail
    assert "manually or with the Dograh SDK" in detail


def test_create_workflow_from_template_mps_http_error_preserves_status():
    """Test that HTTP errors from MPS preserve the original status code."""
    app = _make_test_app()
    client = TestClient(app)

    # Mock call_workflow_api to raise HTTPStatusError with 429 (rate limit)
    mock_response = httpx.Response(429, json={"detail": "Rate limit exceeded"})
    with patch(
        "api.routes.workflow.mps_service_key_client.call_workflow_api",
        AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Rate limit exceeded",
                request=httpx.Request("POST", "http://test"),
                response=mock_response,
            )
        ),
    ):
        response = client.post(
            "/workflow/create/template",
            json={
                "call_type": "inbound",
                "use_case": "customer support",
                "activity_description": "Answer customer questions",
            },
        )

    assert response.status_code == 429


def test_create_workflow_from_template_other_errors_return_500():
    """Test that unexpected errors return 500."""
    app = _make_test_app()
    client = TestClient(app)

    with patch(
        "api.routes.workflow.mps_service_key_client.call_workflow_api",
        AsyncMock(side_effect=ValueError("Unexpected error")),
    ):
        response = client.post(
            "/workflow/create/template",
            json={
                "call_type": "inbound",
                "use_case": "customer support",
                "activity_description": "Answer customer questions",
            },
        )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "unexpected error occurred" in detail.lower()
