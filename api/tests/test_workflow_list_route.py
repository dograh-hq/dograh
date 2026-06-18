from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.workflow import router
from api.services.auth.depends import get_user


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1,
        selected_organization_id=11,
    )
    return app


def test_workflow_fetch_list_includes_workflow_uuid():
    app = _make_test_app()
    client = TestClient(app)

    workflow = SimpleNamespace(
        id=5,
        name="Sales Agent",
        status="active",
        created_at=datetime(2026, 5, 22, 10, 30, tzinfo=timezone.utc),
        folder_id=3,
        workflow_uuid="workflow-uuid-123",
    )

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_all_workflows_for_listing = AsyncMock(return_value=[workflow])
        mock_db.get_workflow_run_counts = AsyncMock(return_value={workflow.id: 9})

        response = client.get("/workflow/fetch")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": workflow.id,
            "name": workflow.name,
            "status": workflow.status,
            "created_at": "2026-05-22T10:30:00Z",
            "total_runs": 9,
            "folder_id": workflow.folder_id,
            "workflow_uuid": workflow.workflow_uuid,
        }
    ]


def test_workflow_fetch_invalid_status_returns_422_without_db_query():
    """A status outside the workflow_status enum (e.g. 'published') must fail
    as a clean 422 instead of a 500 from the Postgres enum cast."""
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_all_workflows_for_listing = AsyncMock()
        mock_db.get_workflow_run_counts = AsyncMock()

        response = client.get("/workflow/fetch?status=published")

    assert response.status_code == 422
    assert "published" in response.json()["detail"]
    # The invalid value must never reach the database layer.
    mock_db.get_all_workflows_for_listing.assert_not_called()


def test_workflow_fetch_valid_single_status_passes_through():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_all_workflows_for_listing = AsyncMock(return_value=[])
        mock_db.get_workflow_run_counts = AsyncMock(return_value={})

        response = client.get("/workflow/fetch?status=active")

    assert response.status_code == 200
    mock_db.get_all_workflows_for_listing.assert_awaited_once_with(
        organization_id=11, status="active"
    )


def test_workflow_fetch_comma_separated_status_queries_each_value():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_all_workflows_for_listing = AsyncMock(return_value=[])
        mock_db.get_workflow_run_counts = AsyncMock(return_value={})

        response = client.get("/workflow/fetch?status=active,archived")

    assert response.status_code == 200
    assert mock_db.get_all_workflows_for_listing.await_count == 2
    statuses = {
        call.kwargs["status"]
        for call in mock_db.get_all_workflows_for_listing.await_args_list
    }
    assert statuses == {"active", "archived"}


def test_workflow_fetch_mixed_valid_and_invalid_status_returns_422():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_all_workflows_for_listing = AsyncMock()
        mock_db.get_workflow_run_counts = AsyncMock()

        response = client.get("/workflow/fetch?status=active,published")

    assert response.status_code == 422
    mock_db.get_all_workflows_for_listing.assert_not_called()


@pytest.mark.parametrize("status", [" ", ",", "active,,archived"])
def test_workflow_fetch_blank_status_token_returns_422_without_db_query(status: str):
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_all_workflows_for_listing = AsyncMock()
        mock_db.get_workflow_run_counts = AsyncMock()

        response = client.get("/workflow/fetch", params={"status": status})

    assert response.status_code == 422
    assert "<empty>" in response.json()["detail"]
    mock_db.get_all_workflows_for_listing.assert_not_called()


def test_workflow_summary_blank_status_token_returns_422_without_db_query():
    app = _make_test_app()
    client = TestClient(app)

    with patch("api.routes.workflow.db_client") as mock_db:
        mock_db.get_all_workflows = AsyncMock()

        response = client.get("/workflow/summary", params={"status": ","})

    assert response.status_code == 422
    mock_db.get_all_workflows.assert_not_called()
