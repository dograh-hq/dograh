import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketState

from api.db import db_client
from api.db.models import UserModel
from api.enums import CallType, WorkflowRunMode
from api.schemas.sakinah import SCENARIO_FIELDS, ScenarioWriteRequest
from api.services.auth.depends import get_user, get_user_ws
from api.services.sakinah.bulk_import import (
    BulkImportError,
    DuplicatePolicy,
    build_preview,
    commit_preview_state,
)
from api.services.sakinah.session_store import (
    create_pending_session,
    finish_session,
)
from api.services.sakinah.simulation import (
    SimulationAuthorizationError,
    simulation_manager,
)
from api.services.sakinah.workflow import ensure_sakinah_workflow
from api.services.workflow.run_creation import prepare_workflow_run_inputs

router = APIRouter(prefix="/sakinah", tags=["sakinah"])

ARTIFACT_RECONCILIATION_ATTEMPTS = 20
ARTIFACT_RECONCILIATION_DELAY_SECONDS = 0.25


def _has_workflow_artifacts(artifacts: dict[str, Any] | None) -> bool:
    if not artifacts:
        return False
    return bool(
        artifacts.get("recording_url")
        or artifacts.get("transcript_url")
        or artifacts.get("recording_file_reference")
    )


async def _wait_for_workflow_artifacts(
    user_id: int, workflow_run_id: int
) -> dict[str, Any] | None:
    """Give the pipeline completion handler time to publish its artifacts.

    The browser can observe the WebRTC socket closing just before the pipeline's
    final event handler has uploaded the recording/transcript. Returning the
    Sakinah row before that handler finishes leaves a permanently incomplete
    white-label summary unless the later reconciliation wins the race.
    """
    artifacts: dict[str, Any] | None = None
    for attempt in range(ARTIFACT_RECONCILIATION_ATTEMPTS):
        artifacts = await db_client.get_workflow_run_artifacts_for_user(
            user_id, workflow_run_id
        )
        if artifacts is None or _has_workflow_artifacts(artifacts):
            return artifacts
        if attempt + 1 < ARTIFACT_RECONCILIATION_ATTEMPTS:
            await asyncio.sleep(ARTIFACT_RECONCILIATION_DELAY_SECONDS)
    return artifacts


class ScenarioResponse(ScenarioWriteRequest):
    id: str
    sequence: int
    created_at: datetime
    updated_at: datetime


class ScenarioListResponse(BaseModel):
    scenarios: list[ScenarioResponse]


def _scenario_payload(request: ScenarioWriteRequest) -> dict[str, Any]:
    return {field: getattr(request, field) for field in SCENARIO_FIELDS}


def _scenario_response(value: dict[str, Any]) -> ScenarioResponse:
    return ScenarioResponse(
        id=value["id"],
        sequence=value["sequence"],
        title=value["title"],
        category=value.get("category", ""),
        tags=value.get("tags", []),
        mode=value["mode"],
        persona=value["persona"],
        age=value["age"],
        gender=value["gender"],
        language=value["language"],
        emotion=value["emotion"],
        communication_style=value["communicationStyle"],
        initial_information=value["initialInformation"],
        hidden_information=value["hiddenInformation"],
        disclosure=value["disclosure"],
        behaviour=value["behaviour"],
        background=value["background"],
        additional_factors=value["additionalFactors"],
        notes=value["notes"],
        freestyle_prompt=value["freestylePrompt"],
        created_at=value["createdAt"],
        updated_at=value["updatedAt"],
    )


def _format_transcript(turns: list[dict[str, Any]]) -> str:
    role_labels = {
        "user": "user",
        "sakinah": "assistant",
        "service_user": "service user",
    }
    return "".join(
        f"[{turn.get('timestamp', '')}] {role_labels.get(turn.get('role'), turn.get('role', 'unknown'))}: {turn.get('text', '')}\n"
        for turn in turns
        if turn.get("text")
    )


@router.get("/scenarios", response_model=ScenarioListResponse)
async def list_scenarios(
    search: str | None = Query(default=None, max_length=200),
    user: UserModel = Depends(get_user),
) -> ScenarioListResponse:
    scenarios = await db_client.get_sakinah_scenarios(user.id, search=search)
    return ScenarioListResponse(
        scenarios=[_scenario_response(item) for item in scenarios]
    )


@router.post("/scenarios", response_model=ScenarioResponse)
async def create_scenario(
    request: ScenarioWriteRequest, user: UserModel = Depends(get_user)
) -> ScenarioResponse:
    if request.mode == "freestyle" and not request.freestyle_prompt.strip():
        raise HTTPException(
            status_code=422, detail="Freestyle scenarios need instructions"
        )
    if request.mode == "structured" and (
        not request.title.strip()
        or not request.persona.strip()
        or not request.behaviour.strip()
    ):
        raise HTTPException(
            status_code=422,
            detail="Structured scenarios need title, persona, and behaviour",
        )
    scenario = await db_client.create_sakinah_scenario(
        user.id, _scenario_payload(request)
    )
    return _scenario_response(scenario)


@router.put("/scenarios/{scenario_id}", response_model=ScenarioResponse)
async def update_scenario(
    scenario_id: str,
    request: ScenarioWriteRequest,
    user: UserModel = Depends(get_user),
) -> ScenarioResponse:
    scenario = await db_client.update_sakinah_scenario(
        user.id, scenario_id, _scenario_payload(request)
    )
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return _scenario_response(scenario)


@router.delete("/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(
    scenario_id: str, user: UserModel = Depends(get_user)
) -> None:
    if not await db_client.delete_sakinah_scenario(user.id, scenario_id):
        raise HTTPException(status_code=404, detail="Scenario not found")


class BulkScenarioImportItemResponse(BaseModel):
    item_index: int
    source_filename: str
    scenario_title: str | None = None
    status: str
    validation_status: str
    validation_error: str | None = None
    existing_scenario_id: str | None = None
    scenario_id: str | None = None


class BulkScenarioImportResponse(BaseModel):
    preview_token: str | None = None
    files_detected: int
    valid: int
    invalid: int
    duplicates: int
    already_existing: int
    imported: int = 0
    failed: int = 0
    items: list[BulkScenarioImportItemResponse]


class BulkScenarioImportCommitRequest(BaseModel):
    preview_token: str = Field(min_length=1, max_length=100)
    duplicate_policy: DuplicatePolicy = "skip_existing"
    item_indexes: list[int] | None = None


async def require_sakinah_bulk_admin(
    user: UserModel = Depends(get_user),
) -> UserModel:
    if not user.is_superuser:
        raise HTTPException(
            status_code=403,
            detail="Administrator privileges are required for bulk scenario imports.",
        )
    return user


@router.post(
    "/scenarios/bulk-import/preview",
    response_model=BulkScenarioImportResponse,
    summary="Preview a bulk Sakinah scenario import",
)
async def preview_bulk_scenarios(
    files: list[UploadFile] = File(...),
    user: UserModel = Depends(require_sakinah_bulk_admin),
) -> BulkScenarioImportResponse:
    try:
        _, response = await build_preview(files, user.id, db_client)
    except BulkImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BulkScenarioImportResponse(**response)


@router.post(
    "/scenarios/bulk-import/commit",
    response_model=BulkScenarioImportResponse,
    summary="Commit a previewed bulk Sakinah scenario import",
)
async def commit_bulk_scenarios(
    request: BulkScenarioImportCommitRequest,
    user: UserModel = Depends(require_sakinah_bulk_admin),
) -> BulkScenarioImportResponse:
    try:
        response = await commit_preview_state(
            request.preview_token,
            user.id,
            request.duplicate_policy,
            db_client,
            request.item_indexes,
        )
    except BulkImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return BulkScenarioImportResponse(**response)


class SakinahRunResponse(BaseModel):
    session_id: uuid.UUID
    agent_id: int
    run_id: int
    service_user_agent_id: int | None = None
    service_user_run_id: int | None = None
    scenario: str
    status: str
    experiment_mode: str | None = None
    transcript: str | None = None
    transcript_url: str | None = None
    conversation: list[dict[str, Any]]
    preview_data: dict[str, Any]
    recording_url: str | None = None
    recording_file_reference: dict[str, Any]
    started_at: datetime
    ended_at: datetime | None = None
    calm_turns: list[dict[str, Any]]
    timings: dict[str, Any]
    created_at: datetime


class SakinahRunListResponse(BaseModel):
    runs: list[SakinahRunResponse]


def _run_response(run) -> SakinahRunResponse:
    return SakinahRunResponse(
        session_id=run.session_id,
        agent_id=run.agent_id,
        run_id=run.run_id,
        service_user_agent_id=run.service_user_agent_id,
        service_user_run_id=run.service_user_run_id,
        scenario=run.scenario,
        status=run.status,
        experiment_mode=run.experiment_mode,
        transcript=run.transcript,
        transcript_url=run.transcript_url,
        conversation=run.conversation or [],
        preview_data=run.preview_data or {},
        recording_url=run.recording_url,
        recording_file_reference=run.recording_file_reference or {},
        started_at=run.started_at,
        ended_at=run.ended_at,
        calm_turns=run.calm_turns or [],
        timings=run.timings or {},
        created_at=run.created_at,
    )


@router.get("/runs", response_model=SakinahRunListResponse)
async def list_sakinah_runs(
    user: UserModel = Depends(get_user),
) -> SakinahRunListResponse:
    runs = await db_client.get_sakinah_runs(user.id)
    return SakinahRunListResponse(runs=[_run_response(run) for run in runs])


@router.get("/runs/{session_id}", response_model=SakinahRunResponse)
async def get_sakinah_run(
    session_id: uuid.UUID, user: UserModel = Depends(get_user)
) -> SakinahRunResponse:
    run = await db_client.get_sakinah_run(user.id, str(session_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_response(run)


class CreateSessionRequest(BaseModel):
    scenario: str = Field(min_length=1, max_length=20_000)
    name: str | None = Field(default=None, max_length=200)
    scenario_id: str | None = Field(default=None, max_length=128)
    scenario_name: str | None = Field(default=None, max_length=500)


class CreateSessionResponse(BaseModel):
    session_id: uuid.UUID
    workflow_id: int
    workflow_run_id: int
    call_id: str
    started_at: datetime


class TranscriptTurn(BaseModel):
    role: Literal["user", "sakinah"]
    text: str = Field(min_length=1)
    final: bool
    timestamp: datetime


class EndSessionRequest(BaseModel):
    turns: list[TranscriptTurn]
    ended_at: datetime | None = None
    timings: dict[str, float] | None = None


class EndSessionResponse(BaseModel):
    session_id: uuid.UUID
    saved: bool


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(
    request: CreateSessionRequest, user: UserModel = Depends(get_user)
) -> CreateSessionResponse:
    scenario = request.scenario.strip()
    if not scenario:
        raise HTTPException(status_code=422, detail="Scenario cannot be blank")

    session_id = uuid.uuid4()
    started_at = datetime.now(UTC)
    workflow = await ensure_sakinah_workflow(db_client, user)
    initial_context = {
        "scenario": scenario,
        "session_id": str(session_id),
        "direction": CallType.INBOUND.value,
        "scenario_id": request.scenario_id,
        "scenario_name": request.scenario_name,
    }
    run_inputs = await prepare_workflow_run_inputs(
        db_client, workflow, initial_context=initial_context
    )
    run = await db_client.create_workflow_run(
        request.name or f"Sakinah {session_id}",
        workflow.id,
        WorkflowRunMode.SMALLWEBRTC.value,
        user.id,
        call_type=CallType.INBOUND,
        organization_id=user.selected_organization_id,
        definition_id=run_inputs.definition_id,
        initial_context=run_inputs.initial_context,
    )
    await db_client.create_sakinah_run(
        session_id=str(session_id),
        user_id=user.id,
        agent_id=workflow.id,
        run_id=run.id,
        scenario=scenario,
        started_at=started_at,
    )
    create_pending_session(
        session_id=str(session_id),
        scenario=scenario,
        name=request.name,
        workflow_id=workflow.id,
        workflow_run_id=run.id,
        organization_id=user.selected_organization_id,
        started_at=started_at,
    )
    return CreateSessionResponse(
        session_id=session_id,
        workflow_id=workflow.id,
        workflow_run_id=run.id,
        call_id=run.call_id,
        started_at=started_at,
    )


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
async def end_session(
    session_id: uuid.UUID,
    request: EndSessionRequest,
    user: UserModel = Depends(get_user),
) -> EndSessionResponse:
    ended_at = request.ended_at or datetime.now(UTC)
    persisted_session = await db_client.get_sakinah_run(user.id, str(session_id))
    if persisted_session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    workflow_artifacts = await _wait_for_workflow_artifacts(
        user.id, persisted_session.run_id
    )
    transcript = _format_transcript(
        [turn.model_dump(mode="json") for turn in request.turns]
    )
    persisted_run = await db_client.complete_sakinah_run(
        user_id=user.id,
        session_id=str(session_id),
        status="completed",
        ended_at=ended_at,
        transcript=transcript,
        conversation=[turn.model_dump(mode="json") for turn in request.turns],
        preview_data={
            "turns": [turn.model_dump(mode="json") for turn in request.turns]
        },
        recording_url=(workflow_artifacts or {}).get("recording_url"),
        transcript_url=(workflow_artifacts or {}).get("transcript_url"),
        recording_file_reference=(workflow_artifacts or {}).get(
            "recording_file_reference", {}
        ),
        timings=request.timings,
    )
    if persisted_run is None:
        raise HTTPException(status_code=404, detail="Session not found")
    # The pipeline handler also performs this reconciliation after uploading
    # artifacts. Keep an explicit final pass here for the common browser-close
    # race, so a saved session immediately exposes its media metadata.
    await db_client.sync_sakinah_run_artifacts(persisted_session.run_id)
    try:
        finish_session(
            session_id=str(session_id),
            organization_id=user.selected_organization_id,
            ended_at=ended_at,
            turns=[turn.model_dump(mode="json") for turn in request.turns],
            timings=request.timings,
        )
    except (FileNotFoundError, PermissionError):
        # The database row is authoritative. The compatibility JSON file may
        # be absent after a container restart, without losing the saved run.
        logger.warning(f"Session JSON unavailable for persisted session {session_id}")
    return EndSessionResponse(session_id=session_id, saved=True)


class StartSimulationRequest(BaseModel):
    scenario: str = Field(min_length=1, max_length=20_000)
    scenario_id: str | None = Field(default=None, max_length=128)
    scenario_name: str | None = Field(default=None, max_length=500)
    max_duration_seconds: int | None = Field(default=None, ge=30, le=900)
    experiment_mode: Literal[
        "baseline", "scores_only", "scores_and_trends", "full_calm_prompt"
    ] = "full_calm_prompt"


class SimulationAgentInfo(BaseModel):
    workflow_id: int
    workflow_run_id: int


class SimulationResponse(BaseModel):
    simulation_id: str
    status: str
    stop_reason: str | None = None
    error: str | None = None
    scenario: str
    started_at: str
    ended_at: str | None = None
    turn_count: int
    experiment_mode: Literal[
        "baseline", "scores_only", "scores_and_trends", "full_calm_prompt"
    ]
    calm_scores: dict[str, Any] = Field(default_factory=dict)
    calm_trend: dict[str, Any] = Field(default_factory=dict)
    agents: dict[str, SimulationAgentInfo]


def _simulation_response(snapshot: dict[str, Any]) -> SimulationResponse:
    return SimulationResponse.model_validate(snapshot)


@router.post("/simulations", response_model=SimulationResponse)
async def start_simulation(
    request: StartSimulationRequest, user: UserModel = Depends(get_user)
) -> SimulationResponse:
    scenario = request.scenario.strip()
    if not scenario:
        raise HTTPException(status_code=422, detail="Scenario cannot be blank")

    try:
        simulation = await simulation_manager.start_simulation(
            user,
            scenario,
            max_duration_seconds=request.max_duration_seconds,
            experiment_mode=request.experiment_mode,
            scenario_id=request.scenario_id,
            scenario_name=request.scenario_name,
        )
    except SimulationAuthorizationError as e:
        raise HTTPException(status_code=402, detail=str(e)) from None
    return _simulation_response(simulation.snapshot())


@router.post("/simulations/{simulation_id}/stop", response_model=SimulationResponse)
async def stop_simulation(
    simulation_id: str, user: UserModel = Depends(get_user)
) -> SimulationResponse:
    try:
        simulation = await simulation_manager.stop_simulation(
            simulation_id,
            user.selected_organization_id,
            reason="user_stopped",
            user_id=user.id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Simulation not found") from None
    return _simulation_response(simulation.snapshot())


@router.get("/simulations/{simulation_id}", response_model=SimulationResponse)
async def get_simulation(
    simulation_id: str, user: UserModel = Depends(get_user)
) -> SimulationResponse:
    try:
        simulation = simulation_manager.get(
            simulation_id, user.selected_organization_id, user.id
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Simulation not found") from None
    return _simulation_response(simulation.snapshot())


@router.websocket("/simulations/{simulation_id}/audio")
async def simulation_audio(
    websocket: WebSocket,
    simulation_id: str,
    user: UserModel = Depends(get_user_ws),
):
    """Stream the live conversation audio (raw 16 kHz mono s16le PCM).

    Both agents' spoken audio is interleaved by arrival order; turns
    alternate, so the result plays back as the full conversation. The
    stream closes when the simulation finalizes.
    """
    if not user.selected_organization_id:
        await websocket.close(code=1008, reason="No organization selected")
        return

    try:
        simulation = simulation_manager.get(
            simulation_id, user.selected_organization_id, user.id
        )
    except KeyError:
        await websocket.close(code=1008, reason="Simulation not found")
        return

    await websocket.accept()
    queue = simulation.subscribe_audio()
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:  # end-of-stream sentinel from finalize
                break
            await websocket.send_bytes(chunk)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Simulation audio WS error for {simulation_id}: {e}")
    finally:
        simulation.unsubscribe_audio(queue)
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass


@router.websocket("/simulations/{simulation_id}/events")
async def simulation_events(
    websocket: WebSocket,
    simulation_id: str,
    user: UserModel = Depends(get_user_ws),
):
    """Stream simulation transcript events: backlog replay, then live events."""
    if not user.selected_organization_id:
        await websocket.close(code=1008, reason="No organization selected")
        return

    try:
        simulation = simulation_manager.get(
            simulation_id, user.selected_organization_id, user.id
        )
    except KeyError:
        await websocket.close(code=1008, reason="Simulation not found")
        return

    await websocket.accept()
    backlog, queue = simulation.subscribe()
    try:
        for event in backlog:
            await websocket.send_json(event)
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("type") == "simulation-status" and (
                event.get("payload") or {}
            ).get("status") in ("completed", "failed"):
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"Simulation events WS error for {simulation_id}: {e}")
    finally:
        simulation.unsubscribe(queue)
        if websocket.application_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:
                pass
