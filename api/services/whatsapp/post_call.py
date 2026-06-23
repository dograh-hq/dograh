"""Send a WhatsApp template message after a call completes (org-configured, opt-in).

Wired into the post-call flow (api/tasks/run_integrations.py) AFTER recordings/
transcripts are uploaded, so links resolve. Org-scoped config + gates + idempotency
keep it safe; failures are swallowed (never break the post-call pipeline).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from loguru import logger

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.schemas.whatsapp_config import WhatsAppConfig
from api.services.whatsapp.base import WhatsAppProvider
from api.services.whatsapp.providers.aisensy import AiSensyProvider
from api.utils.common import get_backend_endpoints

_TOKEN_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _resolve_provider(cfg: WhatsAppConfig) -> Optional[WhatsAppProvider]:
    if cfg.provider == "aisensy":
        return AiSensyProvider(api_key=cfg.api_key)
    # gupshup / wati / meta adapters slot in here later.
    logger.warning(f"WhatsApp: unknown provider '{cfg.provider}'")
    return None


async def _build_substitutions(workflow_run: Any, public_token: Optional[str]) -> dict:
    initial = workflow_run.initial_context or {}
    gathered = workflow_run.gathered_context or {}
    subs: dict[str, str] = {
        "called_number": str(initial.get("called_number") or initial.get("phone_number") or ""),
        "caller_number": str(initial.get("caller_number") or ""),
        "disposition": str(
            gathered.get("mapped_call_disposition") or gathered.get("call_disposition") or ""
        ),
        "recording_url": "",
        "transcript_url": "",
    }
    if public_token:
        try:
            backend, _ = await get_backend_endpoints()
            base = f"{backend}/api/v1/public/download/workflow/{public_token}"
            subs["recording_url"] = f"{base}/recording"
            subs["transcript_url"] = f"{base}/transcript"
        except Exception:
            pass
    # Lead/campaign variables (CSV columns etc.) — exposed as {{name}} and {{var.name}}
    for k, v in (initial.get("context_variables") or {}).items():
        subs[str(k)] = str(v)
        subs[f"var.{k}"] = str(v)
    return subs


def _render(template: str, subs: dict) -> str:
    return _TOKEN_RE.sub(lambda m: subs.get(m.group(1), ""), template or "")


async def send_post_call_whatsapp(
    *,
    workflow_run: Any,
    organization_id: int,
    public_token: Optional[str],
) -> None:
    raw = await db_client.get_configuration_value(
        organization_id,
        OrganizationConfigurationKey.WHATSAPP_PROVIDERS.value,
        default=None,
    )
    if not raw:
        return
    try:
        cfg = WhatsAppConfig.model_validate(raw)
    except Exception as exc:
        logger.warning(f"WhatsApp config invalid for org {organization_id}: {exc}")
        return
    if not (cfg.enabled and cfg.api_key and cfg.campaign_name):
        return

    initial = workflow_run.initial_context or {}
    to = initial.get("called_number") or initial.get("phone_number")
    if not to:
        return

    # Idempotency — never double-send if the post-call task re-runs.
    logs = workflow_run.logs or {}
    if isinstance(logs, dict) and logs.get("whatsapp_post_call", {}).get("attempted"):
        return

    # Disposition gate
    gathered = workflow_run.gathered_context or {}
    disposition = gathered.get("mapped_call_disposition") or gathered.get("call_disposition")
    if cfg.trigger_dispositions and disposition not in cfg.trigger_dispositions:
        return

    # Minimum-duration gate
    if cfg.min_call_seconds > 0:
        cost = workflow_run.cost_info or {}
        duration = cost.get("call_duration_seconds") or 0
        try:
            if float(duration) < cfg.min_call_seconds:
                return
        except (TypeError, ValueError):
            pass

    subs = await _build_substitutions(workflow_run, public_token)
    params = [_render(p, subs) for p in cfg.template_params]
    media_url = _render(cfg.media_url, subs) if cfg.media_url else None

    provider = _resolve_provider(cfg)
    if provider is None:
        return

    result = await provider.send_template(
        to=to,
        campaign_name=cfg.campaign_name,
        template_params=params,
        sender_name=cfg.sender_name,
        media_url=media_url or None,
        media_filename=cfg.media_filename,
    )

    try:
        await db_client.update_workflow_run(
            workflow_run.id,
            logs={
                "whatsapp_post_call": {
                    "attempted": True,
                    "ok": result.ok,
                    "detail": result.detail,
                    "to": to,
                    "provider": cfg.provider,
                }
            },
        )
    except Exception as exc:
        logger.warning(f"WhatsApp: failed to record result for run {workflow_run.id}: {exc}")
