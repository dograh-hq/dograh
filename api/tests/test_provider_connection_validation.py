"""Connection writes reuse credential checks without probing on model edits."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import openai
import pytest
from fastapi import FastAPI

from api.routes import model_connections as routes
from api.services.auth.depends import get_user_with_selected_organization
from api.services.configuration import check_validity


@pytest.fixture
async def api(monkeypatch):
    row = SimpleNamespace(
        uuid=str(uuid4()),
        organization_id=7,
        name="OpenAI",
        provider="openai",
        credentials={"api_key": "valid-saved"},
        connection_settings={},
        revision=3,
        is_active=True,
    )
    create = AsyncMock(
        side_effect=lambda organization_id, **values: SimpleNamespace(
            **values, uuid=str(uuid4()), revision=1, is_active=True
        )
    )
    update = AsyncMock(return_value=row)
    lookup = AsyncMock(return_value=row)
    monkeypatch.setattr(routes.db_client, "create_provider_connection", create)
    monkeypatch.setattr(routes.db_client, "update_provider_connection", update)
    monkeypatch.setattr(routes.db_client, "get_provider_connection", lookup)

    def openai_client(**kwargs):
        def list_models():
            if kwargs["api_key"].startswith("bad-"):
                # Provider response bodies must not leak through our error.
                raise openai.AuthenticationError(
                    f"Rejected {kwargs['api_key']}",
                    response=httpx.Response(
                        401,
                        request=httpx.Request(
                            "GET", "https://api.openai.com/v1/models"
                        ),
                    ),
                    body={"key": kwargs["api_key"]},
                )
            return []

        return SimpleNamespace(models=SimpleNamespace(list=list_models))

    probe = Mock(side_effect=openai_client)
    monkeypatch.setattr(check_validity.openai, "OpenAI", probe)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: (
        SimpleNamespace(selected_organization_id=7, provider_id="user-7")
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(
            client=client,
            row=row,
            create=create,
            update=update,
            lookup=lookup,
            probe=probe,
        )


@pytest.mark.asyncio
async def test_rejected_openai_key_is_not_saved_or_returned(api):
    response = await api.client.post(
        "/model-connections/provider-connections",
        json={
            "name": "OpenAI",
            "provider": "openai",
            "credentials": {"api_key": "bad-secret"},
        },
    )
    assert response.status_code == 422
    assert "Invalid OpenAI API key" in response.json()["detail"]
    assert "bad-secret" not in response.text
    api.probe.assert_called_once_with(
        api_key="bad-secret", base_url="https://api.openai.com/v1"
    )
    api.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_key_in_a_pool_is_validated_before_create(api):
    response = await api.client.post(
        "/model-connections/provider-connections",
        json={
            "name": "OpenAI",
            "provider": "openai",
            "credentials": {"api_key": ["valid-one", "valid-two"]},
        },
    )
    assert response.status_code == 201
    assert [call.kwargs["api_key"] for call in api.probe.call_args_list] == [
        "valid-one",
        "valid-two",
    ]
    assert api.create.call_args.args == (7,)
    assert api.create.call_args.kwargs["credentials"] == {
        "api_key": ["valid-one", "valid-two"]
    }
    assert "valid-one" not in response.text
    assert "valid-two" not in response.text


@pytest.mark.asyncio
async def test_invalid_second_key_rejects_the_entire_pool(api):
    response = await api.client.post(
        "/model-connections/provider-connections",
        json={
            "name": "OpenAI",
            "provider": "openai",
            "credentials": {"api_key": ["valid-one", "bad-second"]},
        },
    )
    assert response.status_code == 422
    assert [call.kwargs["api_key"] for call in api.probe.call_args_list] == [
        "valid-one",
        "bad-second",
    ]
    api.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_replacement_keeps_existing_credentials(api):
    response = await api.client.patch(
        f"/model-connections/provider-connections/{api.row.uuid}",
        json={"revision": 3, "credentials": {"api_key": "bad-replacement"}},
    )
    assert response.status_code == 422
    api.update.assert_not_awaited()
    assert api.row.credentials == {"api_key": "valid-saved"}
    assert "bad-replacement" not in response.text


@pytest.mark.asyncio
async def test_endpoint_changes_validate_with_existing_key_at_new_endpoint(api):
    response = await api.client.patch(
        f"/model-connections/provider-connections/{api.row.uuid}",
        json={
            "revision": 3,
            "connection_settings": {"base_url": "https://models.example.com/v1"},
        },
    )
    assert response.status_code == 200
    api.probe.assert_called_once_with(
        api_key="valid-saved", base_url="https://models.example.com/v1"
    )
    api.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_and_unchanged_settings_do_not_probe_provider(api):
    response = await api.client.patch(
        f"/model-connections/provider-connections/{api.row.uuid}",
        json={
            "revision": 3,
            "name": "Renamed",
            "credentials": {},
            "connection_settings": {},
        },
    )
    assert response.status_code == 200
    api.probe.assert_not_called()
    api.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_or_cross_org_connection_is_rejected_before_probing(api):
    api.lookup.return_value = None
    response = await api.client.patch(
        f"/model-connections/provider-connections/{api.row.uuid}",
        json={"credentials": {"api_key": "valid-replacement"}},
    )
    assert response.status_code == 404
    api.lookup.assert_awaited_once_with(7, api.row.uuid)
    api.probe.assert_not_called()
    api.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_revision_is_rejected_before_probing(api):
    response = await api.client.patch(
        f"/model-connections/provider-connections/{api.row.uuid}",
        json={"revision": 2, "credentials": {"api_key": "valid-replacement"}},
    )
    assert response.status_code == 409
    api.probe.assert_not_called()
    api.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_dograh_checks_keep_organization_context(api, monkeypatch):
    validate = Mock(return_value=True)
    monkeypatch.setattr(
        check_validity.mps_service_key_client, "validate_service_key", validate
    )
    response = await api.client.post(
        "/model-connections/provider-connections",
        json={
            "name": "Dograh",
            "provider": "dograh",
            "credentials": {"api_key": "mps-valid"},
        },
    )
    assert response.status_code == 201
    validate.assert_called_once_with(
        "mps-valid", organization_id=7, created_by="user-7"
    )


@pytest.mark.asyncio
async def test_structural_errors_do_not_probe_the_provider(api):
    response = await api.client.post(
        "/model-connections/provider-connections",
        json={
            "name": "OpenAI",
            "provider": "openai",
            "credentials": {"api_key": ["valid", ""]},
        },
    )
    assert response.status_code == 422
    api.probe.assert_not_called()
    api.create.assert_not_awaited()
