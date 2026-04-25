"""HTTP client for the Dograh REST API.

Most endpoint methods come from `_GeneratedClient` (auto-generated from
the FastAPI OpenAPI spec — see `scripts/generate_sdk.sh`). This class
adds the session/auth/cache surface around that mixin plus a couple of
ergonomic wrappers (`load_workflow`, `save_workflow`) that compose a
generated call with local `Workflow` hydration.

The SDK surface on the backend is controlled by decorating routes with
`@sdk_expose(method="...")`; anything else is invisible here.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ._generated_client import _GeneratedClient
from ._generated_models import (
    NodeSpec,
    NodeTypesResponse,
    UpdateWorkflowRequest,
    WorkflowResponse,
)
from .errors import ApiError, SpecMismatchError
from .workflow import Workflow


class DograhClient(_GeneratedClient):
    """Sync HTTP client. Suitable for scripts, pytest, and the LLM SDK
    exec sandbox.

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

        # Populated by the first call to `list_node_types` / `get_node_type`
        # — avoids repeated round-trips when building a workflow.
        self._spec_cache: dict[str, NodeSpec] = {}
        self._spec_version: str | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> DograhClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @property
    def spec_version(self) -> str | None:
        """Contract version reported by the server, or None until the
        first `list_node_types` / `get_node_type` call."""
        return self._spec_version

    # ── spec discovery overrides (generated methods + caching) ────────

    def list_node_types(self) -> NodeTypesResponse:
        resp = super().list_node_types()
        self._spec_version = resp.spec_version
        for spec in resp.node_types:
            self._spec_cache[spec.name] = spec
        return resp

    def get_node_type(self, name: str) -> NodeSpec:
        cached = self._spec_cache.get(name)
        if cached is not None:
            return cached
        try:
            spec = super().get_node_type(name)
        except ApiError as e:
            if e.status_code == 404:
                raise SpecMismatchError(f"Unknown node type: {name!r}") from e
            raise
        self._spec_cache[name] = spec
        return spec

    # ── ergonomic workflow wrappers ───────────────────────────────────

    def load_workflow(self, workflow_id: int) -> Workflow:
        """Fetch a workflow and hydrate it into an editable `Workflow` builder."""
        resp = self.get_workflow(workflow_id)
        if not resp.workflow_definition:
            raise ApiError(
                200,
                f"Workflow {workflow_id} has no definition to load",
                body=resp.model_dump(mode="json"),
            )
        return Workflow.from_json(
            resp.workflow_definition, client=self, name=resp.name
        )

    def save_workflow(self, workflow_id: int, workflow: Workflow) -> WorkflowResponse:
        """Persist a `Workflow` builder back to the server as a new draft."""
        return self.update_workflow(
            workflow_id,
            body=UpdateWorkflowRequest(
                name=workflow.name,
                workflow_definition=workflow.to_json(),
            ),
        )

    # ── low-level ──────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code >= 400:
            try:
                body = resp.json()
                if isinstance(body, dict):
                    message = body.get("detail") or body.get("message") or resp.text
                else:
                    message = resp.text
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
