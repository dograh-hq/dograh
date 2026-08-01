from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.enums import ToolCategory
from api.routes.tool import router
from api.services.auth.depends import get_user


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user] = lambda: SimpleNamespace(
        id=1,
        provider_id="provider-1",
        selected_organization_id=11,
    )
    return app


@patch("api.routes.tool.create_tool_for_user", new_callable=AsyncMock)
def test_create_tool_with_body_template_persists(mock_create):
    app = _make_test_app()
    client = TestClient(app)

    payload = {
        "name": "Test Tool",
        "description": "Test",
        "category": "http_api",
        "definition": {
            "schema_version": 1,
            "type": "http_api",
            "config": {
                "method": "POST",
                "url": "http://test",
                "body_template": {"key": "{{val}}"},
            },
        },
    }

    mock_create.return_value = {
        "id": "1",
        "tool_uuid": "uuid",
        "icon": "icon",
        "icon_color": "blue",
        "status": "active",
        "created_at": "2023-01-01T00:00:00Z",
        "updated_at": "2023-01-01T00:00:00Z",
        **payload,
    }

    res = client.post("/tools/", json=payload)
    assert res.status_code == 200
    mock_create.assert_called_once()

    # Verify the body_template was passed along to the service layer.
    call_kwargs = mock_create.call_args.kwargs
    passed_tool = call_kwargs.get("request") or mock_create.call_args.args[0]
    assert passed_tool.definition.config.body_template == {"key": "{{val}}"}


@patch("api.routes.tool.db_client.get_tool_by_uuid", new_callable=AsyncMock)
@patch("api.services.workflow.tools.custom_tool.httpx.AsyncClient.request")
def test_test_endpoint_renders_body_template(mock_request, mock_get_tool):
    app = _make_test_app()
    client = TestClient(app)

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"id": 1})
    mock_request.return_value = mock_response

    mock_get_tool.return_value = SimpleNamespace(
        name="Test",
        tool_uuid="uuid",
        category=ToolCategory.HTTP_API.value,
        definition={
            "config": {
                "method": "POST",
                "url": "http://test",
                "body_template": {"nested": "{{val}}"},
                "parameters": [{"name": "val", "type": "number"}],
            }
        },
    )

    res = client.post(
        "/tools/uuid/test", json={"llm_params": {"val": 42}, "preset_params": {}}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["request_body"] == {"nested": 42}


@patch("api.routes.tool.db_client.get_tool_by_uuid", new_callable=AsyncMock)
@patch("api.services.workflow.tools.custom_tool.httpx.AsyncClient.request")
def test_test_endpoint_flat_tool_unchanged(mock_request, mock_get_tool):
    app = _make_test_app()
    client = TestClient(app)

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"id": 1})
    mock_request.return_value = mock_response

    mock_get_tool.return_value = SimpleNamespace(
        name="Test",
        tool_uuid="uuid",
        category=ToolCategory.HTTP_API.value,
        definition={"config": {"method": "POST", "url": "http://test"}},
    )

    res = client.post(
        "/tools/uuid/test", json={"llm_params": {"val": 42}, "preset_params": {}}
    )
    assert res.status_code == 200
    data = res.json()
    assert data["request_body"] == {"val": 42}


@patch("api.routes.tool.db_client.get_tool_by_uuid", new_callable=AsyncMock)
@patch("api.services.workflow.tools.custom_tool.httpx.AsyncClient.request")
def test_test_endpoint_call_context_not_in_test_mode(mock_request, mock_get_tool):
    app = _make_test_app()
    client = TestClient(app)

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"id": 1})
    mock_request.return_value = mock_response

    mock_get_tool.return_value = SimpleNamespace(
        name="Test",
        tool_uuid="uuid",
        category=ToolCategory.HTTP_API.value,
        definition={
            "config": {
                "method": "POST",
                "url": "http://test",
                "body_template": {"phone": "{{initial_context.phone}}"},
            }
        },
    )

    res = client.post("/tools/uuid/test", json={"llm_params": {}, "preset_params": {}})
    assert res.status_code == 200
    data = res.json()
    assert data["request_body"] == {"phone": ""}


@patch("api.routes.tool.db_client.get_tool_by_uuid", new_callable=AsyncMock)
@patch("api.services.workflow.tools.custom_tool.httpx.AsyncClient.request")
def test_llm_arg_wins_over_call_context_key(mock_request, mock_get_tool):
    app = _make_test_app()
    client = TestClient(app)

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"id": 1})
    mock_request.return_value = mock_response

    mock_get_tool.return_value = SimpleNamespace(
        name="Test",
        tool_uuid="uuid",
        category=ToolCategory.HTTP_API.value,
        definition={
            "config": {
                "method": "POST",
                "url": "http://test",
                "body_template": {"val": "{{prop}}"},
                "parameters": [{"name": "prop", "type": "string"}],
            }
        },
    )

    res = client.post(
        "/tools/uuid/test",
        json={"llm_params": {"prop": "LLM_VALUE"}, "preset_params": {}},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["request_body"] == {"val": "LLM_VALUE"}


@patch("api.routes.tool.db_client.get_tool_by_uuid", new_callable=AsyncMock)
@patch("api.services.workflow.tools.custom_tool.httpx.AsyncClient.request")
def test_test_endpoint_coerces_deeply_nested_types(mock_request, mock_get_tool):
    app = _make_test_app()
    client = TestClient(app)

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"id": 1})
    mock_request.return_value = mock_response

    mock_get_tool.return_value = SimpleNamespace(
        name="Test",
        tool_uuid="uuid",
        category=ToolCategory.HTTP_API.value,
        definition={
            "config": {
                "method": "POST",
                "url": "http://test",
                "body_template": {
                    "outer": {
                        "inner_arr": [
                            {"target": "{{val}}"},
                            {"other": "{{other_val}}"},
                        ],
                        "deep": {"deeper": {"val": "{{val}}"}},
                    }
                },
                "parameters": [
                    {"name": "val", "type": "number"},
                    {"name": "other_val", "type": "boolean"},
                ],
            }
        },
    )

    res = client.post(
        "/tools/uuid/test",
        json={"llm_params": {"val": 42, "other_val": False}, "preset_params": {}},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["request_body"] == {
        "outer": {
            "inner_arr": [{"target": 42}, {"other": False}],
            "deep": {"deeper": {"val": 42}},
        }
    }


@patch("api.routes.tool.db_client.get_tool_by_uuid", new_callable=AsyncMock)
@patch("api.services.workflow.tools.custom_tool.httpx.AsyncClient.request")
def test_test_endpoint_reserved_name_collision_is_prevented(
    mock_request, mock_get_tool
):
    app = _make_test_app()
    client = TestClient(app)

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.json = Mock(return_value={"id": 1})
    mock_request.return_value = mock_response

    mock_get_tool.return_value = SimpleNamespace(
        name="Test",
        tool_uuid="uuid",
        category=ToolCategory.HTTP_API.value,
        definition={
            "config": {
                "method": "POST",
                "url": "http://test",
                "body_template": {"data": "{{initial_context.phone}}"},
                "parameters": [{"name": "initial_context", "type": "object"}],
            }
        },
    )

    # The new code raises a ValueError when a parameter literally named
    # 'initial_context' is passed at runtime, since it would corrupt the
    # {{initial_context.*}} namespace. Expect an error response.
    res = client.post(
        "/tools/uuid/test",
        json={
            "llm_params": {"initial_context": {"phone": "hacked"}},
            "preset_params": {},
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["error"] is not None
    assert "reserved" in data["error"].lower() or "namespace" in data["error"].lower()


@patch("api.routes.tool.db_client.get_tool_by_uuid", new_callable=AsyncMock)
@patch("api.services.workflow.tools.custom_tool.httpx.AsyncClient.request")
def test_test_endpoint_rejects_key_collision(
    mock_request, mock_get_tool
):
    app = _make_test_app()
    client = TestClient(app)

    # We mock the tool to have a body template where two keys render to the same string.
    # We use {{key1}} and {{key2}} which will both be "same_key" based on our inputs.
    mock_get_tool.return_value = SimpleNamespace(
        name="Test",
        tool_uuid="uuid",
        category=ToolCategory.HTTP_API.value,
        definition={
            "config": {
                "method": "POST",
                "url": "http://test",
                "body_template": {"{{key1}}": "value1", "{{key2}}": "value2"},
                "parameters": [
                    {"name": "key1", "type": "string"},
                    {"name": "key2", "type": "string"},
                ],
            }
        },
    )

    res = client.post(
        "/tools/uuid/test",
        json={
            "llm_params": {"key1": "same_key", "key2": "same_key"},
            "preset_params": {},
        },
    )
    
    # The endpoint should return a 200, but with the error caught and surfaced in the response.
    assert res.status_code == 200
    data = res.json()
    assert "keys that render to the same value" in data["error"]
