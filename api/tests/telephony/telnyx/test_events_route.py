"""Regression test for issue #779.

POST /telnyx/events/{workflow_run_id} must return a clean 400 when the
run's telephony config fails to resolve, not an unhandled 500 — and the
detail must distinguish "no config at all" from "a specific config id
was requested but couldn't be resolved", matching the initiate-call
route's pattern.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.services.telephony.providers.telnyx.routes import handle_telnyx_events


def _fake_request(body: dict) -> MagicMock:
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode("utf-8"))
    request.headers = {}
    return request


def _fake_db(workflow_run, workflow) -> MagicMock:
    db = MagicMock()
    db.get_workflow_run_by_id = AsyncMock(return_value=workflow_run)
    db.get_workflow_by_id = AsyncMock(return_value=workflow)
    return db


@pytest.mark.asyncio
async def test_events_route_returns_400_when_org_has_no_default_config():
    """No telephony_configuration_id stamped on the run, and no org
    default exists either -> telephony_not_configured.
    """
    workflow_run = SimpleNamespace(workflow_id=1, initial_context={})
    workflow = SimpleNamespace(organization_id=157)
    db = _fake_db(workflow_run, workflow)
    request = _fake_request({"data": {"event_type": "call.initiated"}})

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client", db),
        patch(
            "api.services.telephony.providers.telnyx.routes."
            "get_telephony_provider_for_run",
            AsyncMock(
                side_effect=ValueError(
                    "No default telephony configuration found for "
                    "organization 157"
                )
            ),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await handle_telnyx_events(request, workflow_run_id=524)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "telephony_not_configured"


@pytest.mark.asyncio
async def test_events_route_returns_400_when_stamped_config_cannot_be_resolved():
    """A telephony_configuration_id IS stamped on the run's
    initial_context, but it fails to resolve (deleted, inactive, wrong
    org, etc) -> telephony_configuration_not_found, not the generic
    telephony_not_configured.
    """
    workflow_run = SimpleNamespace(
        workflow_id=1,
        initial_context={"telephony_configuration_id": 42},
    )
    workflow = SimpleNamespace(organization_id=157)
    db = _fake_db(workflow_run, workflow)
    request = _fake_request({"data": {"event_type": "call.initiated"}})

    with (
        patch("api.services.telephony.providers.telnyx.routes.db_client", db),
        patch(
            "api.services.telephony.providers.telnyx.routes."
            "get_telephony_provider_for_run",
            AsyncMock(
                side_effect=ValueError(
                    "Telephony configuration 42 not found for "
                    "organization 157"
                )
            ),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await handle_telnyx_events(request, workflow_run_id=524)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "telephony_configuration_not_found"
