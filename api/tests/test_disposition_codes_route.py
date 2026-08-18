from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pipecat.utils.enums import EndTaskReason

from api.routes.organization import router
from api.services.auth.depends import get_user_with_selected_organization
from api.services.workflow.disposition_codes import (
    END_TASK_REASON_DISPOSITION_CODES,
    SYSTEM_DISPOSITION_CODES,
)


def _make_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_user_with_selected_organization] = lambda: (
        SimpleNamespace(id=1, selected_organization_id=11)
    )
    return app


def _get_catalog(org_codes: list[str]) -> dict[str, list[str]]:
    client = TestClient(_make_test_app())
    with patch("api.routes.organization.db_client") as mock_db:
        mock_db.get_organization_disposition_codes = AsyncMock(return_value=org_codes)
        response = client.get("/organizations/disposition-codes")

    assert response.status_code == 200
    return response.json()


def _get_codes(org_codes: list[str]) -> list[str]:
    return _get_catalog(org_codes)["codes"]


def test_catalog_covers_every_disposition_the_pipeline_can_record():
    """The dropdown is built from this response, so a disposition missing here
    is a disposition nobody can filter on."""
    codes = _get_codes([])

    assert codes == list(SYSTEM_DISPOSITION_CODES)
    # Regression: call_transferred shipped in the enum but never reached the
    # frontend's hardcoded list, leaving transferred calls unfilterable.
    assert "call_transferred" in codes


def test_catalog_exposes_end_task_reason_codes_for_superadmin_filters():
    catalog = _get_catalog(["XFER"])

    assert catalog["end_task_reason_codes"] == [
        reason.value for reason in EndTaskReason
    ]
    assert catalog["end_task_reason_codes"] == list(END_TASK_REASON_DISPOSITION_CODES)
    assert "XFER" not in catalog["end_task_reason_codes"]


def test_catalog_includes_telephony_statuses_for_calls_that_never_connected():
    """status_processor writes TelephonyCallStatus values straight into
    mapped_call_disposition, so they are filterable dispositions too."""
    codes = _get_codes([])

    for status in ("no-answer", "busy", "failed", "canceled", "error"):
        assert status in codes


def test_org_custom_codes_are_appended_without_duplicating_builtins():
    codes = _get_codes(["XFER", "DNC", "user_hangup"])

    assert codes[: len(SYSTEM_DISPOSITION_CODES)] == list(SYSTEM_DISPOSITION_CODES)
    assert codes[len(SYSTEM_DISPOSITION_CODES) :] == ["DNC", "XFER"]
    assert codes.count("user_hangup") == 1
