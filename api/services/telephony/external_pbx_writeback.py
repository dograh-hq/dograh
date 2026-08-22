"""Record a finished run's outcome on the external PBX's copy of the call.

This runs from workflow completion rather than from the ARI hangup strategy,
because that strategy only executes when *Dograh* ends the call. When the
customer or the PBX hangs up first, Asterisk tears the channel down through
StasisEnd and no hangup strategy runs at all -- so a write-back attached there
silently skips the calls the customer hung up on, which is a large share of
them. Completion runs for every call either way.

Writing after the media leg is gone is safe: ``update_fields`` addresses the
PBX's lead record, not the live call.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from api.db import db_client
from api.services.organization_preferences import external_pbx_integrations_enabled
from api.services.telephony.external_pbx import resolve_external_pbx_field_mappings
from api.services.telephony.providers.ari.external_pbx import (
    ExternalPBXAdapter,
    create_adapter,
)


def _disposition_of(gathered: dict[str, Any]) -> str | None:
    return gathered.get("mapped_call_disposition") or gathered.get("call_disposition")


async def _adapter_for_organization(
    organization_id: int,
) -> ExternalPBXAdapter | None:
    """Build the org's configured external-PBX adapter, or None if it has none."""
    if not await external_pbx_integrations_enabled(organization_id):
        return None
    configurations = await db_client.list_telephony_configurations_by_provider(
        organization_id, "ari"
    )
    for configuration in configurations:
        external_pbx = (configuration.credentials or {}).get("external_pbx")
        if not external_pbx:
            continue
        try:
            return create_adapter(external_pbx)
        except ValueError as exc:
            logger.warning(
                f"[External PBX] unusable adapter config on telephony "
                f"configuration {configuration.id}: {exc}"
            )
    return None


async def sync_external_pbx_call_record(workflow_run_id: int) -> None:
    """Write the call's disposition and mapped lead fields back to the PBX.

    Safe to read the disposition straight off the run: the pipeline stamps it
    before its final variable extraction, so it is already stored by the time
    ``on_pipeline_finished`` enqueues the job that calls this.

    Best effort throughout: a rejected or impossible write-back must never fail
    the job that runs it.
    """
    try:
        run = await db_client.get_workflow_run_by_id(workflow_run_id)
        if run is None:
            return

        initial_context: dict[str, Any] = run.initial_context or {}
        identity = initial_context.get("external_pbx_call") or initial_context.get(
            "upstream_pbx"
        )
        if not identity:
            return

        gathered: dict[str, Any] = run.gathered_context or {}
        # A transferred call already recorded XFER before the handoff, while
        # Dograh still owned the leg. Writing again now would overwrite whatever
        # the closer has dispositioned in the meantime.
        if gathered.get("external_pbx_transferred") or gathered.get(
            "upstream_transferred"
        ):
            return

        organization_id = run.workflow.organization_id
        adapter = await _adapter_for_organization(organization_id)
        if adapter is None:
            return
        identity_type = identity.get("type") or identity.get("provider")
        if identity_type != adapter.type:
            logger.warning(
                f"[External PBX] run {workflow_run_id} captured identity "
                f"{identity_type!r} does not match adapter {adapter.type!r}"
            )
            return

        disposition = _disposition_of(gathered)

        workflow_configurations = await db_client.get_workflow_run_configurations(
            workflow_run_id, organization_id
        )
        field_updates = {
            # Every call reports its outcome. The workflow's own mappings are
            # merged last so an explicit mapping onto the same field wins.
            **adapter.disposition_fields(disposition),
            **resolve_external_pbx_field_mappings(
                gathered,
                workflow_configurations.get("external_pbx_field_mappings", []),
            ),
        }
        if field_updates:
            result = await adapter.update_fields(identity, field_updates)
        else:
            result = None
        if result is not None and not result.ok:
            logger.warning(
                f"[External PBX] lead write-back rejected for run "
                f"{workflow_run_id}: {result.message}"
            )

        # Suppression is attempted even when the field write above failed: a
        # caller who asked not to be called again should not stay callable
        # because an unrelated lead field was rejected.
        suppression = await adapter.apply_do_not_call(identity, disposition)
        if suppression is not None:
            logger.info(
                f"[External PBX] do-not-call requested for run {workflow_run_id}: "
                f"ok={suppression.ok} {suppression.message}"
            )
    except Exception as exc:
        logger.error(
            f"[External PBX] lead write-back failed for run {workflow_run_id}: {exc}"
        )
