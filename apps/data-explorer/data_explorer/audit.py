"""Explorer audit trail kept outside the clinical/call datastore."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .auth import AdminPrincipal


class FileAuditSink:
    def __init__(self, path: str):
        self.path = Path(path)

    async def record(
        self,
        principal: AdminPrincipal,
        event_type: str,
        *,
        call_id: str | None = None,
        caller_id: str | None = None,
        file_id: str | None = None,
    ) -> None:
        # IDs are identifiers, not bodies of clinical data. Never put transcript
        # or memory text in this audit sink.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "actor": principal.subject,
            "event_type": event_type,
            "call_id": call_id,
            "caller_id": caller_id,
            "file_id": file_id,
        }
        with self.path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry, separators=(",", ":")) + "\n")
