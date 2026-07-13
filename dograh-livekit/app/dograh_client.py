from __future__ import annotations

import logging
from typing import Any

import httpx
from app.config import Settings
from app.models import RuntimeConfig

logger = logging.getLogger(__name__)


class DograhClient:
    """HTTP client for Dograh's internal API."""

    def __init__(self, settings: Settings):
        self._base_url = settings.dograh_api_url.rstrip("/")
        self._token = settings.dograh_internal_token
        self._headers = {
            "X-Internal-Token": self._token,
            "Content-Type": "application/json",
        }

    async def fetch_runtime_config(self, deploy_id: str) -> RuntimeConfig:
        """Fetch full runtime config for a deploy."""
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{self._base_url}/api/internal/deploy/{deploy_id}/runtime-config",
                headers=self._headers,
            )
            if response.status_code == 404:
                raise ValueError(f"Deploy {deploy_id} not found")
            response.raise_for_status()
            data = response.json()
            return RuntimeConfig(**data)

    async def search_knowledge(self, org_id: str, query: str, kb_refs: list[str] | None = None) -> dict[str, Any]:
        """Search the knowledge base."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/api/internal/kb/{org_id}/search",
                headers=self._headers,
                json={"query": query, "kb_refs": kb_refs or []},
            )
            response.raise_for_status()
            return response.json()

    async def create_session(
        self,
        deploy_id: str,
        org_id: str,
        room_name: str,
        channel: str,
        agent_id: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Create a session record in Dograh."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/api/internal/sessions",
                headers=self._headers,
                json={
                    "deploy_id": deploy_id,
                    "org_id": org_id,
                    "room_name": room_name,
                    "channel": channel,
                    "agent_id": agent_id,
                    **kwargs,
                },
            )
            response.raise_for_status()
            return response.json()

    async def update_session(self, session_id: str, org_id: str, **fields) -> dict[str, Any]:
        """Update a session record."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.put(
                f"{self._base_url}/api/internal/sessions/{session_id}",
                headers=self._headers,
                json={"org_id": org_id, **fields},
            )
            response.raise_for_status()
            return response.json()

    async def hangup_session(self, session_id: str, org_id: str, deploy_id: str, **kwargs) -> None:
        """Notify Dograh of session hangup."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{self._base_url}/api/internal/sessions/hangup",
                headers=self._headers,
                json={
                    "session_id": session_id,
                    "org_id": org_id,
                    "deploy_id": deploy_id,
                    **kwargs,
                },
            )
            response.raise_for_status()
