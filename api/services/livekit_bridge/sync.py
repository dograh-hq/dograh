"""LiveKit Bridge — dispatch rule sync service.

Keeps LiveKit SIP dispatch rules in sync with Dograh workflows.
Stores mapping in workflow.workflow_configurations["livekit"].
"""

import json
import os
from typing import Optional

from loguru import logger


LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")


def _is_configured() -> bool:
    return bool(LIVEKIT_URL and LIVEKIT_API_KEY and LIVEKIT_API_SECRET)


async def _lk_api(method: str, path: str, body: dict | None = None) -> dict:
    """Call LiveKit Admin API. Returns parsed JSON response."""
    import httpx
    import base64

    auth = base64.b64encode(f"{LIVEKIT_API_KEY}:{LIVEKIT_API_SECRET}".encode()).decode()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.request(
            method,
            f"{LIVEKIT_URL.rstrip('/')}/{path.lstrip('/')}",
            headers={
                "Authorization": f"Bearer {auth}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


async def sync_workflow_dispatch_rule(
    workflow_id: int,
    org_id: int,
    sip_trunk_id: str | None = None,
) -> dict:
    """Create or update the LiveKit SIP dispatch rule for a workflow.

    Returns the mapping dict stored in workflow.workflow_configurations["livekit"].

    If sip_trunk_id is None, looks for an existing trunk or creates one.
    """
    if not _is_configured():
        logger.debug("LiveKit not configured — skipping dispatch rule sync")
        return {}

    mapping = {}

    # 1) Ensure a SIP inbound trunk exists
    if sip_trunk_id:
        mapping["sip_trunk_id"] = sip_trunk_id
    else:
        sip_trunk_id = await _find_or_create_trunk()
        if sip_trunk_id:
            mapping["sip_trunk_id"] = sip_trunk_id

    if not sip_trunk_id:
        return mapping

    # 2) Create dispatch rule
    rule_name = f"dograh-wf-{workflow_id}"
    try:
        resp = await _lk_api(
            "POST",
            "/twirp/livekit.SIP/CreateSIPDispatchRule",
            {
                "name": rule_name,
                "trunk_ids": [sip_trunk_id],
                "rule": {
                    "dispatch_rule_individual": {
                        "room_prefix": f"dograh-call-{workflow_id}-",
                    }
                },
                "metadata": json.dumps({
                    "workflow_id": workflow_id,
                    "org_id": org_id,
                    "channel": "voice_sip",
                }),
                "room_config": {
                    "agents": [{"agent_name": "dograh-agent"}],
                },
            },
        )
        mapping["dispatch_rule_id"] = resp.get("sip_dispatch_rule_id", "")
        logger.info(
            "LiveKit dispatch rule synced for workflow {}: {}",
            workflow_id, mapping["dispatch_rule_id"],
        )
    except Exception as exc:
        logger.warning("Failed to create LiveKit dispatch rule for workflow {}: {}", workflow_id, exc)

    return mapping


async def _find_or_create_trunk() -> Optional[str]:
    """Find an existing dograh trunk, or create one."""
    try:
        # List trunks
        resp = await _lk_api("GET", "/twirp/livekit.SIP/ListSIPInboundTrunk", {})
        items = resp.get("items", [])
        for trunk in items:
            if "dograh" in (trunk.get("name") or ""):
                return trunk.get("sip_trunk_id")
    except Exception:
        pass

    # Create new trunk
    try:
        resp = await _lk_api(
            "POST",
            "/twirp/livekit.SIP/CreateSIPInboundTrunk",
            {
                "name": "dograh-inbound",
                "auth_username": "livekit",
                "auth_password": "livekit-secret",
            },
        )
        trunk_id = resp.get("sip_trunk_id", "")
        logger.info("Created LiveKit SIP trunk: {}", trunk_id)
        return trunk_id
    except Exception as exc:
        logger.warning("Failed to create LiveKit SIP trunk: {}", exc)
        return None


async def sync_all_published_workflows(db_client) -> int:
    """Sync dispatch rules for all published workflows. Returns count of synced workflows."""
    if not _is_configured():
        return 0

    from api.db.models import WorkflowModel

    async with db_client.session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(WorkflowModel).where(
                WorkflowModel.status == "active",
                WorkflowModel.released_definition_id.isnot(None),
            )
        )
        workflows = result.scalars().all()

    count = 0
    for wf in workflows:
        try:
            existing = (wf.workflow_configurations or {}).get("livekit", {})
            if existing.get("dispatch_rule_id"):
                # Already synced — skip
                continue

            mapping = await sync_workflow_dispatch_rule(
                workflow_id=wf.id,
                org_id=wf.organization_id,
            )
            if mapping:
                configs = dict(wf.workflow_configurations or {})
                configs["livekit"] = mapping
                await db_client.update_workflow(wf.id, workflow_configurations=configs)
                count += 1
        except Exception as exc:
            logger.warning("Failed to sync LiveKit for workflow {}: {}", wf.id, exc)

    return count
