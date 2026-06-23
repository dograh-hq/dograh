"""Outbound KYC gate: configured/unconfigured, no-client, complete/incomplete, fail-open."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.services.voicelink_kyc import VoiceLinkKycError
from api.services.voicelink_kyc import gating
from api.services.voicelink_kyc.gating import assert_org_kyc_complete, is_org_kyc_complete


def _client(*, configured=True, status=None, error=None):
    c = AsyncMock()
    c.is_configured = configured
    if error is not None:
        c.get_status = AsyncMock(side_effect=error)
    else:
        c.get_status = AsyncMock(return_value=status)
    return c


def _patches(client, client_id):
    return (
        patch.object(gating, "get_kyc_client", return_value=client),
        patch.object(gating, "resolve_org_voicelink_client_id",
                     new=AsyncMock(return_value=(client_id, True))),
    )


async def _run_is_complete(client, client_id):
    p1, p2 = _patches(client, client_id)
    with p1, p2:
        return await is_org_kyc_complete(1)


async def test_kyc_not_configured_allows():
    assert await _run_is_complete(_client(configured=False), None) is True


async def test_no_voicelink_client_allows():
    assert await _run_is_complete(_client(status={"data": {"is_complete": False}}), None) is True


async def test_complete_allows():
    assert await _run_is_complete(_client(status={"data": {"is_complete": True}}), "cid") is True


async def test_incomplete_blocks():
    assert await _run_is_complete(_client(status={"data": {"is_complete": False}}), "cid") is False


async def test_api_error_fails_open():
    assert await _run_is_complete(_client(error=VoiceLinkKycError("boom")), "cid") is True


async def test_assert_raises_403_when_incomplete():
    p1, p2 = _patches(_client(status={"data": {"is_complete": False}}), "cid")
    with p1, p2, pytest.raises(HTTPException) as exc:
        await assert_org_kyc_complete(1)
    assert exc.value.status_code == 403


async def test_assert_noop_when_complete():
    p1, p2 = _patches(_client(status={"data": {"is_complete": True}}), "cid")
    with p1, p2:
        await assert_org_kyc_complete(1)  # must not raise
