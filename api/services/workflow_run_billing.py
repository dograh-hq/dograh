"""Workflow-run billing hooks.

Dograh does not rate or deduct credits locally. MPS owns credit accounting.
For hosted deployments, Dograh reports completed platform usage to MPS.
When a server-minted MPS correlation id exists, MPS uses model-service usage
as the canonical duration. Otherwise Dograh reports the completed run duration.
"""

from typing import Any

from loguru import logger

from api.constants import DEPLOYMENT_MODE
from api.db import db_client
from api.enums import WorkflowRunMode
from api.services.managed_model_services import get_mps_correlation_id
from api.services.mps_service_key_client import mps_service_key_client


def _workflow_run_organization_id(workflow_run) -> int | None:
    workflow = getattr(workflow_run, "workflow", None)
    return getattr(workflow, "organization_id", None)


def _duration_seconds_from_usage_info(workflow_run) -> float | None:
    usage_info: dict[str, Any] = getattr(workflow_run, "usage_info", None) or {}
    duration = usage_info.get("call_duration_seconds")
    try:
        duration_seconds = float(duration)
    except (TypeError, ValueError):
        return None

    return duration_seconds if duration_seconds > 0 else None


def _is_usage_not_ready_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 409:
        return False
    return "usage_not_ready" in (getattr(response, "text", "") or "")


async def report_workflow_run_platform_usage(workflow_run) -> None:
    """Report hosted platform usage for a completed workflow run and deduct platform credits."""
    if getattr(workflow_run, "mode", None) == WorkflowRunMode.TEXTCHAT.value:
        logger.info(
            "Skipping platform usage report for text chat workflow run {}",
            workflow_run.id,
        )
        return

    if not getattr(workflow_run, "is_completed", False):
        logger.warning(
            "Workflow run is not completed in report_workflow_run_platform_usage"
        )
        return

    organization_id = _workflow_run_organization_id(workflow_run)
    if organization_id is None:
        logger.warning(
            "Skipping platform usage report for workflow run {}: no organization_id",
            workflow_run.id,
        )
        return

    duration_seconds = _duration_seconds_from_usage_info(workflow_run)

    # 1. Platform wallet deduction and cost_info breakdown
    if duration_seconds and duration_seconds > 0:
        try:
            workflow = getattr(workflow_run, "workflow", None)
            custom_price_per_sec = getattr(workflow, "price_per_second", None) if workflow else None

            if custom_price_per_sec and float(custom_price_per_sec) > 0:
                cost_usd = round(float(custom_price_per_sec) * duration_seconds, 4)
                rate_per_min = round(float(custom_price_per_sec) * 60.0, 4)
                rates = {
                    "rate_per_minute": rate_per_min,
                    "custom_price_per_second": float(custom_price_per_sec),
                }
            else:
                workflow_config = {}
                definition = None
                try:
                    definition = getattr(workflow_run, "definition", None)
                except Exception:
                    pass

                if not definition and getattr(workflow_run, "definition_id", None):
                    try:
                        definition = await db_client.get_workflow_definition_by_id(
                            workflow_run.definition_id
                        )
                    except Exception:
                        pass

                if definition and getattr(definition, "workflow_configurations", None):
                    workflow_config = definition.workflow_configurations
                elif workflow and getattr(workflow, "workflow_configurations", None):
                    workflow_config = workflow.workflow_configurations

                model_overrides = (workflow_config or {}).get("model_overrides") or {}
                llm_info = model_overrides.get("llm") or {}
                stt_info = model_overrides.get("stt") or {}
                tts_info = model_overrides.get("tts") or {}

                if not (llm_info.get("provider") or stt_info.get("provider") or tts_info.get("provider")):
                    try:
                        from api.services.configuration.ai_model_configuration import (
                            get_organization_ai_model_configuration_v2,
                        )
                        org_config = await get_organization_ai_model_configuration_v2(organization_id)
                        if org_config and getattr(org_config, "byok", None) and getattr(org_config.byok, "pipeline", None):
                            pipe = org_config.byok.pipeline
                            if getattr(pipe, "llm", None):
                                llm_info = {"provider": getattr(pipe.llm, "provider", ""), "model": getattr(pipe.llm, "model", "")}
                            if getattr(pipe, "stt", None):
                                stt_info = {"provider": getattr(pipe.stt, "provider", ""), "model": getattr(pipe.stt, "model", "")}
                            if getattr(pipe, "tts", None):
                                tts_info = {"provider": getattr(pipe.tts, "provider", ""), "model": getattr(pipe.tts, "model", "")}
                    except Exception as e:
                        logger.warning("Could not load organization global model config for billing: {}", e)

                from api.services.platform_keys import calculate_run_composite_rate

                rates = calculate_run_composite_rate(
                    llm_provider=llm_info.get("provider", ""),
                    llm_model=llm_info.get("model", ""),
                    stt_provider=stt_info.get("provider", ""),
                    stt_model=stt_info.get("model", ""),
                    tts_provider=tts_info.get("provider", ""),
                    tts_model=tts_info.get("model", ""),
                    telephony_rate=0.02,
                )
                rate_per_min = rates["rate_per_minute"]
                cost_usd = round((duration_seconds / 60.0) * rate_per_min, 4)

            new_balance = None
            if hasattr(db_client, "update_wallet_balance"):
                new_balance = await db_client.update_wallet_balance(
                    organization_id, -cost_usd
                )

            cost_info = getattr(workflow_run, "cost_info", None) or {}
            cost_info.update(
                {
                    "total_cost_usd": cost_usd,
                    "charge_usd": cost_usd,
                    "call_duration_seconds": duration_seconds,
                    "rate_per_minute": rate_per_min,
                    "rates": rates,
                }
            )
            if new_balance is not None:
                cost_info["wallet_balance_after"] = new_balance

            if hasattr(db_client, "update_workflow_run"):
                await db_client.update_workflow_run(
                    workflow_run.id, cost_info=cost_info
                )

            logger.info(
                "Deducted ${} for run {} ({}s at ${}/min) from org {}. New balance: {}",
                cost_usd,
                workflow_run.id,
                duration_seconds,
                rate_per_min,
                organization_id,
                new_balance,
            )
        except Exception as e:
            logger.warning(
                "Failed to record platform wallet deduction for run {}: {}",
                workflow_run.id,
                e,
            )

    # 2. Report hosted platform usage to MPS if configured
    if DEPLOYMENT_MODE == "oss":
        return

    correlation_id = get_mps_correlation_id(
        getattr(workflow_run, "initial_context", None)
    )
    report_duration = (
        None if correlation_id else duration_seconds
    )
    if not correlation_id and report_duration is None:
        logger.warning(
            "Skipping platform usage report for workflow run {}: no billable duration",
            workflow_run.id,
        )
        return

    try:
        result = await mps_service_key_client.report_platform_usage(
            organization_id=organization_id,
            correlation_id=correlation_id,
            duration_seconds=report_duration,
            workflow_run_id=workflow_run.id,
            metadata={
                "source": "workflow_run_completion",
                "workflow_id": getattr(workflow_run, "workflow_id", None),
                "duration_source": (
                    "mps_correlation" if correlation_id else "dograh_usage_info"
                ),
            },
        )
        logger.info(
            "Reported platform usage for workflow run {} to MPS: {}",
            workflow_run.id,
            result,
        )
    except Exception as e:
        if _is_usage_not_ready_error(e):
            # A run can start and receive an MPS correlation id, then fail or end
            # before billable STT usage is recorded. MPS returns usage_not_ready
            # for that no-platform-fee path, so keep it out of error alerts.
            logger.warning(
                "Failed to report platform usage for workflow run {}: {}",
                workflow_run.id,
                e,
            )
        else:
            logger.error(
                "Failed to report platform usage for workflow run {}: {}",
                workflow_run.id,
                e,
            )


async def report_completed_workflow_run_platform_usage(workflow_run_id: int) -> None:
    """Load a completed workflow run and report platform usage to MPS."""
    workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
    if not workflow_run:
        logger.warning(
            "Skipping platform usage report: workflow run {} not found",
            workflow_run_id,
        )
        return

    await report_workflow_run_platform_usage(workflow_run)
