import json
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from api import constants


def sessions_directory() -> Path:
    # Read through the module attribute (not a from-import) so tests can
    # patch api.constants.SAKINAH_SESSIONS_DIR at call time.
    return Path(constants.SAKINAH_SESSIONS_DIR)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary_file:
        json.dump(payload, temporary_file, indent=2, ensure_ascii=False)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    temporary_path.replace(path)


def create_pending_session(
    *,
    session_id: str,
    scenario: str,
    name: str | None,
    workflow_id: int,
    workflow_run_id: int,
    organization_id: int,
    started_at: datetime,
) -> None:
    _write_json(
        sessions_directory() / ".pending" / f"{session_id}.json",
        {
            "session_id": session_id,
            "scenario": scenario,
            "name": name,
            "workflow_id": workflow_id,
            "workflow_run_id": workflow_run_id,
            "organization_id": organization_id,
            "started_at": started_at.astimezone(UTC).isoformat(),
        },
    )


def finish_session(
    *,
    session_id: str,
    organization_id: int,
    ended_at: datetime,
    turns: list[dict],
    timings: dict[str, float] | None = None,
) -> Path:
    pending_path = sessions_directory() / ".pending" / f"{session_id}.json"
    if not pending_path.exists():
        raise FileNotFoundError(session_id)

    with pending_path.open(encoding="utf-8") as pending_file:
        session = json.load(pending_file)
    if session["organization_id"] != organization_id:
        raise PermissionError(session_id)

    final_payload = {
        "session_id": session["session_id"],
        "scenario": session["scenario"],
        "workflow_run_id": session["workflow_run_id"],
        "started_at": session["started_at"],
        "ended_at": ended_at.astimezone(UTC).isoformat(),
        "timings": timings or {},
        "turns": turns,
    }
    final_path = sessions_directory() / f"{session_id}.json"
    _write_json(final_path, final_payload)
    pending_path.unlink(missing_ok=True)
    return final_path
