"""HTTP client for the Dograh REST API.

Wraps `/api/v1/node-types`, the reference-catalog endpoints, and the
workflow CRUD endpoints. Specs are fetched once per client and cached in
memory — spec changes on the server require a fresh client instance.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from .errors import ApiError, SpecMismatchError
from .workflow import Workflow


class DograhClient:
    """Sync HTTP client. Suitable for scripts, pytest, and the LLM SDK
    exec sandbox. An async variant can be added in a later pass.

    Auth precedence:
        1. `api_key` kwarg
        2. `DOGRAH_API_KEY` env var
        3. unauthenticated (most endpoints will 401)
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ):
        resolved_url = base_url or os.environ.get(
            "DOGRAH_API_URL", "http://localhost:8000"
        )
        self.base_url = resolved_url.rstrip("/")
        self.api_key = api_key or os.environ.get("DOGRAH_API_KEY")

        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers=headers,
            timeout=timeout,
        )

        # Spec + catalog caches. `_spec_version` is pinned on first fetch;
        # the SDK warns if the server later reports a different version.
        self._spec_cache: dict[str, dict] = {}
        self._spec_version: str | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DograhClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── spec discovery ─────────────────────────────────────────────

    def list_node_types(self) -> dict[str, Any]:
        """Return `{spec_version, node_types}` summary. Populates the
        per-spec cache so subsequent `get_node_type` calls are local."""
        body = self._request("GET", "/node-types")
        self._spec_version = body.get("spec_version")
        for spec in body.get("node_types") or []:
            self._spec_cache[spec["name"]] = spec
        return body

    def get_node_type(self, name: str) -> dict[str, Any]:
        """Return the full NodeSpec for `name`. Cached per client."""
        if name in self._spec_cache:
            return self._spec_cache[name]
        try:
            spec = self._request("GET", f"/node-types/{name}")
        except ApiError as e:
            if e.status_code == 404:
                raise SpecMismatchError(f"Unknown node type: {name!r}") from e
            raise
        self._spec_cache[name] = spec
        return spec

    @property
    def spec_version(self) -> str | None:
        """Spec contract version reported by the server, or None until
        first discovery call."""
        return self._spec_version

    # ── reference catalogs ─────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        return self._request("GET", "/tools/")

    def list_documents(self) -> list[dict[str, Any]]:
        body = self._request("GET", "/knowledge-base/documents")
        # Response may be wrapped in { documents, total, limit, offset } —
        # return the flat list for convenience.
        if isinstance(body, dict) and "documents" in body:
            return body["documents"]
        return body if isinstance(body, list) else []

    def list_credentials(self) -> list[dict[str, Any]]:
        return self._request("GET", "/credentials/")

    def list_recordings(self) -> list[dict[str, Any]]:
        body = self._request("GET", "/workflow-recordings/")
        if isinstance(body, dict) and "recordings" in body:
            return body["recordings"]
        return body if isinstance(body, list) else []

    # ── workflow CRUD ──────────────────────────────────────────────

    def list_workflows(self) -> list[dict[str, Any]]:
        body = self._request("GET", "/workflow/")
        if isinstance(body, dict) and "workflows" in body:
            return body["workflows"]
        return body if isinstance(body, list) else []

    def get_workflow(self, workflow_id: int) -> dict[str, Any]:
        return self._request("GET", f"/workflow/{workflow_id}")

    def load_workflow(self, workflow_id: int) -> Workflow:
        """Fetch a workflow and return it as an editable `Workflow` object.

        Wraps the REST `get_workflow` + `Workflow.from_json`. Raises if
        the workflow has no current definition.
        """
        raw = self.get_workflow(workflow_id)
        definition = raw.get("current_definition") or raw.get("definition") or {}
        workflow_json = definition.get("workflow_json") or raw.get("workflow_json")
        if not workflow_json:
            raise ApiError(
                200,
                f"Workflow {workflow_id} has no current definition to load",
                body=raw,
            )
        return Workflow.from_json(
            workflow_json, client=self, name=raw.get("name", "")
        )

    def save_workflow(self, workflow_id: int, workflow: Workflow) -> dict[str, Any]:
        """Persist a workflow built via the SDK. Uses the existing draft
        flow so saves don't silently overwrite published versions."""
        return self._request(
            "PUT",
            f"/workflow/{workflow_id}",
            json={"workflow_definition": workflow.to_json(), "name": workflow.name},
        )

    # ── low-level ──────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                message = (
                    body.get("detail")
                    or body.get("message")
                    or resp.text
                    if isinstance(body, dict)
                    else resp.text
                )
            except ValueError:
                body = resp.text
                message = resp.text
            raise ApiError(resp.status_code, message, body=body)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text
