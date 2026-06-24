"""Sync a completed call to the org's CRM (org-configured, opt-in).

Mirrors api/services/whatsapp/post_call.py: same org-config + gates + idempotency,
wired into run_integrations.py after recordings/transcripts upload so the links
resolve. Best-effort — never raises into the post-call pipeline.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.schemas.crm_config import CRMConfig
from api.services.integrations.crm.base import CallLog, CRMProvider
from api.services.integrations.crm.providers.gohighlevel import GoHighLevelProvider
from api.utils.common import get_backend_endpoints
from api.utils.secret_crypto import decrypt_secret


def _resolve_provider(cfg: CRMConfig) -> Optional[CRMProvider]:
    if cfg.provider == "gohighlevel":
        return GoHighLevelProvider(api_key=cfg.api_key, location_id=cfg.location_id)
    # leadsquared / kylas / hubspot adapters slot in here later.
    logger.warning(f"CRM: unknown provider '{cfg.provider}'")
    return None


async def _build_call_log(workflow_run: Any, public_token: Optional[str]) -> CallLog:
    initial = workflow_run.initial_context or {}
    gathered = workflow_run.gathered_context or {}
    annotations = workflow_run.annotations or {}
    cost = workflow_run.cost_info or {}
    usage = workflow_run.usage_info or {}
    cvars = initial.get("context_variables") or {}

    recording_url = transcript_url = ""
    if public_token:
        try:
            backend, _ = await get_backend_endpoints()
            base = f"{backend}/api/v1/public/download/workflow/{public_token}"
            recording_url = f"{base}/recording"
            transcript_url = f"{base}/transcript"
        except Exception:
            pass

    duration = cost.get("call_duration_seconds") or usage.get("call_duration_seconds") or 0
    try:
        duration = int(round(float(duration)))
    except (TypeError, ValueError):
        duration = 0

    # Lead identity from CSV columns; phone falls back to the dialed number.
    phone = (
        cvars.get("phone")
        or cvars.get("phone_number")
        or initial.get("called_number")
        or initial.get("phone_number")
        or ""
    )
    name = cvars.get("name") or " ".join(
        x for x in [cvars.get("first_name", ""), cvars.get("last_name", "")] if x
    )

    return CallLog(
        phone=str(phone),
        name=str(name).strip(),
        email=str(cvars.get("email") or ""),
        disposition=str(
            gathered.get("mapped_call_disposition") or gathered.get("call_disposition") or ""
        ),
        duration_seconds=duration,
        recording_url=recording_url,
        transcript_url=transcript_url,
        summary=str(annotations.get("summary") or ""),
        sentiment=str(annotations.get("overall_sentiment") or ""),
        quality_score=annotations.get("call_quality_score"),
        external_id=str(workflow_run.id),
        extra={k: v for k, v in cvars.items() if k not in ("phone", "phone_number", "name", "email")},
    )


async def send_post_call_crm(
    *,
    workflow_run: Any,
    organization_id: int,
    public_token: Optional[str],
) -> None:
    raw = await db_client.get_configuration_value(
        organization_id, OrganizationConfigurationKey.CRM_PROVIDERS.value, default=None
    )
    if not raw:
        return
    try:
        cfg = CRMConfig.model_validate(raw)
    except Exception as exc:
        logger.warning(f"CRM config invalid for org {organization_id}: {exc}")
        return
    cfg.api_key = decrypt_secret(cfg.api_key)  # encrypted at rest
    if not (cfg.enabled and cfg.api_key):
        return

    # Idempotency — never double-write if the post-call task re-runs.
    logs = workflow_run.logs or {}
    if isinstance(logs, dict) and logs.get("crm_post_call", {}).get("attempted"):
        return

    # Disposition gate
    gathered = workflow_run.gathered_context or {}
    disposition = gathered.get("mapped_call_disposition") or gathered.get("call_disposition")
    if cfg.trigger_dispositions and disposition not in cfg.trigger_dispositions:
        return

    # Sentiment gate — e.g. only push interested/positive leads to the CRM.
    if cfg.trigger_sentiments:
        sentiment = str((workflow_run.annotations or {}).get("overall_sentiment") or "").lower()
        if not any(t.lower() in sentiment for t in cfg.trigger_sentiments):
            return

    # Minimum-duration gate
    if cfg.min_call_seconds > 0:
        cost = workflow_run.cost_info or {}
        try:
            if float(cost.get("call_duration_seconds") or 0) < cfg.min_call_seconds:
                return
        except (TypeError, ValueError):
            pass

    provider = _resolve_provider(cfg)
    if provider is None:
        return

    call = await _build_call_log(workflow_run, public_token)
    if not call.phone:
        return

    result = await provider.sync_call(call)

    try:
        await db_client.update_workflow_run(
            workflow_run.id,
            logs={
                "crm_post_call": {
                    "attempted": True,
                    "ok": result.ok,
                    "detail": result.detail,
                    "contact_id": result.contact_id,
                    "provider": cfg.provider,
                }
            },
        )
    except Exception as exc:
        logger.warning(f"CRM: failed to record result for run {workflow_run.id}: {exc}")
