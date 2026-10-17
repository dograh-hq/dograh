"""Run the call's frozen extraction requests before completion integrations."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.openai.base_llm import BaseOpenAILLMService
from pipecat.utils.run_context import set_current_org_id, set_current_run_id

from api.db import db_client
from api.errors.failure import (
    ErrorSource,
    classify_exception,
    extract_http_status,
    log_failure,
)
from api.schemas.workflow_configurations import CallDispositionOption
from api.services.configuration.model_connections import (
    hydrate_model_configuration_snapshot,
)
from api.services.configuration.run_model_configuration import (
    get_effective_ai_model_configuration_for_run,
)
from api.services.managed_model_services import MPS_CORRELATION_ID_CONTEXT_KEY
from api.services.pipecat.fallback_llm import FallbackLLMProcessor
from api.services.pipecat.service_factory import create_llm_service
from api.services.workflow.conversation_history import (
    build_conversation_history,
    has_user_turns,
)
from api.services.workflow.disposition_extraction import DispositionExtractionService
from api.services.workflow.disposition_mapping import apply_disposition_mapping
from api.services.workflow.dto import ExtractionVariableDTO
from api.services.workflow.pipecat_engine_variable_extractor import (
    ENGINE_OWNED_CONTEXT_KEYS,
    ExtractionResponseError,
    extract_variables,
)

FINAL_EXTRACTION_BUDGET_SECONDS = 60.0
EXTRACTION_ATTEMPT_TIMEOUT_SECONDS = 15.0
RETRY_DELAYS = (2.0, 5.0)


async def _close_llm(llm: Any) -> None:
    try:
        async with asyncio.timeout(5):
            await llm.cleanup()
    except Exception as exc:  # noqa: BLE001 - cleanup must not discard extraction results
        logger.warning(
            "Final extraction client cleanup failed ({})", type(exc).__name__
        )
    finally:
        # Pipecat's OpenAI-compatible services own an SDK client but their
        # processor cleanup does not close it. Fallbacks own several services.
        processors = llm.processors if isinstance(llm, FallbackLLMProcessor) else [llm]
        for processor in processors:
            if isinstance(processor, BaseOpenAILLMService):
                try:
                    async with asyncio.timeout(5):
                        await processor._client.close()
                except Exception as exc:  # noqa: BLE001 - attempt each client's cleanup
                    logger.warning(
                        "Final extraction HTTP client cleanup failed ({})",
                        type(exc).__name__,
                    )


async def _create_llm(user_config, correlation_id):
    # Vertex constructors refresh credentials synchronously. Keep that network
    # request off the worker loop, including when the extraction deadline expires.
    task = asyncio.create_task(
        asyncio.to_thread(
            create_llm_service,
            user_config,
            correlation_id=correlation_id,
            usage_context="variable_extraction",
        )
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:

        def close_late_client(done):
            if not done.cancelled() and done.exception() is None:
                asyncio.create_task(_close_llm(done.result()))

        task.add_done_callback(close_late_client)
        raise


async def _retry(
    operation: Callable[[], Awaitable[Any]], *, run_id: int, organization_id: int
):
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            async with asyncio.timeout(EXTRACTION_ATTEMPT_TIMEOUT_SECONDS):
                return await operation()
        except Exception as exc:
            failure = classify_exception(exc, source=ErrorSource.LLM)
            retryable = (
                extract_http_status(exc) == 429
                or failure.retryable is True
                or isinstance(exc, (TimeoutError, ExtractionResponseError))
            )
            if not retryable or attempt == len(RETRY_DELAYS):
                log_failure(
                    failure, workflow_run_id=run_id, organization_id=organization_id
                )
                raise
            await asyncio.sleep(RETRY_DELAYS[attempt])


async def run_final_extraction(run_id: int, organization_id: int, token: str) -> None:
    run, run_org = await db_client.get_workflow_run_with_context(run_id)
    if run is None or run_org != organization_id:
        raise ValueError("Workflow run not found in completion organization")
    completion = (run.extra or {}).get("workflow_completion") or {}
    state = completion.get("final_extraction") or {}
    if state.get("status") != "pending":
        return
    set_current_run_id(str(run_id))
    set_current_org_id(organization_id)
    variables: dict = {}
    disposition = None
    disposition_request: dict = {}
    errors: list[str] = []

    async def with_llm(request, operation):
        # Validate the pinned definition through the run's organization even
        # when it belongs to an agent visited after the original workflow.
        definition = await db_client.get_workflow_definition(
            request["workflow_id"], request["definition_id"], organization_id
        )
        if definition is None:
            raise ValueError("Extraction definition is unavailable")
        snapshot = request.get("model_configuration_snapshot")
        if snapshot:
            config = await hydrate_model_configuration_snapshot(
                organization_id, snapshot
            )
        elif request["definition_id"] == run.definition_id:
            config = await get_effective_ai_model_configuration_for_run(
                organization_id=organization_id,
                workflow_run=run,
            )
        else:
            raise ValueError("Transferred agent has no model snapshot")
        llm = await _create_llm(
            config, (run.initial_context or {}).get(MPS_CORRELATION_ID_CONTEXT_KEY)
        )
        try:
            return await _retry(
                lambda: operation(llm),
                run_id=run_id,
                organization_id=organization_id,
            )
        finally:
            await _close_llm(llm)

    try:
        async with asyncio.timeout(FINAL_EXTRACTION_BUDGET_SECONDS):
            payload = (run.logs or {}).get("final_extraction") or {}
            if payload.get("version") != 1 or "conversation" not in (run.logs or {}):
                raise ValueError("Missing or unsupported final extraction snapshot")
            context = LLMContext(messages=run.logs["conversation"])
            conversation = build_conversation_history(context)
            for request in payload["requests"]:
                try:
                    requested = [
                        ExtractionVariableDTO.model_validate(variable)
                        for variable in request["variables"]
                        if variable["name"] not in ENGINE_OWNED_CONTEXT_KEYS
                    ]
                    if not requested:
                        continue
                    result = await with_llm(
                        request,
                        lambda llm, requested=requested, request=request: (
                            extract_variables(
                                llm,
                                requested,
                                request.get("conversation_history", conversation),
                                extraction_prompt=request["prompt"],
                                strict=True,
                            )
                        ),
                    )
                    names = {variable.name for variable in requested}
                    variables.update(
                        {key: value for key, value in result.items() if key in names}
                    )
                except Exception as exc:  # noqa: BLE001 - preserve other nodes' results
                    errors.append(type(exc).__name__)
            disposition_request = payload["disposition"]
            if disposition_request["options"] and has_user_turns(context):
                options = [
                    CallDispositionOption.model_validate(option)
                    for option in disposition_request["options"]
                ]
                disposition = await with_llm(
                    disposition_request,
                    lambda llm: DispositionExtractionService(
                        llm=llm,
                        context=context,
                        options=options,
                        template_context=None,
                    ).extract(raise_on_error=True),
                )
    except Exception as exc:  # noqa: BLE001 - persist failure before integrations
        errors.append(type(exc).__name__)
    status = "failed" if errors else "done"
    if errors:
        logger.warning("Final extraction failed for run {} ({})", run_id, errors[-1])
    saved = await db_client.finish_final_extraction(
        run_id,
        organization_id,
        token,
        variables=variables,
        disposition=disposition,
        fallback=disposition_request.get("fallback"),
        mapped_disposition=apply_disposition_mapping(
            disposition_request.get("mapping") or {}, disposition
        ),
        status=status,
        error_class=errors[-1] if errors else None,
    )
    if not saved:
        raise RuntimeError("Completion lease expired before extraction was saved")
