"""The SDK client is generated from routes tagged with ``sdk_expose``.

These names are the contract AI agents call. Adding a route to the set is
intentional; dropping one should fail here before the generated clients
drift.
"""

from fastapi.openapi.utils import get_openapi

from api.app import app

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
