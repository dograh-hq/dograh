from __future__ import annotations

from typing import Any

import httpx
from loguru import logger
from pydantic import BaseModel, field_validator


class TunerDeliveryConfig(BaseModel):
    base_url: str
    api_key: str
    workspace_id: int
    agent_id: str

    @field_validator("api_key", "agent_id")
    @classmethod
    def _must_not_be_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("workspace_id")
    @classmethod
    def _workspace_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be a positive integer")
        return value


async def post_call(
    config: TunerDeliveryConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = (
        f"{config.base_url}/api/v1/public/call"
        f"?workspace_id={config.workspace_id}"
        f"&agent_remote_identifier={config.agent_id}"
    )
    headers = {"Authorization": f"Bearer {config.api_key}"}

    logger.info(
        "[tuner] posting completed call {} to workspace {} / agent {}",
        payload.get("call_id"),
        config.workspace_id,
        config.agent_id,
    )

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(url, json=payload, headers=headers)

    if response.status_code == 409:
        logger.info("[tuner] call {} already exists in tuner", payload.get("call_id"))
        return {"status": "duplicate", "status_code": response.status_code}

    if response.status_code >= 400:
        logger.error(
            "[tuner] POST failed for call {} with status {}: {}",
            payload.get("call_id"),
            response.status_code,
            response.text[:200],
        )

    response.raise_for_status()

    logger.info(
        "[tuner] POST succeeded for call {} with status {}",
        payload.get("call_id"),
        response.status_code,
    )
    return {"status": "delivered", "status_code": response.status_code}
