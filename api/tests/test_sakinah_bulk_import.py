"""Coverage for the staged, safe Sakinah bulk scenario importer."""

import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.services.sakinah import bulk_import as importer

pytestmark = pytest.mark.asyncio


class FakeUpload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self.data = data

    async def read(self, limit: int = -1) -> bytes:
        return self.data if limit < 0 else self.data[:limit]


class MemoryScenarioDB:
    def __init__(self, initial=None):
        self.items = [dict(item) for item in (initial or [])]
        self.next_id = len(self.items) + 1

    async def get_sakinah_scenarios(self, user_id: int):
        return [dict(item) for item in self.items if item.get("user_id", user_id) == user_id]

    async def create_sakinah_scenario(self, user_id: int, scenario: dict):
        item = {
            "id": f"scenario-{self.next_id}",
            "user_id": user_id,
            "sequence": len(self.items) + 1,
            "title": scenario["title"],
            **scenario,
        }
        self.next_id += 1
        self.items.append(item)
        return dict(item)

    async def update_sakinah_scenario(self, user_id: int, scenario_id: str, scenario: dict):
        for item in self.items:
            if item["id"] == scenario_id and item.get("user_id", user_id) == user_id:
                item.update(scenario)
                return dict(item)
        return None


class FailingScenarioDB(MemoryScenarioDB):
    async def create_sakinah_scenario(self, user_id: int, scenario: dict):
        if scenario["title"] == "Fail this scenario":
            raise RuntimeError("simulated independent write failure")
        return await super().create_sakinah_scenario(user_id, scenario)


@pytest.fixture(autouse=True)
def isolated_preview_state(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "STATE_ROOT", tmp_path / "preview-state")


def scenario(title: str, **overrides) -> dict:
    value = {
        "mode": "structured",
        "scenario_title": title,
        "service_user_age": "40",
        "persona": "A cautious service user.",
        "gender": "Female",
        "language": "English",
        "emotional_state": {"category": "Guarded", "description": "Cautious"},
        "communication_style": "Brief and hesitant.",
        "initial_information": "The immediate concern.",
        "hidden_information": "The concern beneath the surface.",
        "disclosure": "Disclose more after rapport.",
        "behaviour": "Answer briefly, then open up when treated with care.",
        "background_context": "Relevant context.",
        "additional_factors": "Do not invent facts.",
        "optional_free_notes": "Test fixture.",
    }
    value.update(overrides)
    return value


def upload_json(filename: str, value) -> FakeUpload:
    data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
    return FakeUpload(filename, data)


def upload_zip(entries: dict[str, bytes | str]) -> FakeUpload:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return FakeUpload("scenarios.zip", buffer.getvalue())


async def preview(uploaded, db=None):
    database = db or MemoryScenarioDB()
    _, response = await importer.build_preview(uploaded, 42, database)
    return response, database


async def commit(response, db, policy="skip_existing"):
    return await importer.commit_preview_state(
        response["preview_token"], 42, policy, db
    )


async def test_one_valid_json_and_commit():
    response, db = await preview([upload_json("one.json", scenario("One"))])
    assert response["files_detected"] == 1
    assert response["valid"] == 1
    assert response["invalid"] == 0
    assert response["items"][0]["scenario_title"] == "One"

    committed = await commit(response, db, "import_as_new")
    assert committed["imported"] == 1
    assert committed["failed"] == 0
    assert committed["items"][0]["status"] == "imported"


async def test_multiple_valid_json_files():
    response, _ = await preview([
        upload_json("one.json", scenario("One")),
        upload_json("two.JSON", scenario("Two")),
    ])
    assert (response["files_detected"], response["valid"], response["invalid"]) == (2, 2, 0)


async def test_valid_zip_with_multiple_json_files():
    response, _ = await preview([
        upload_zip({
            "nested/one.json": json.dumps(scenario("One")),
            "nested/two.json": json.dumps(scenario("Two")),
        })
    ])
    assert (response["files_detected"], response["valid"]) == (2, 2)
    assert {item["source_filename"] for item in response["items"]} == {
        "nested/one.json",
        "nested/two.json",
    }


async def test_malformed_json_is_reported_without_stopping_other_files():
    response, _ = await preview([
        upload_json("bad.json", b"{not json"),
        upload_json("good.json", scenario("Good")),
    ])
    assert (response["valid"], response["invalid"]) == (1, 1)
    assert "malformed JSON" in response["items"][0]["validation_error"]


async def test_duplicate_policies_are_explicit_and_ownership_scoped():
    response, db = await preview([upload_json("same.json", scenario("Same title"))])
    await commit(response, db, "import_as_new")

    duplicate_preview, _ = await preview([upload_json("same.json", scenario(" Same   TITLE "))], db)
    assert duplicate_preview["duplicates"] == 1
    assert duplicate_preview["already_existing"] == 1

    skipped = await commit(duplicate_preview, db, "skip_existing")
    assert skipped["imported"] == 0
    assert skipped["items"][0]["status"] == "skipped"

    replaced_preview, _ = await preview([upload_json("same.json", scenario("Same title", notes="Replaced"))], db)
    replaced = await commit(replaced_preview, db, "replace_existing")
    assert replaced["imported"] == 1
    assert replaced["items"][0]["status"] == "replaced"

    new_preview, _ = await preview([upload_json("same.json", scenario("Same title"))], db)
    imported_as_new = await commit(new_preview, db, "import_as_new")
    assert imported_as_new["imported"] == 1
    assert len(db.items) == 2


async def test_ignored_macos_metadata_and_manifest_json():
    response, _ = await preview([
        upload_zip({
            "__MACOSX/._scenario.json": json.dumps(scenario("Ignored")),
            ".DS_Store": b"not a scenario",
            "manifest.json": json.dumps(scenario("Ignored manifest")),
            "manifest.csv": b"title\nIgnored",
            "VALIDATION.txt": b"validation metadata",
            "folder/real.json": json.dumps(scenario("Real")),
        })
    ])
    assert response["files_detected"] == 1
    assert response["valid"] == 1
    assert response["items"][0]["scenario_title"] == "Real"


async def test_zip_path_traversal_is_rejected():
    with pytest.raises(importer.BulkImportError, match="Unsafe ZIP path"):
        await preview([upload_zip({"../outside.json": json.dumps(scenario("Unsafe"))})])


async def test_oversized_archive_is_rejected_before_extraction(monkeypatch):
    monkeypatch.setattr(importer, "MAX_DECOMPRESSED_BYTES", 32)
    with pytest.raises(importer.BulkImportError, match="decompressed"):
        await preview([upload_zip({"large.json": json.dumps(scenario("Too large"))})])


async def test_safety_limits_and_unsupported_file_types():
    with pytest.raises(importer.BulkImportError, match="No JSON"):
        await preview([FakeUpload("notes.txt", b"not accepted")])
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(importer, "MAX_ARCHIVE_FILES", 1)
    try:
        with pytest.raises(importer.BulkImportError, match="too many entries"):
            await preview([upload_zip({"one.json": b"{}", "two.json": b"{}"})])
        with pytest.raises(importer.BulkImportError, match="Too many JSON files"):
            await preview([
                upload_json("one.json", scenario("One")),
                upload_json("two.json", scenario("Two")),
            ])
    finally:
        monkeypatch.undo()


async def test_saudian_title_prefix_and_utf8_arabic_are_preserved():
    title = "*** الناجح في الرياض الذي لا يعرف أحد أنه مكتئب"
    response, db = await preview([upload_json("saudi.json", scenario(title, language="العربية"))])
    assert response["items"][0]["scenario_title"] == title
    committed = await commit(response, db, "import_as_new")
    assert committed["items"][0]["scenario_title"] == title


async def test_partial_import_failure_has_independent_outcomes():
    db = FailingScenarioDB()
    response, _ = await preview([
        upload_json("good.json", scenario("Good")),
        upload_json("fail.json", scenario("Fail this scenario")),
    ], db)
    committed = await commit(response, db, "import_as_new")
    assert committed["imported"] == 1
    assert committed["failed"] == 1
    assert [item["status"] for item in committed["items"]] == ["imported", "failed"]


async def test_preview_does_not_write_database_and_commit_is_single_use():
    response, db = await preview([upload_json("one.json", scenario("One"))])
    assert db.items == []
    await commit(response, db, "import_as_new")
    with pytest.raises(importer.BulkImportError, match="expired"):
        await importer.commit_preview_state(
            response["preview_token"], 42, "import_as_new", db
        )


async def test_unauthorized_user_is_rejected():
    from api.routes.sakinah import require_sakinah_bulk_admin

    with pytest.raises(HTTPException) as exc_info:
        await require_sakinah_bulk_admin(SimpleNamespace(is_superuser=False))
    assert exc_info.value.status_code == 403
