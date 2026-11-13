import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException


@pytest.fixture
def endpoint(monkeypatch):
    """Load the real router with offline identity, DB and storage boundaries."""
    user = SimpleNamespace(id=1, selected_organization_id=42, is_superuser=False)

    async def get_user():
        return user

    storage = SimpleNamespace(
        aget_signed_url=AsyncMock(return_value="https://storage.example/preview")
    )
    db_client = SimpleNamespace(
        get_workflow_run=AsyncMock(return_value=None),
        get_workflow_run_by_id=AsyncMock(return_value=None),
    )
    backend = Mock(return_value=storage)
    boundaries = {
        "api.db": {"db_client": db_client},
        "api.services.auth.depends": {"get_user": get_user},
        "api.services.storage": {
            "get_storage_for_backend": backend,
            "storage_fs": storage,
        },
    }
    for name, attributes in boundaries.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    filename = Path(__file__).resolve().parents[1] / "routes" / "s3_signed_url.py"
    spec = importlib.util.spec_from_file_location("_ambient_signed_url_test", filename)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    return SimpleNamespace(
        app=app,
        user=user,
        storage=storage,
        backend=backend,
        db=db_client,
        get_user=get_user,
    )


@pytest.fixture
async def client(endpoint):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=endpoint.app), base_url="http://synthetic"
    ) as client:
        yield client


KEY = "ambient-noise/42/17/7fcaa14b-a610-44a9-b414-00c039e808cd_noise.mp3"


@pytest.mark.parametrize("backend", ["s3", "minio", None])
async def test_same_org_ambient_preview(client, endpoint, backend):
    params = {"key": KEY, "expires_in": 120, "inline": "true"}
    if backend is not None:
        params["storage_backend"] = backend
    response = await client.get("/api/v1/s3/signed-url", params=params)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "url": "https://storage.example/preview",
        "expires_in": 120,
    }
    endpoint.storage.aget_signed_url.assert_awaited_once_with(
        KEY, expiration=120, force_inline=True
    )
    if backend is None:
        endpoint.backend.assert_not_called()
    else:
        endpoint.backend.assert_called_once_with(backend)
    endpoint.db.get_workflow_run.assert_not_awaited()


async def test_cross_org_ambient_preview_is_denied(client, endpoint):
    response = await client.get(
        "/api/v1/s3/signed-url", params={"key": KEY.replace("/42/", "/99/")}
    )
    assert response.status_code == 403
    endpoint.storage.aget_signed_url.assert_not_awaited()
    endpoint.backend.assert_not_called()


async def test_superuser_keeps_cross_org_access(client, endpoint):
    endpoint.user.is_superuser = True
    response = await client.get(
        "/api/v1/s3/signed-url", params={"key": KEY.replace("/42/", "/99/")}
    )
    assert response.status_code == 200


async def test_default_preview_query_preserves_signing_options(client, endpoint):
    response = await client.get(
        "/api/v1/s3/signed-url", params={"key": KEY, "storage_backend": "s3"}
    )
    assert response.status_code == 200
    endpoint.storage.aget_signed_url.assert_awaited_once_with(
        KEY, expiration=3600, force_inline=False
    )


async def test_oversized_organization_id_is_rejected_without_signing(client, endpoint):
    key = f"ambient-noise/{'9' * 5000}/17/file.mp3"
    response = await client.get("/api/v1/s3/signed-url", params={"key": key})
    assert response.status_code in (400, 403)
    endpoint.storage.aget_signed_url.assert_not_awaited()


@pytest.mark.parametrize(
    "key",
    [
        "ambient-noise/42/file.mp3",
        "ambient-noise/42/17/",
        "ambient-noise/0/17/file.mp3",
        "ambient-noise/-42/17/file.mp3",
        "ambient-noise/42/0/file.mp3",
        "ambient-noise/42/not-a-workflow/file.mp3",
        "ambient-noise/42/17/.",
        "ambient-noise/42/17/..",
        "ambient-noise/42/17/../secret.mp3",
        "ambient-noise/42/17/subdir/file.mp3",
        "ambient-noise/42/17/..\\secret.mp3",
        "ambient-noise/42/17/%2e%2e%2fsecret.mp3",
        "ambient-noise//42/17/file.mp3",
        "/ambient-noise/42/17/file.mp3",
        "ambient-noise/42/17/file\x00.mp3",
        "unknown/42/17/file.mp3",
    ],
)
async def test_malformed_ambient_paths_never_reach_storage(client, endpoint, key):
    response = await client.get("/api/v1/s3/signed-url", params={"key": key})
    assert response.status_code == 400
    endpoint.storage.aget_signed_url.assert_not_awaited()
    endpoint.backend.assert_not_called()
    endpoint.db.get_workflow_run.assert_not_awaited()


async def test_authentication_dependency_runs_before_signing(client, endpoint):
    async def deny():
        raise HTTPException(status_code=401, detail="Synthetic unauthorized user")

    endpoint.app.dependency_overrides[endpoint.get_user] = deny
    response = await client.get("/api/v1/s3/signed-url", params={"key": KEY})
    assert response.status_code == 401
    endpoint.storage.aget_signed_url.assert_not_awaited()


@pytest.mark.parametrize(
    "key",
    [
        "campaigns/42/source.csv",
        "knowledge_base/42/doc/file.pdf",
        "recordings/42/greeting-123/greeting.wav",
    ],
)
async def test_existing_org_namespaces_still_work(client, key):
    response = await client.get("/api/v1/s3/signed-url", params={"key": key})
    assert response.status_code == 200


async def test_legacy_recording_authorization_stays_workflow_scoped(client, endpoint):
    endpoint.db.get_workflow_run.return_value = SimpleNamespace(storage_backend="minio")
    response = await client.get(
        "/api/v1/s3/signed-url", params={"key": "recordings/1855/user.wav"}
    )
    assert response.status_code == 200
    endpoint.db.get_workflow_run.assert_awaited_once_with(1855, organization_id=42)
    endpoint.backend.assert_called_once_with("minio")


async def test_legacy_recording_still_rejects_unowned_workflow_runs(client, endpoint):
    response = await client.get(
        "/api/v1/s3/signed-url", params={"key": "recordings/1855/user.wav"}
    )
    assert response.status_code == 403
    endpoint.db.get_workflow_run.assert_awaited_once_with(1855, organization_id=42)
    endpoint.storage.aget_signed_url.assert_not_awaited()
