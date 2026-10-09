"""SDK client codegen tracks body omission separately from nullability.

FastAPI types ``Model | None`` as an ``anyOf`` of the schema ref and null.
Omitting that body is not the same as sending JSON ``null``:

- optional + nullable: ``body: X | None = None`` / ``body?: X | null``
- required + nullable: ``body: X | None`` / ``body: X | null``, and ``None``
  is sent as the JSON literal ``null``
- required + non-nullable, and optional + non-nullable: the parameter stays
  a required model, matching the existing client
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dograh_sdk._generated_models import CreateCampaignRequest  # noqa: E402
from dograh_sdk.client import DograhClient  # noqa: E402

from sdk.codegen.client_codegen import (  # noqa: E402
    _collect,
    emit_python,
    emit_typescript,
)

_PUBLISH = {
    "anyOf": [
        {"$ref": "#/components/schemas/PublishWorkflowRequest"},
        {"type": "null"},
    ]
}
_MODEL = {"$ref": "#/components/schemas/PublishWorkflowRequest"}


def _json_op(
    method: str,
    description: str,
    schema: dict,
    *,
    required: bool | None = None,
    response: str = "PublishWorkflowResponse",
) -> dict:
    request_body: dict = {
        "content": {"application/json": {"schema": schema}},
    }
    if required is not None:
        request_body["required"] = required
    return {
        "post": {
            "x-sdk-method": method,
            "x-sdk-description": description,
            "requestBody": request_body,
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": f"#/components/schemas/{response}"}
                        }
                    }
                }
            },
        }
    }


def _spec() -> dict:
    publish = _json_op(
        "publish_workflow",
        "Publish the draft.",
        _PUBLISH,
    )
    publish["post"]["parameters"] = [
        {
            "name": "workflow_id",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
        }
    ]
    return {
        "paths": {
            "/api/v1/workflow/{workflow_id}/publish": publish,
            "/api/v1/example/required-nullable": _json_op(
                "required_nullable",
                "Nullable schema, but the body is required.",
                _PUBLISH,
                required=True,
            ),
            "/api/v1/example/nullable-flag": _json_op(
                "nullable_flag",
                "OpenAPI 3.0 nullable: true on a required body.",
                {**_MODEL, "nullable": True},
                required=True,
            ),
            "/api/v1/example/nullable-type-list": _json_op(
                "nullable_type_list",
                "OpenAPI 3.1 type list including null on a required body.",
                {
                    "allOf": [_MODEL],
                    "type": ["object", "null"],
                },
                required=True,
            ),
            "/api/v1/campaign/create": _json_op(
                "create_campaign",
                "Create a campaign.",
                {"$ref": "#/components/schemas/CreateCampaignRequest"},
                required=True,
                response="CampaignResponse",
            ),
            "/api/v1/example/optional-model": _json_op(
                "optional_model",
                "Body may be omitted, but the schema is not nullable.",
                {"$ref": "#/components/schemas/CreateFolderRequest"},
                required=False,
                response="FolderResponse",
            ),
        }
    }


def _method_source(python: str, name: str) -> str:
    marker = f"def {name}("
    rest = python.split(marker, 1)[1]
    return marker + rest.split("\n    def ", 1)[0]


def test_body_omission_and_nullability_are_tracked_separately():
    ops, _models = _collect(_spec())
    by_name = {op.method: op for op in ops}

    publish = by_name["publish_workflow"]
    assert publish.request_class == "PublishWorkflowRequest"
    assert publish.body_optional is True
    assert publish.body_nullable is True
    assert publish.response.class_name == "PublishWorkflowResponse"

    required_nullable = by_name["required_nullable"]
    assert required_nullable.request_class == "PublishWorkflowRequest"
    assert required_nullable.body_optional is False
    assert required_nullable.body_nullable is True
    assert by_name["nullable_flag"].body_nullable is True
    assert by_name["nullable_flag"].body_optional is False
    assert by_name["nullable_type_list"].body_nullable is True
    assert by_name["nullable_type_list"].body_optional is False

    create = by_name["create_campaign"]
    assert create.request_class == "CreateCampaignRequest"
    assert create.body_optional is False
    assert create.body_nullable is False

    optional_model = by_name["optional_model"]
    assert optional_model.request_class == "CreateFolderRequest"
    assert optional_model.body_optional is True
    assert optional_model.body_nullable is False

    python = emit_python(ops, [])
    typescript = emit_typescript(ops, [])

    publish_method = _method_source(python, "publish_workflow")
    assert (
        "def publish_workflow(self, workflow_id: int, *, "
        "body: PublishWorkflowRequest | None = None) -> PublishWorkflowResponse:"
        in publish_method
    )
    assert "if body is not None:" in publish_method
    assert 'kwargs["json"] = None' not in publish_method
    assert (
        'kwargs["json"] = body.model_dump(mode="json", exclude_none=True)'
        in publish_method
    )

    required_method = _method_source(python, "required_nullable")
    assert (
        "def required_nullable(self, *, body: PublishWorkflowRequest | None) "
        "-> PublishWorkflowResponse:" in required_method
    )
    assert "body: PublishWorkflowRequest | None =" not in required_method
    assert "if body is None:" in required_method
    assert 'kwargs["json"] = None' in required_method
    assert (
        'kwargs["json"] = body.model_dump(mode="json", exclude_none=True)'
        in required_method
    )
    for name in ("nullable_flag", "nullable_type_list"):
        method = _method_source(python, name)
        assert f"def {name}(self, *, body: PublishWorkflowRequest | None) " in method
        assert " | None =" not in method
        assert 'kwargs["json"] = None' in method

    assert (
        "def create_campaign(self, *, body: CreateCampaignRequest) -> CampaignResponse:"
        in python
    )
    optional_method = _method_source(python, "optional_model")
    assert (
        "def optional_model(self, *, body: CreateFolderRequest) -> FolderResponse:"
        in optional_method
    )
    assert " | None" not in optional_method
    assert 'json=body.model_dump(mode="json", exclude_none=True)' in optional_method

    assert (
        "async publishWorkflow(workflowId: number, "
        "opts: { body?: PublishWorkflowRequest | null } = {}): "
        "Promise<PublishWorkflowResponse>" in typescript
    )
    assert (
        "async requiredNullable(opts: { body: PublishWorkflowRequest | null }): "
        "Promise<PublishWorkflowResponse>" in typescript
    )
    assert "json: opts.body" in _ts_method(typescript, "requiredNullable")
    assert (
        "async createCampaign(opts: { body: CreateCampaignRequest }): "
        "Promise<CampaignResponse>" in typescript
    )
    assert (
        "async optionalModel(opts: { body: CreateFolderRequest }): "
        "Promise<FolderResponse>" in typescript
    )
    assert "body?: CreateFolderRequest" not in typescript
    assert "CreateFolderRequest | null" not in typescript


def _ts_method(source: str, name: str) -> str:
    marker = f"async {name}("
    rest = source.split(marker, 1)[1]
    return marker + rest.split("\n    async ", 1)[0]


def test_required_nullable_sends_json_null_and_omitted_body_does_not():
    ops, models = _collect(_spec())
    namespace: dict = {"__name__": "generated_client_under_test"}
    exec(emit_python(ops, models), namespace)  # noqa: S102
    generated = namespace["_GeneratedClient"]()
    captured: dict = {}

    def _request(_method, _path, **kwargs):
        captured["kwargs"] = kwargs
        return {
            "id": 1,
            "version_number": 1,
            "status": "published",
            "published_at": "2026-01-01T00:00:00+00:00",
        }

    generated._request = _request
    generated.required_nullable(body=None)
    assert captured["kwargs"] == {"json": None}

    generated.publish_workflow(7)
    assert "json" not in captured["kwargs"]

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    client = DograhClient(base_url="http://example.test", api_key="test")
    client._http.close()
    client._http = httpx.Client(
        base_url="http://example.test/api/v1",
        transport=httpx.MockTransport(handler),
    )
    try:
        client._request("POST", "/example/required-nullable", json=None)
        client._request("POST", "/workflow/1/publish")
        campaign = CreateCampaignRequest(
            name="SDK campaign",
            workflow_id=1,
            source_type="csv",
            source_id="contacts.csv",
        )
        dumped = campaign.model_dump(mode="json", exclude_none=True)
        assert dumped["rate_limit_per_second"] == 1
        client._request("POST", "/campaign/create", json=dumped)
    finally:
        client.close()

    null_body, omitted, campaign_body = seen
    assert null_body.content == b"null"
    assert null_body.headers["content-type"] == "application/json"
    assert omitted.content == b""
    assert omitted.headers.get("content-type") is None
    assert b'"rate_limit_per_second":null' not in campaign_body.content
    assert b'"rate_limit_per_second":1' in campaign_body.content
