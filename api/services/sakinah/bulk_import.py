"""Safe, two-stage bulk import support for persisted Sakinah scenarios.

The preview state contains only parsed, schema-shaped scenario payloads and is
kept in a short-lived temporary directory. Uploaded content is never executed
and is not written to the database until the commit stage.
"""

import asyncio
import io
import json
import os
import secrets
import stat
import tempfile
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal

from loguru import logger
from pydantic import ValidationError

from api.schemas.sakinah import ScenarioWriteRequest

DuplicatePolicy = Literal["skip_existing", "replace_existing", "import_as_new"]

MAX_UPLOAD_BYTES = int(
    os.getenv("SAKINAH_BULK_IMPORT_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))
)
MAX_DECOMPRESSED_BYTES = int(
    os.getenv("SAKINAH_BULK_IMPORT_MAX_DECOMPRESSED_BYTES", str(100 * 1024 * 1024))
)
MAX_ARCHIVE_FILES = int(
    os.getenv("SAKINAH_BULK_IMPORT_MAX_ARCHIVE_FILES", "500")
)
MAX_SCENARIO_JSON_BYTES = int(
    os.getenv("SAKINAH_BULK_IMPORT_MAX_JSON_BYTES", str(2 * 1024 * 1024))
)
MAX_COMPRESSION_RATIO = int(
    os.getenv("SAKINAH_BULK_IMPORT_MAX_COMPRESSION_RATIO", "1000")
)
PREVIEW_TTL_SECONDS = int(
    os.getenv("SAKINAH_BULK_IMPORT_PREVIEW_TTL_SECONDS", str(30 * 60))
)
STATE_ROOT = Path(
    os.getenv(
        "SAKINAH_BULK_IMPORT_STATE_DIR",
        str(Path(tempfile.gettempdir()) / "dograh-sakinah-bulk-imports"),
    )
)

IGNORED_FILENAMES = {
    ".ds_store",
    "manifest.csv",
    "manifest.json",
    "validation.txt",
}


class BulkImportError(ValueError):
    """A user-correctable bulk import or archive safety error."""


def normalize_title_for_duplicate_check(title: str) -> str:
    """Normalize only the comparison key; never mutate the stored title."""

    normalized = unicodedata.normalize("NFKC", title)
    return " ".join(normalized.split()).casefold()


def _safe_archive_path(raw_name: str) -> PurePosixPath:
    if not raw_name or "\x00" in raw_name:
        raise BulkImportError("ZIP contains an invalid filename")

    # ZIP uses forward slashes, but accepting backslashes here would allow a
    # Windows-created archive to bypass a traversal check on POSIX.
    normalized_name = raw_name.replace("\\", "/")
    path = PurePosixPath(normalized_name)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise BulkImportError(f"Unsafe ZIP path: {raw_name}")
    if path.parts and ":" in path.parts[0]:
        raise BulkImportError(f"Unsafe ZIP path: {raw_name}")
    return path


def _is_ignored_archive_path(path: PurePosixPath) -> bool:
    return "__MACOSX" in path.parts or path.name.casefold() in IGNORED_FILENAMES


def _is_ignored_upload(filename: str | None) -> bool:
    if not filename:
        return False
    return _is_ignored_archive_path(_safe_archive_path(filename))


def _is_json_filename(filename: str) -> bool:
    return filename.casefold().endswith(".json")


def _display_filename(filename: str | None) -> str:
    if not filename:
        return "uploaded-file"
    path = _safe_archive_path(filename)
    return path.name or "uploaded-file"


async def _read_upload(upload: Any, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise BulkImportError(
            f"{getattr(upload, 'filename', None) or 'Uploaded file'} exceeds the "
            f"{limit // (1024 * 1024)} MiB upload limit"
        )
    return data


def _archive_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o177777
    return stat.S_ISLNK(mode)


def _extract_zip(data: bytes, staging_dir: Path) -> list[tuple[str, bytes | None, str | None]]:
    """Extract JSON members safely, returning (filename, bytes, error)."""

    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise BulkImportError("The uploaded file is not a valid ZIP archive")

    sources: list[tuple[str, bytes | None, str | None]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_FILES:
                raise BulkImportError(
                    f"ZIP contains too many entries; maximum is {MAX_ARCHIVE_FILES}"
                )

            total_declared_size = 0
            json_infos: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            for info in infos:
                path = _safe_archive_path(info.filename)
                if _archive_is_symlink(info):
                    raise BulkImportError(f"ZIP contains an unsafe symlink: {info.filename}")
                total_declared_size += info.file_size
                if total_declared_size > MAX_DECOMPRESSED_BYTES:
                    raise BulkImportError(
                        "ZIP decompressed content exceeds the configured safety limit"
                    )
                if info.compress_size and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO:
                    raise BulkImportError(
                        f"ZIP entry has an unsafe compression ratio: {info.filename}"
                    )
                if info.is_dir() or _is_ignored_archive_path(path):
                    continue
                if _is_json_filename(path.name):
                    json_infos.append((info, path))

            actual_total_size = 0
            for info, path in json_infos:
                source_name = path.as_posix()
                if info.file_size > MAX_SCENARIO_JSON_BYTES:
                    sources.append(
                        (
                            source_name,
                            None,
                            f"JSON file exceeds the {MAX_SCENARIO_JSON_BYTES // (1024 * 1024)} MiB per-file limit",
                        )
                    )
                    continue

                destination = (staging_dir / path.as_posix()).resolve()
                staging_root = staging_dir.resolve()
                if staging_root not in destination.parents:
                    raise BulkImportError(f"Unsafe ZIP path: {info.filename}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("wb") as target:
                    content = bytearray()
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        actual_total_size += len(chunk)
                        if (
                            len(content) + len(chunk) > MAX_SCENARIO_JSON_BYTES
                            or actual_total_size > MAX_DECOMPRESSED_BYTES
                        ):
                            raise BulkImportError(
                                "ZIP decompressed content exceeds the configured safety limit"
                            )
                        target.write(chunk)
                        content.extend(chunk)
                sources.append((source_name, bytes(content), None))
    except zipfile.BadZipFile as exc:
        raise BulkImportError("The uploaded file is not a valid ZIP archive") from exc
    return sources


def _string_value(
    candidate: dict[str, Any], names: str | Iterable[str], fallback: str = ""
) -> str:
    aliases = [names] if isinstance(names, str) else list(names)
    name = next((alias for alias in aliases if alias in candidate), None)
    if name is None or candidate[name] is None:
        return fallback
    value = candidate[name]
    if not isinstance(value, str):
        raise BulkImportError(f'field "{name}" must be a string')
    return value


def normalize_import_candidate(candidate: Any) -> dict[str, Any]:
    """Map the CALMOS scenario export aliases into the persisted write schema."""

    if not isinstance(candidate, dict):
        raise BulkImportError("scenario must be a JSON object")

    emotional_state = candidate.get("emotional_state", candidate.get("emotionalState"))
    if emotional_state is not None and (
        not isinstance(emotional_state, dict) or isinstance(emotional_state, list)
    ):
        raise BulkImportError('field "emotional_state" must be an object')
    emotional_category = emotional_state.get("category") if emotional_state else None
    emotional_description = emotional_state.get("description") if emotional_state else None
    if emotional_category is not None and not isinstance(emotional_category, str):
        raise BulkImportError('field "emotional_state.category" must be a string')
    if emotional_description is not None and not isinstance(emotional_description, str):
        raise BulkImportError('field "emotional_state.description" must be a string')

    raw_mode = candidate["mode"] if "mode" in candidate else None
    prompt = _string_value(candidate, ("freestylePrompt", "prompt", "instructions"))
    mode = ("freestyle" if prompt.strip() else "structured") if "mode" not in candidate else raw_mode
    if mode not in ("structured", "freestyle"):
        raise BulkImportError("mode must be structured or freestyle")

    raw_tags = candidate.get("tags", [])
    if raw_tags is None:
        raw_tags = []
    if not isinstance(raw_tags, list) or any(not isinstance(tag, str) for tag in raw_tags):
        raise BulkImportError('field "tags" must be an array of strings')

    payload = {
        "title": _string_value(
            candidate,
            ("title", "scenario_title"),
            "Imported scenario" if prompt.strip() else "",
        ),
        "category": _string_value(candidate, "category"),
        "tags": raw_tags,
        "mode": mode,
        "persona": _string_value(candidate, "persona"),
        "age": _string_value(candidate, ("age", "service_user_age")),
        "gender": _string_value(candidate, "gender", "Not specified"),
        "language": _string_value(candidate, "language", "English"),
        "emotion": _string_value(candidate, "emotion", emotional_category or ""),
        "communication_style": _string_value(
            candidate, ("communicationStyle", "communication_style")
        ),
        "initial_information": _string_value(
            candidate, ("initialInformation", "initial_information")
        ),
        "hidden_information": _string_value(
            candidate, ("hiddenInformation", "hidden_information")
        ),
        "disclosure": _string_value(candidate, "disclosure"),
        "behaviour": _string_value(candidate, "behaviour"),
        "background": _string_value(
            candidate, ("background", "background_context")
        ),
        "additional_factors": _string_value(
            candidate,
            ("additionalFactors", "additional_factors"),
            f"Emotional state detail: {emotional_description}"
            if isinstance(emotional_description, str)
            else "",
        ),
        "notes": _string_value(candidate, ("notes", "optional_free_notes")),
        "freestyle_prompt": prompt,
    }

    try:
        validated = ScenarioWriteRequest(**payload)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            messages.append(f"{location}: {error['msg']}")
        raise BulkImportError("; ".join(messages)) from exc

    if validated.mode == "freestyle" and not validated.freestyle_prompt.strip():
        raise BulkImportError("Freestyle scenarios need instructions")
    if validated.mode == "structured" and (
        not validated.title.strip()
        or not validated.persona.strip()
        or not validated.behaviour.strip()
    ):
        raise BulkImportError("Structured scenarios need title, persona, and behaviour")
    return validated.model_dump()


def _json_candidates(raw: bytes) -> list[Any]:
    text = ""
    try:
        text = raw.decode("utf-8-sig")
        parsed = json.loads(text)
    except UnicodeDecodeError as exc:
        raise BulkImportError("file must contain valid UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        lines = [line for line in text.splitlines() if line.strip()]
        try:
            parsed_lines = [json.loads(line) for line in lines]
        except json.JSONDecodeError as line_exc:
            raise BulkImportError(f"malformed JSON: {line_exc.msg}") from exc
        if not parsed_lines:
            raise BulkImportError("JSON file does not contain any scenarios") from exc
        return parsed_lines

    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("scenarios"), list):
        return parsed["scenarios"]
    return [parsed]


def _new_item(index: int, source_filename: str) -> dict[str, Any]:
    return {
        "item_index": index,
        "source_file": source_filename,
        "source_filename": source_filename,
        "scenario_title": None,
        "payload": None,
        "status": "invalid",
        "validation_status": "invalid",
        "validation_error": None,
        "existing_scenario_id": None,
        "duplicate_item_index": None,
        "scenario_id": None,
    }


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "item_index",
            "source_filename",
            "scenario_title",
            "status",
            "validation_status",
            "validation_error",
            "existing_scenario_id",
            "scenario_id",
        )
    }


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    valid = sum(item["payload"] is not None for item in items)
    invalid = len(items) - valid
    duplicates = sum(
        item["existing_scenario_id"] is not None
        or item["duplicate_item_index"] is not None
        for item in items
    )
    already_existing = sum(item["existing_scenario_id"] is not None for item in items)
    return {
        "files_detected": len({item.get("source_file", item["source_filename"]) for item in items}),
        "valid": valid,
        "invalid": invalid,
        "duplicates": duplicates,
        "already_existing": already_existing,
    }


async def collect_sources(files: list[Any]) -> list[tuple[str, bytes | None, str | None]]:
    """Read accepted uploads into temporary storage with aggregate limits."""

    if not files:
        raise BulkImportError("Upload at least one ZIP or JSON file")

    upload_total = 0
    sources: list[tuple[str, bytes | None, str | None]] = []
    with tempfile.TemporaryDirectory(prefix="dograh-sakinah-upload-") as temp_dir:
        staging_dir = Path(temp_dir)
        for upload in files:
            raw_filename = getattr(upload, "filename", None)
            if _is_ignored_upload(raw_filename):
                continue
            filename = _display_filename(raw_filename)
            lower_name = filename.casefold()
            if lower_name.endswith(".zip"):
                raw = await _read_upload(upload, MAX_UPLOAD_BYTES)
                upload_total += len(raw)
                if upload_total > MAX_UPLOAD_BYTES:
                    raise BulkImportError(
                        f"Combined upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit"
                    )
                sources.extend(_extract_zip(raw, staging_dir))
            elif _is_json_filename(filename):
                raw = await _read_upload(upload, MAX_SCENARIO_JSON_BYTES)
                upload_total += len(raw)
                if upload_total > MAX_UPLOAD_BYTES:
                    raise BulkImportError(
                        f"Combined upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB limit"
                    )
                sources.append((filename, raw, None))
            else:
                # Unsupported files are ignored, just like archive metadata and
                # directories. This lets an administrator drop a mixed selection.
                continue
            if len(sources) > MAX_ARCHIVE_FILES:
                raise BulkImportError(
                    f"Too many JSON files; maximum is {MAX_ARCHIVE_FILES}"
                )
    if not sources:
        raise BulkImportError("No JSON scenario files were found in the upload")
    return sources


async def build_preview(files: list[Any], user_id: int, db_client: Any) -> tuple[str, dict[str, Any]]:
    sources = await collect_sources(files)
    existing = await db_client.get_sakinah_scenarios(user_id)
    existing_by_title = {
        normalize_title_for_duplicate_check(item["title"]): item for item in existing
    }
    seen_titles: dict[str, int] = {}
    items: list[dict[str, Any]] = []
    next_index = 0

    for source_filename, raw, source_error in sources:
        if source_error:
            item = _new_item(next_index, source_filename)
            item["validation_error"] = source_error
            items.append(item)
            next_index += 1
            continue
        try:
            candidates = _json_candidates(raw or b"")
        except BulkImportError as exc:
            item = _new_item(next_index, source_filename)
            item["validation_error"] = str(exc)
            items.append(item)
            next_index += 1
            continue

        for candidate_number, candidate in enumerate(candidates, start=1):
            item_filename = (
                source_filename
                if len(candidates) == 1
                else f"{source_filename} [scenario {candidate_number}]"
            )
            item = _new_item(next_index, item_filename)
            try:
                payload = normalize_import_candidate(candidate)
                item["payload"] = payload
                item["scenario_title"] = payload["title"]
                item["validation_status"] = "valid"
                title_key = normalize_title_for_duplicate_check(payload["title"])
                existing_item = existing_by_title.get(title_key)
                if existing_item:
                    item["existing_scenario_id"] = existing_item["id"]
                elif title_key in seen_titles:
                    item["duplicate_item_index"] = seen_titles[title_key]
                else:
                    seen_titles[title_key] = next_index
                item["status"] = "duplicate" if (
                    item["existing_scenario_id"] or item["duplicate_item_index"] is not None
                ) else "valid"
            except BulkImportError as exc:
                item["validation_error"] = str(exc)
            items.append(item)
            next_index += 1

    if not items:
        raise BulkImportError("No JSON scenario files were found in the upload")
    state = {
        "created_at": time.time(),
        "user_id": user_id,
        "items": items,
    }
    token = await save_preview_state(state)
    response = {**_summary(items), "preview_token": token, "items": [_public_item(item) for item in items]}
    return token, response


async def save_preview_state(state: dict[str, Any]) -> str:
    STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    await asyncio.to_thread(_prune_preview_states)
    while True:
        token = secrets.token_urlsafe(32)
        path = STATE_ROOT / f"{token}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False)
            return token
        except FileExistsError:
            continue


def _prune_preview_states() -> None:
    cutoff = time.time() - PREVIEW_TTL_SECONDS
    if not STATE_ROOT.exists():
        return
    for path in STATE_ROOT.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except FileNotFoundError:
            continue


def _state_path(token: str) -> Path:
    if not token or len(token) > 100 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in token):
        raise BulkImportError("Invalid or expired preview token")
    path = (STATE_ROOT / f"{token}.json").resolve()
    if STATE_ROOT.resolve() not in path.parents:
        raise BulkImportError("Invalid or expired preview token")
    return path


async def load_preview_state(token: str, user_id: int) -> dict[str, Any]:
    await asyncio.to_thread(_prune_preview_states)
    path = _state_path(token)
    try:
        state = await asyncio.to_thread(
            lambda: json.loads(path.read_text(encoding="utf-8"))
        )
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise BulkImportError("Invalid or expired preview token") from exc
    if state.get("user_id") != user_id or time.time() - state.get("created_at", 0) > PREVIEW_TTL_SECONDS:
        raise BulkImportError("Invalid or expired preview token")
    return state


async def delete_preview_state(token: str) -> None:
    try:
        await asyncio.to_thread(_state_path(token).unlink, True)
    except (FileNotFoundError, BulkImportError):
        return


async def commit_preview_state(
    token: str,
    user_id: int,
    duplicate_policy: DuplicatePolicy,
    db_client: Any,
    item_indexes: list[int] | None = None,
) -> dict[str, Any]:
    state = await load_preview_state(token, user_id)
    items = state.get("items", [])
    selected = set(item_indexes) if item_indexes is not None else {
        item["item_index"] for item in items if item.get("payload") is not None
    }
    valid_indexes = {item["item_index"] for item in items}
    if not selected.issubset(valid_indexes):
        raise BulkImportError("item_indexes contains an item that is not in the preview")

    current = await db_client.get_sakinah_scenarios(user_id)
    current_by_title = {
        normalize_title_for_duplicate_check(item["title"]): item for item in current
    }
    working_by_title = dict(current_by_title)
    imported = 0
    failed = 0

    for item in items:
        if item["item_index"] not in selected:
            if item.get("payload") is not None:
                item["status"] = "not_selected"
            continue
        payload = item.get("payload")
        if payload is None:
            item["status"] = "invalid"
            continue

        title_key = normalize_title_for_duplicate_check(payload["title"])
        existing = working_by_title.get(title_key)
        try:
            if existing and duplicate_policy == "skip_existing":
                item["status"] = "skipped"
                item["scenario_id"] = existing["id"]
                continue
            if existing and duplicate_policy == "replace_existing":
                updated = await db_client.update_sakinah_scenario(
                    user_id, existing["id"], payload
                )
                if updated is None:
                    raise BulkImportError("Existing scenario no longer exists")
                item["status"] = "replaced"
                item["scenario_id"] = updated["id"]
                working_by_title[title_key] = updated
                imported += 1
                continue

            created = await db_client.create_sakinah_scenario(user_id, payload)
            item["status"] = "imported"
            item["scenario_id"] = created["id"]
            working_by_title[title_key] = created
            imported += 1
        except Exception as exc:  # independent outcome; never abort the batch
            failed += 1
            item["status"] = "failed"
            item["validation_error"] = str(exc) or "Unable to import scenario"
            logger.exception(
                "Bulk Sakinah scenario import failed for {}", item["source_filename"]
            )

    summary = _summary(items)
    response = {
        **summary,
        "imported": imported,
        "failed": failed,
        "preview_token": token,
        "items": [_public_item(item) for item in items],
    }
    await delete_preview_state(token)
    return response
