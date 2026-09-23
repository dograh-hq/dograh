"""Inline editing of .txt/.md knowledge base documents.

A save overwrites the stored file and re-runs processing. The previous version
must keep serving agents until that succeeds, and concurrent saves or a save
racing a running job must never leave the file and the index out of step.
"""

import hashlib
from unittest.mock import AsyncMock, patch

import aiofiles
import pytest

ORIGINAL_TEXT = "Price: $10\n"
ORIGINAL_HASH = hashlib.sha256(ORIGINAL_TEXT.encode()).hexdigest()
EDITED_TEXT = "Price: $12\n"
EDITED_HASH = hashlib.sha256(EDITED_TEXT.encode()).hexdigest()


async def _make_user(db_session, slug: str):
    user, _ = await db_session.get_or_create_user_by_provider_id(f"{slug}_user")
    org, _ = await db_session.get_or_create_organization_by_provider_id(
        f"{slug}_org", user.id
    )
    await db_session.update_user_selected_organization(user.id, org.id)
    return await db_session.get_user_by_id(user.id)


async def _make_document(
    db_session,
    user,
    *,
    filename: str = "menu.txt",
    retrieval_mode: str = "full_document",
    status: str = "completed",
    full_text: str | None = ORIGINAL_TEXT,
    file_hash: str = ORIGINAL_HASH,
):
    document = await db_session.create_document(
        organization_id=user.selected_organization_id,
        created_by=user.id,
        filename=filename,
        file_size_bytes=len(ORIGINAL_TEXT),
        file_hash=file_hash,
        mime_type="text/plain",
        custom_metadata={
            "s3_key": f"knowledge_base/{user.selected_organization_id}/x/{filename}"
        },
        retrieval_mode=retrieval_mode,
    )
    if full_text is not None:
        await db_session.update_document_full_text(document.id, full_text)
    await db_session.update_document_status(document.id, status)
    return await db_session.get_document_by_id(document.id)


@pytest.fixture
def storage():
    with patch("api.services.knowledge_base_content.storage_fs") as fs:
        fs.acreate_file_from_bytes = AsyncMock(return_value=True)

        async def _download(_key, local_path):
            async with aiofiles.open(local_path, "wb") as f:
                await f.write(ORIGINAL_TEXT.encode())
            return True

        fs.adownload_file = AsyncMock(side_effect=_download)
        yield fs


@pytest.fixture
def enqueue():
    with (
        patch(
            "api.services.knowledge_base_content.enqueue_job", new=AsyncMock()
        ) as mock,
        patch("api.routes.knowledge_base.capture_event"),
    ):
        yield mock


@pytest.mark.parametrize("status", ["processing", "failed"])
async def test_full_text_keeps_serving_while_reindexing_or_after_failure(
    db_session, status
):
    user = await _make_user(db_session, f"kb_live_{status}")
    document = await _make_document(db_session, user, status=status)

    docs = await db_session.get_full_text_documents(
        user.selected_organization_id, [document.document_uuid]
    )

    assert [d.full_text for d in docs] == [ORIGINAL_TEXT]


async def test_full_text_not_served_before_first_successful_processing(db_session):
    user = await _make_user(db_session, "kb_live_first")
    document = await _make_document(
        db_session, user, status="processing", full_text=None
    )

    docs = await db_session.get_full_text_documents(
        user.selected_organization_id, [document.document_uuid]
    )

    assert docs == []


async def test_save_overwrites_file_and_queues_reprocessing(
    test_client_factory, db_session, storage, enqueue
):
    user = await _make_user(db_session, "kb_save")
    document = await _make_document(
        db_session, user, filename="policy.md", retrieval_mode="chunked"
    )
    s3_key = document.custom_metadata["s3_key"]

    async with test_client_factory(user) as client:
        response = await client.put(
            f"/api/v1/knowledge-base/documents/{document.document_uuid}/content",
            json={"content": EDITED_TEXT, "expected_file_hash": ORIGINAL_HASH},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["processing_status"] == "pending"
    assert body["file_hash"] == EDITED_HASH
    storage.acreate_file_from_bytes.assert_awaited_once_with(
        s3_key, EDITED_TEXT.encode()
    )
    enqueue.assert_awaited_once()
    args = enqueue.await_args.args
    assert args[1:4] == (document.id, s3_key, user.selected_organization_id)
    assert args[-1] == "chunked"


async def test_save_with_stale_hash_is_rejected_without_touching_file(
    test_client_factory, db_session, storage, enqueue
):
    user = await _make_user(db_session, "kb_stale")
    document = await _make_document(db_session, user)

    async with test_client_factory(user) as client:
        response = await client.put(
            f"/api/v1/knowledge-base/documents/{document.document_uuid}/content",
            json={"content": EDITED_TEXT, "expected_file_hash": "not-the-hash"},
        )

    assert response.status_code == 409
    storage.acreate_file_from_bytes.assert_not_awaited()
    enqueue.assert_not_awaited()


async def test_save_while_processing_is_rejected(
    test_client_factory, db_session, storage, enqueue
):
    user = await _make_user(db_session, "kb_busy")
    document = await _make_document(db_session, user, status="processing")

    async with test_client_factory(user) as client:
        response = await client.put(
            f"/api/v1/knowledge-base/documents/{document.document_uuid}/content",
            json={"content": EDITED_TEXT, "expected_file_hash": ORIGINAL_HASH},
        )

    assert response.status_code == 409
    storage.acreate_file_from_bytes.assert_not_awaited()


async def test_only_one_of_two_concurrent_claims_wins(db_session):
    """Both saves pass the route's pre-checks; the atomic claim must still
    let exactly one through, or the loser could overwrite the winner's file."""
    user = await _make_user(db_session, "kb_race")
    document = await _make_document(db_session, user)
    claim = {
        "document_uuid": document.document_uuid,
        "organization_id": user.selected_organization_id,
        "expected_file_hash": ORIGINAL_HASH,
        "file_size_bytes": len(EDITED_TEXT),
    }

    first = await db_session.claim_document_for_content_update(
        **claim, file_hash=EDITED_HASH
    )
    second = await db_session.claim_document_for_content_update(
        **claim, file_hash="other"
    )

    assert first is not None and first.processing_status == "pending"
    assert second is None


async def test_unchanged_content_on_completed_document_is_a_no_op(
    test_client_factory, db_session, storage, enqueue
):
    user = await _make_user(db_session, "kb_noop")
    document = await _make_document(db_session, user)

    async with test_client_factory(user) as client:
        response = await client.put(
            f"/api/v1/knowledge-base/documents/{document.document_uuid}/content",
            json={"content": ORIGINAL_TEXT, "expected_file_hash": ORIGINAL_HASH},
        )

    assert response.status_code == 200
    assert response.json()["processing_status"] == "completed"
    storage.acreate_file_from_bytes.assert_not_awaited()
    enqueue.assert_not_awaited()


async def test_unchanged_content_on_failed_document_retries(
    test_client_factory, db_session, storage, enqueue
):
    user = await _make_user(db_session, "kb_retry")
    document = await _make_document(db_session, user, status="failed")

    async with test_client_factory(user) as client:
        response = await client.put(
            f"/api/v1/knowledge-base/documents/{document.document_uuid}/content",
            json={"content": ORIGINAL_TEXT, "expected_file_hash": ORIGINAL_HASH},
        )

    assert response.status_code == 200
    assert response.json()["processing_status"] == "pending"
    enqueue.assert_awaited_once()


@pytest.mark.parametrize("raises", [False, True], ids=["returns_false", "raises"])
async def test_failed_storage_write_restores_previous_state(
    test_client_factory, db_session, storage, enqueue, raises
):
    """Whether the backend reports the failure or raises, the claim must be
    released, or the document stays pending and every later save is refused."""
    user = await _make_user(db_session, f"kb_write_fail_{raises}")
    document = await _make_document(db_session, user)
    if raises:
        storage.acreate_file_from_bytes.side_effect = ConnectionError("unreachable")
    else:
        storage.acreate_file_from_bytes.return_value = False
    url = f"/api/v1/knowledge-base/documents/{document.document_uuid}/content"
    save = {"content": EDITED_TEXT, "expected_file_hash": ORIGINAL_HASH}

    async with test_client_factory(user) as client:
        failed = await client.put(url, json=save)
        enqueue.assert_not_awaited()
        # Copy the values out: the retry's claim refreshes the same ORM object.
        restored = await db_session.get_document_by_id(document.id)
        restored_state = (restored.processing_status, restored.file_hash)

        storage.acreate_file_from_bytes.side_effect = None
        storage.acreate_file_from_bytes.return_value = True
        retried = await client.put(url, json=save)

    assert failed.status_code == 500
    assert restored_state == ("completed", ORIGINAL_HASH)
    assert retried.status_code == 200, retried.text
    enqueue.assert_awaited_once()


async def test_non_text_documents_are_not_editable(
    test_client_factory, db_session, storage, enqueue
):
    user = await _make_user(db_session, "kb_pdf")
    document = await _make_document(db_session, user, filename="manual.pdf")
    url = f"/api/v1/knowledge-base/documents/{document.document_uuid}/content"

    async with test_client_factory(user) as client:
        get_response = await client.get(url)
        put_response = await client.put(
            url, json={"content": EDITED_TEXT, "expected_file_hash": ORIGINAL_HASH}
        )

    assert get_response.status_code == 400
    assert put_response.status_code == 400
    storage.acreate_file_from_bytes.assert_not_awaited()


async def test_get_content_returns_stored_text_and_version(
    test_client_factory, db_session, storage
):
    user = await _make_user(db_session, "kb_read")
    document = await _make_document(db_session, user, filename="faq.md")

    async with test_client_factory(user) as client:
        response = await client.get(
            f"/api/v1/knowledge-base/documents/{document.document_uuid}/content"
        )

    assert response.status_code == 200
    assert response.json()["content"] == ORIGINAL_TEXT
    assert response.json()["file_hash"] == ORIGINAL_HASH


async def test_other_organizations_cannot_read_or_edit(
    test_client_factory, db_session, storage, enqueue
):
    owner = await _make_user(db_session, "kb_owner")
    outsider = await _make_user(db_session, "kb_outsider")
    document = await _make_document(db_session, owner)
    url = f"/api/v1/knowledge-base/documents/{document.document_uuid}/content"

    async with test_client_factory(outsider) as client:
        get_response = await client.get(url)
        put_response = await client.put(
            url, json={"content": EDITED_TEXT, "expected_file_hash": ORIGINAL_HASH}
        )

    assert get_response.status_code == 404
    assert put_response.status_code == 404
    storage.acreate_file_from_bytes.assert_not_awaited()


async def test_list_reports_live_content_during_reindex(
    test_client_factory, db_session
):
    user = await _make_user(db_session, "kb_list_live")
    reindexing = await _make_document(db_session, user, status="processing")
    first_upload = await _make_document(
        db_session,
        user,
        filename="new.txt",
        status="processing",
        full_text=None,
        file_hash="other",
    )
    emptied = await _make_document(
        db_session, user, filename="empty.txt", full_text="", file_hash="empty"
    )

    async with test_client_factory(user) as client:
        response = await client.get("/api/v1/knowledge-base/documents")

    live = {
        d["document_uuid"]: d["has_live_content"] for d in response.json()["documents"]
    }
    assert live == {
        reindexing.document_uuid: True,
        first_upload.document_uuid: False,
        emptied.document_uuid: False,
    }
