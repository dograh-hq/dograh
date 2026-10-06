"""The SDK client is generated from routes tagged with ``sdk_expose``.

These names are the contract AI agents call. Adding a route to the set is
intentional; dropping one should fail here before the generated clients
drift.
"""

from pathlib import Path

from fastapi.openapi.utils import get_openapi

from api.app import app
from api.routes.campaign import CreateCampaignRequest

# Stable, org-scoped methods an agent needs beyond the original surface
# (create/list/get workflow, tools, credentials, recordings, node types,
# and test_phone_call).
EXPECTED_SDK_METHODS = {
    "create_campaign",
    "create_folder",
    "delete_tool",
    "get_campaign",
    "get_campaign_progress",
    "get_tool",
    "get_workflow_run",
    "list_campaigns",
    "list_folders",
    "list_workflow_runs",
    "pause_campaign",
    "publish_workflow",
    "resume_campaign",
    "start_campaign",
    "update_tool",
    "validate_workflow",
}


def test_sdk_openapi_includes_agent_workflow_methods():
    sdk_routes = [
        r
        for r in app.routes
        if getattr(r, "openapi_extra", None)
        and "x-sdk-method" in (r.openapi_extra or {})
    ]
    spec = get_openapi(title=app.title, version=app.version, routes=sdk_routes)
    methods = {
        op.get("x-sdk-method")
        for path_item in spec["paths"].values()
        for op in path_item.values()
        if isinstance(op, dict) and op.get("x-sdk-method")
    }
    missing = EXPECTED_SDK_METHODS - methods
    assert not missing, f"sdk_expose methods missing from OpenAPI: {sorted(missing)}"


def test_create_campaign_rate_limit_is_optional_in_sdk_schema():
    """Callers may omit the rate. The schema default is 1 and the field is not required.

    ``scripts/generate_sdk.sh`` strips that default before openapi-typescript
    runs, because a component ``default`` would mark the TypeScript property
    required. The Python model keeps the default so a plain dump sends 1.
    """
    sdk_routes = [
        r
        for r in app.routes
        if getattr(r, "openapi_extra", None)
        and "x-sdk-method" in (r.openapi_extra or {})
    ]
    spec = get_openapi(title=app.title, version=app.version, routes=sdk_routes)
    schema = spec["components"]["schemas"]["CreateCampaignRequest"]
    assert "rate_limit_per_second" not in schema.get("required", [])
    prop = schema["properties"]["rate_limit_per_second"]
    assert prop["type"] == "integer"
    assert prop["default"] == 1
    assert "1" in prop["description"]
    omitted = CreateCampaignRequest(
        name="SDK campaign",
        workflow_id=1,
        source_type="csv",
        source_id="contacts.csv",
    )
    assert omitted.rate_limit_per_second == 1


def _sdk_spec():
    sdk_routes = [
        r
        for r in app.routes
        if getattr(r, "openapi_extra", None)
        and "x-sdk-method" in (r.openapi_extra or {})
    ]
    return get_openapi(title=app.title, version=app.version, routes=sdk_routes)


def test_publish_workflow_keeps_release_notes_on_the_response():
    """The typed publish response must include the fields the route returns.

    A narrower response model strips them, and clients reading version_name
    then raise KeyError.
    """
    spec = _sdk_spec()
    op = spec["paths"]["/api/v1/workflow/{workflow_id}/publish"]["post"]
    response_ref = op["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    schema = spec["components"]["schemas"][response_ref.rsplit("/", 1)[-1]]
    assert set(schema["properties"]) >= {
        "id",
        "version_number",
        "status",
        "published_at",
        "version_name",
        "change_description",
    }
    body_schema = op["requestBody"]["content"]["application/json"]["schema"]
    body_refs = [
        branch["$ref"] for branch in body_schema.get("anyOf", []) if "$ref" in branch
    ]
    assert body_refs == ["#/components/schemas/PublishWorkflowRequest"]


def test_generated_publish_methods_send_optional_release_notes():
    repo_root = Path(__file__).resolve().parents[2]
    python_client = (
        repo_root / "sdk" / "python" / "src" / "dograh_sdk" / "_generated_client.py"
    ).read_text()
    typescript_client = (
        repo_root / "sdk" / "typescript" / "src" / "_generated_client.ts"
    ).read_text()
    assert (
        "def publish_workflow(self, workflow_id: int, *, "
        "body: PublishWorkflowRequest | None = None) -> PublishWorkflowResponse:"
        in python_client
    )
    assert (
        'kwargs["json"] = body.model_dump(mode="json", exclude_none=True)'
        in python_client
    )
    assert (
        "async publishWorkflow(workflowId: number, "
        "opts: { body?: PublishWorkflowRequest } = {}): Promise<PublishWorkflowResponse>"
        in typescript_client
    )
    assert "json: opts.body" in typescript_client
