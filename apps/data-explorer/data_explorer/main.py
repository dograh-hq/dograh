"""CALMOS Data Explorer API and small same-origin administrative UI."""

from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from .audit import FileAuditSink
from .auth import AdminPrincipal, require_admin
from .config import Settings
from .repository import ExplorerRepository
from .storage import ObjectNotAvailable, S3ObjectStore, safe_filename


def _not_found(kind: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{kind} not found")


async def _call_or_404(repository: ExplorerRepository, call_id: str) -> dict:
    call = await repository.get_call(call_id)
    if call is None:
        raise _not_found("Call")
    return call


def build_call_export(call: dict, conversation: list[dict], scores: dict, memory: dict, files: list[dict]) -> dict:
    """Stable export envelope while retaining names from the source schema."""
    call_fields = {key: value for key, value in call.items() if key not in {"full_transcript", "organization_id"}}
    return {
        "schema_version": "1.0",
        "exported_at": datetime.now(UTC).isoformat(),
        "call": {**call_fields, "source": {"table": "workflow_runs", "record_id": str(call["run_id"])}},
        "agent_run": {"id": call["run_id"], "workflow_id": call["workflow_id"], "agent": call.get("agent"), "source": {"table": "workflow_runs", "record_id": str(call["run_id"])}},
        "caller": {"service_user_id": call.get("caller_id"), "caller_identifier": call.get("caller_identifier"), "telephone_number": call.get("telephone_number")},
        "transcript": call.get("full_transcript"),
        "conversation": conversation,
        "scores": scores.get("score", {}),
        "safety": {"events": scores.get("events", [])},
        "clinical_evaluation": (scores.get("score") or {}).get("clinical_evaluation", {}),
        "memory": memory,
        "files": [
            {key: value for key, value in item.items() if key not in {"signed_url"}}
            for item in files
        ],
    }


def build_memory_export(caller: dict, memory: dict, calls: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "exported_at": datetime.now(UTC).isoformat(),
        "caller": caller,
        "memory": memory.get("current", []),
        "memory_history": memory.get("history", []),
        "related_calls": [{"call_id": item["call_id"], "run_id": item["run_id"], "started_at": item.get("started_at")} for item in calls],
        "retrieval_status": memory.get("retrieval_status"),
    }


def create_app(
    settings: Settings | None = None,
    repository: ExplorerRepository | None = None,
    object_store: S3ObjectStore | None = None,
    audit_sink: FileAuditSink | None = None,
) -> FastAPI:
    settings = settings or Settings.from_environment()
    repository = repository or ExplorerRepository(settings.database_url)
    object_store = object_store or S3ObjectStore(settings)
    audit_sink = audit_sink or FileAuditSink(settings.audit_log_path)
    admin_dependency = require_admin(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await repository.close()

    app = FastAPI(title="CALMOS Data Explorer", version="1.0.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.repository = repository

    async def audit(principal: AdminPrincipal, event: str, **kwargs) -> None:
        try:
            await audit_sink.record(principal, event, **kwargs)
        except OSError as exc:
            # Data access without a privacy audit trail is intentionally denied.
            raise HTTPException(status_code=503, detail="Audit logging unavailable") from exc

    @app.get("/health")
    async def health() -> dict:
        return {"service": "CALMOS DATA EXPLORER", "version": "1.0.0"}

    @app.get("/api/dashboard")
    async def dashboard(principal: AdminPrincipal = Depends(admin_dependency)) -> dict:
        await audit(principal, "dashboard_viewed")
        return await repository.metrics()

    @app.get("/api/calls")
    async def list_calls(
        call_id: str | None = None,
        run_id: str | None = None,
        caller: str | None = None,
        agent: str | None = None,
        status: str | None = None,
        risk: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=100),
        principal: AdminPrincipal = Depends(admin_dependency),
    ) -> dict:
        await audit(principal, "calls_searched")
        return await repository.list_calls(locals())

    @app.get("/api/calls/{call_id}")
    async def get_call(call_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> dict:
        call = await _call_or_404(repository, call_id)
        await audit(principal, "call_viewed", call_id=call_id, caller_id=call.get("caller_id"))
        return call

    @app.get("/api/calls/{call_id}/conversation")
    async def get_conversation(call_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> list[dict]:
        call = await _call_or_404(repository, call_id)
        await audit(principal, "conversation_viewed", call_id=call_id, caller_id=call.get("caller_id"))
        return await repository.conversation(call["run_id"])

    @app.get("/api/calls/{call_id}/memory")
    async def get_memory(call_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> dict:
        call = await _call_or_404(repository, call_id)
        await audit(principal, "memory_viewed", call_id=call_id, caller_id=call.get("caller_id"))
        return await repository.memory(call)

    @app.get("/api/calls/{call_id}/files")
    async def get_files(call_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> list[dict]:
        call = await _call_or_404(repository, call_id)
        await audit(principal, "files_viewed", call_id=call_id, caller_id=call.get("caller_id"))
        return await repository.files(call)

    @app.get("/api/calls/{call_id}/files/{file_id}/download")
    async def download_call_file(call_id: str, file_id: str, inline: bool = False, return_url: bool = False, principal: AdminPrincipal = Depends(admin_dependency)):
        call = await _call_or_404(repository, call_id)
        file = await repository.file_for_call(call, file_id)
        if file is None:
            raise _not_found("File")
        try:
            url = await object_store.presigned_download(file["object_key"], file["file_name"], inline=inline)
        except ObjectNotAvailable as exc:
            raise HTTPException(status_code=404, detail="Associated object is unavailable") from exc
        await audit(principal, "file_downloaded", call_id=call_id, caller_id=call.get("caller_id"), file_id=file_id)
        if return_url:
            return {"url": url, "expires_in": settings.presign_expiry_seconds}
        return RedirectResponse(url, status_code=307)

    @app.get("/api/files/{file_id}")
    async def get_file(file_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> dict:
        resolved = await repository.recording_file(file_id)
        if resolved is None:
            raise _not_found("File")
        call, file = resolved
        await audit(principal, "file_viewed", call_id=call["call_id"], caller_id=call.get("caller_id"), file_id=file_id)
        return file

    @app.get("/api/files/{file_id}/download")
    async def download_file(file_id: str, inline: bool = False, return_url: bool = False, principal: AdminPrincipal = Depends(admin_dependency)):
        resolved = await repository.recording_file(file_id)
        if resolved is None:
            raise _not_found("File")
        call, file = resolved
        try:
            url = await object_store.presigned_download(file["object_key"], file["file_name"], inline=inline)
        except ObjectNotAvailable as exc:
            raise HTTPException(status_code=404, detail="Associated object is unavailable") from exc
        await audit(principal, "file_downloaded", call_id=call["call_id"], caller_id=call.get("caller_id"), file_id=file_id)
        if return_url:
            return {"url": url, "expires_in": settings.presign_expiry_seconds}
        return RedirectResponse(url, status_code=307)

    @app.get("/api/calls/{call_id}/export")
    async def call_export(call_id: str, download: bool = False, principal: AdminPrincipal = Depends(admin_dependency)):
        call = await _call_or_404(repository, call_id)
        payload = build_call_export(call, await repository.conversation(call["run_id"]), await repository.scores(call["run_id"]), await repository.memory(call), await repository.files(call))
        await audit(principal, "call_json_exported", call_id=call_id, caller_id=call.get("caller_id"))
        headers = {"Content-Disposition": f'attachment; filename="sakinah-call-{safe_filename(call_id)}.json"'} if download else {}
        return JSONResponse(payload, headers=headers)

    @app.get("/api/calls/{call_id}/package")
    async def call_package(call_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> Response:
        call = await _call_or_404(repository, call_id)
        conversation, scores, memory, files = await repository.conversation(call["run_id"]), await repository.scores(call["run_id"]), await repository.memory(call), await repository.files(call)
        call_export_data = build_call_export(call, conversation, scores, memory, files)
        caller = await repository.user(call["caller_id"]) if call.get("caller_id") else None
        calls = await repository.user_calls(call["caller_id"]) if call.get("caller_id") else []
        memory_export_data = build_memory_export(caller or {}, memory, calls)
        content = io.BytesIO()
        used_bytes = 0
        try:
            with ZipFile(content, "w", ZIP_DEFLATED) as archive:
                archive.writestr("call.json", json.dumps(call_export_data, indent=2))
                archive.writestr("memory.json", json.dumps(memory_export_data, indent=2))
                if call.get("full_transcript"):
                    archive.writestr("transcript.txt", call["full_transcript"])
                for file in files:
                    data = await object_store.bytes(file["object_key"], settings.package_max_bytes - used_bytes)
                    used_bytes += len(data)
                    archive.writestr(f"files/{safe_filename(file['file_name'])}", data)
        except ObjectNotAvailable as exc:
            raise HTTPException(status_code=409, detail="Package could not include an associated object") from exc
        await audit(principal, "call_package_exported", call_id=call_id, caller_id=call.get("caller_id"))
        return Response(content.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="sakinah-call-{safe_filename(call_id)}.zip"'})

    @app.get("/api/users/{caller_id}")
    async def get_user(caller_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> dict:
        user = await repository.user(caller_id)
        if user is None:
            raise _not_found("User")
        calls = await repository.user_calls(caller_id)
        await audit(principal, "user_viewed", caller_id=caller_id)
        return {**user, "first_call": calls[-1].get("started_at") if calls else None, "latest_call": calls[0].get("started_at") if calls else None, "number_of_calls": len(calls)}

    @app.get("/api/users/{caller_id}/calls")
    async def get_user_calls(caller_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> list[dict]:
        if not await repository.user(caller_id):
            raise _not_found("User")
        await audit(principal, "user_calls_viewed", caller_id=caller_id)
        return await repository.user_calls(caller_id)

    @app.get("/api/users/{caller_id}/memory")
    async def get_user_memory(caller_id: str, principal: AdminPrincipal = Depends(admin_dependency)) -> dict:
        user = await repository.user(caller_id)
        if user is None:
            raise _not_found("User")
        calls = await repository.user_calls(caller_id)
        memory = await repository.memory({"caller_id": caller_id, "run_id": -1, "started_at": None})
        await audit(principal, "memory_viewed", caller_id=caller_id)
        return build_memory_export(user, memory, calls)

    @app.get("/api/users/{caller_id}/memory/export")
    async def user_memory_export(caller_id: str, principal: AdminPrincipal = Depends(admin_dependency)):
        user = await repository.user(caller_id)
        if user is None:
            raise _not_found("User")
        calls = await repository.user_calls(caller_id)
        memory = await repository.memory({"caller_id": caller_id, "run_id": -1, "started_at": None})
        await audit(principal, "memory_json_exported", caller_id=caller_id)
        return JSONResponse(build_memory_export(user, memory, calls), headers={"Content-Disposition": f'attachment; filename="sakinah-memory-{safe_filename(caller_id)}.json"'})

    @app.get("/api/schema")
    async def schema(principal: AdminPrincipal = Depends(admin_dependency)) -> dict:
        await audit(principal, "schema_viewed")
        return await repository.schema()

    @app.get("/api/read-only-status")
    async def read_only_status(principal: AdminPrincipal = Depends(admin_dependency)) -> dict:
        await audit(principal, "read_only_status_viewed")
        return await repository.readonly_status()

    @app.get("/", include_in_schema=False)
    async def ui() -> FileResponse:
        return FileResponse(Path(__file__).with_name("static") / "index.html")

    return app


app = create_app()
