from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
