"""Tests for the VoiceLink reseller KYC service and routes.

The VoiceLink HTTP layer is mocked at the ``_send_request`` / ``_login``
seams (same pattern as the telephony provider tests) — no network calls.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.kyc import (
    get_kyc_status,
    kyc_final_submit,
    kyc_step_1_register_details,
    kyc_step_2_pan_verify,
    kyc_step_3_aadhaar_init,
    kyc_step_4_gst_verify,
)
from api.schemas.kyc import (
    KycStep1Request,
    KycStep2Request,
    KycStep3Request,
    KycStep4Request,
)
from api.services.voicelink_kyc import VoiceLinkKycClient, VoiceLinkKycError

API_BASE = "https://app.voicelink.co.in/api"


def _client(**overrides) -> VoiceLinkKycClient:
    kwargs = {
        "api_base": API_BASE,
        "username": "reseller-user",
        "password": "placeholder-password",
    }
    kwargs.update(overrides)
    return VoiceLinkKycClient(**kwargs)


def _ok(data=None, message="ok"):
    return (200, {"status": True, "message": message, "data": data or {}})


def _user(org_id=11):
    return SimpleNamespace(selected_organization_id=org_id)


# ======== CLIENT: REQUEST SHAPES ========


@pytest.mark.asyncio
async def test_get_status_forwards_client_id_as_query_param():
    client = _client()
    client._access_token = "tok"

    with patch.object(
        client, "_send_request", new_callable=AsyncMock, return_value=_ok()
    ) as send:
        await client.get_status("474")

    send.assert_awaited_once()
    method, url, payload, token, params = send.await_args.args
    assert method == "GET"
    assert url == f"{API_BASE}/v1/reseller/kyc/status"
    assert payload is None
    assert token == "tok"
    assert params == {"client_id": "474"}


@pytest.mark.asyncio
async def test_get_status_without_client_id_sends_no_params():
    client = _client()
    client._access_token = "tok"

    with patch.object(
        client, "_send_request", new_callable=AsyncMock, return_value=_ok()
    ) as send:
        await client.get_status(None)

    params = send.await_args.args[4]
    assert params is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call,path,expected_payload",
    [
        (
            lambda c: c.step1_register_details(
                {
                    "term_and_condition": True,
                    "account_type": "business",
                    "business_name": "Auto4You",
                    "full_name": "Test User",
                    "email": "user@example.test",
                    "phone": "9999999999",
                    "billing_address": "1 Test Lane",
                },
                "474",
            ),
            "/v1/reseller/kyc/step-1-register-details",
            {
                "client_id": "474",
                "term_and_condition": True,
                "account_type": "business",
                "business_name": "Auto4You",
                "full_name": "Test User",
                "email": "user@example.test",
                "phone": "9999999999",
                "billing_address": "1 Test Lane",
            },
        ),
        (
            lambda c: c.step2_pan_verify(
                {"pan_holder_name": "Test User", "pan_number": "ABCDE1234F"}, "474"
            ),
            "/v1/reseller/kyc/step-2-pan-verify",
            {
                "client_id": "474",
                "pan_holder_name": "Test User",
                "pan_number": "ABCDE1234F",
            },
        ),
        (
            lambda c: c.step3_aadhaar_init("https://app.test/kyc", "474"),
            "/v1/reseller/kyc/step-3-aadhaar-init",
            {"client_id": "474", "redirect_url": "https://app.test/kyc"},
        ),
        (
            lambda c: c.step4_gst_verify({"gst_number": "22AAAAA0000A1Z5"}, "474"),
            "/v1/reseller/kyc/step-4-gst-verify",
            {"client_id": "474", "gst_number": "22AAAAA0000A1Z5"},
        ),
        (
            lambda c: c.final_submit("474"),
            "/v1/reseller/kyc/final-submit",
            {"client_id": "474"},
        ),
    ],
)
async def test_steps_post_the_right_body(call, path, expected_payload):
    client = _client()
    client._access_token = "tok"

    with patch.object(
        client, "_send_request", new_callable=AsyncMock, return_value=_ok()
    ) as send:
        await call(client)

    method, url, payload = send.await_args.args[:3]
    assert method == "POST"
    assert url == f"{API_BASE}{path}"
    assert payload == expected_payload


@pytest.mark.asyncio
async def test_steps_omit_client_id_when_not_resolved():
    client = _client()
    client._access_token = "tok"

    with patch.object(
        client, "_send_request", new_callable=AsyncMock, return_value=_ok()
    ) as send:
        await client.step2_pan_verify(
            {"pan_holder_name": "Test User", "pan_number": "ABCDE1234F"}, None
        )

    payload = send.await_args.args[2]
    assert "client_id" not in payload


# ======== CLIENT: AUTH ========


@pytest.mark.asyncio
async def test_401_triggers_relogin_and_single_retry():
    client = _client()
    client._access_token = "stale-token"

    with (
        patch.object(
            client,
            "_send_request",
            new_callable=AsyncMock,
            side_effect=[(401, {"message": "expired"}), _ok({"is_complete": False})],
        ) as send,
        patch.object(
            client, "_login", new_callable=AsyncMock, return_value="fresh-token"
        ) as login,
    ):
        envelope = await client.get_status("474")

    assert envelope["data"] == {"is_complete": False}
    login.assert_awaited_once()
    assert send.await_count == 2
    # First call used the stale token, retry used the fresh one.
    assert send.await_args_list[0].args[3] == "stale-token"
    assert send.await_args_list[1].args[3] == "fresh-token"


@pytest.mark.asyncio
async def test_envelope_status_false_raises_kyc_error():
    client = _client()
    client._access_token = "tok"

    with patch.object(
        client,
        "_send_request",
        new_callable=AsyncMock,
        return_value=(200, {"status": False, "message": "PAN mismatch"}),
    ):
        with pytest.raises(VoiceLinkKycError, match="PAN mismatch"):
            await client.step2_pan_verify(
                {"pan_holder_name": "X", "pan_number": "Y"}, None
            )


@pytest.mark.asyncio
async def test_missing_credentials_make_client_unconfigured():
    client = _client(username="", password="")
    assert not client.is_configured
    with pytest.raises(VoiceLinkKycError, match="not configured"):
        await client._login()


# ======== ROUTES ========


@pytest.mark.asyncio
async def test_status_route_returns_disabled_when_creds_unset():
    with patch(
        "api.routes.kyc.get_kyc_client", return_value=_client(username="", password="")
    ):
        response = await get_kyc_status(user=_user())

    assert response.enabled is False


@pytest.mark.asyncio
async def test_step_routes_return_503_when_creds_unset():
    with patch(
        "api.routes.kyc.get_kyc_client", return_value=_client(username="", password="")
    ):
        with pytest.raises(HTTPException) as exc:
            await kyc_step_2_pan_verify(
                KycStep2Request(pan_holder_name="X", pan_number="Y"), user=_user()
            )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_status_route_forwards_resolved_client_id():
    client = _client()
    client.get_status = AsyncMock(
        return_value={
            "status": True,
            "data": {
                "kyc_status": "in_progress",
                "pan_verified": True,
                "aadhaar_verified": False,
                "gst_verified": False,
                "is_complete": False,
                "current_step": 3,
                "account_type": "business",
            },
        }
    )

    with (
        patch("api.routes.kyc.get_kyc_client", return_value=client),
        patch(
            "api.routes.kyc.resolve_org_voicelink_client_id",
            new_callable=AsyncMock,
            return_value=("474", True),
        ) as resolve,
    ):
        response = await get_kyc_status(user=_user(org_id=11))

    resolve.assert_awaited_once_with(11)
    client.get_status.assert_awaited_once_with("474")
    assert response.enabled is True
    assert response.client_id_configured is True
    assert response.has_voicelink_config is True
    assert response.pan_verified is True
    assert response.current_step == 3
    assert response.account_type == "business"


@pytest.mark.asyncio
async def test_status_route_without_client_id_shows_hint_flags():
    client = _client()
    client.get_status = AsyncMock(return_value={"status": True, "data": {}})

    with (
        patch("api.routes.kyc.get_kyc_client", return_value=client),
        patch(
            "api.routes.kyc.resolve_org_voicelink_client_id",
            new_callable=AsyncMock,
            return_value=(None, True),
        ),
    ):
        response = await get_kyc_status(user=_user())

    client.get_status.assert_awaited_once_with(None)
    assert response.client_id_configured is False
    assert response.has_voicelink_config is True


@pytest.mark.asyncio
async def test_voicelink_failure_maps_to_502():
    client = _client()
    client.step3_aadhaar_init = AsyncMock(
        side_effect=VoiceLinkKycError("VoiceLink KYC request failed: HTTP 500")
    )

    with (
        patch("api.routes.kyc.get_kyc_client", return_value=client),
        patch(
            "api.routes.kyc.resolve_org_voicelink_client_id",
            new_callable=AsyncMock,
            return_value=(None, False),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await kyc_step_3_aadhaar_init(KycStep3Request(), user=_user())

    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_step_routes_pass_resolved_client_id_and_body():
    client = _client()
    client.step1_register_details = AsyncMock(
        return_value={"status": True, "message": "saved", "data": {"step": 1}}
    )
    client.step4_gst_verify = AsyncMock(
        return_value={"status": True, "message": "verified", "data": {}}
    )
    client.final_submit = AsyncMock(
        return_value={"status": True, "message": "submitted", "data": {}}
    )

    with (
        patch("api.routes.kyc.get_kyc_client", return_value=client),
        patch(
            "api.routes.kyc.resolve_org_voicelink_client_id",
            new_callable=AsyncMock,
            return_value=("474", True),
        ),
    ):
        step1 = await kyc_step_1_register_details(
            KycStep1Request(
                term_and_condition=True,
                account_type="individual",
                full_name="Test User",
                email="user@example.test",
                phone="9999999999",
                billing_address="1 Test Lane",
            ),
            user=_user(),
        )
        await kyc_step_4_gst_verify(
            KycStep4Request(gst_number="22AAAAA0000A1Z5"), user=_user()
        )
        await kyc_final_submit(user=_user())

    body, client_id = client.step1_register_details.await_args.args
    assert client_id == "474"
    assert body["account_type"] == "individual"
    # exclude_none — no business_name key for individual accounts
    assert "business_name" not in body
    assert step1.message == "saved"
    assert step1.data == {"step": 1}

    client.step4_gst_verify.assert_awaited_once_with(
        {"gst_number": "22AAAAA0000A1Z5"}, "474"
    )
    client.final_submit.assert_awaited_once_with("474")


def test_step1_requires_business_name_for_business_accounts():
    with pytest.raises(ValueError, match="business_name"):
        KycStep1Request(
            term_and_condition=True,
            account_type="business",
            full_name="Test User",
            email="user@example.test",
            phone="9999999999",
            billing_address="1 Test Lane",
        )


# ======== CLIENT_ID RESOLUTION ========


@pytest.mark.asyncio
async def test_resolve_client_id_prefers_default_outbound_config():
    from api.services.voicelink_kyc import resolve_org_voicelink_client_id

    configs = [
        SimpleNamespace(
            is_default_outbound=False, credentials={"client_id": "111"}
        ),
        SimpleNamespace(
            is_default_outbound=True, credentials={"client_id": "474"}
        ),
    ]

    with patch("api.db.db_client") as db_client:
        db_client.list_telephony_configurations_by_provider = AsyncMock(
            return_value=configs
        )
        client_id, has_config = await resolve_org_voicelink_client_id(11)

    db_client.list_telephony_configurations_by_provider.assert_awaited_once_with(
        11, "voicelink"
    )
    assert client_id == "474"
    assert has_config is True


@pytest.mark.asyncio
async def test_resolve_client_id_handles_config_without_client_id():
    from api.services.voicelink_kyc import resolve_org_voicelink_client_id

    configs = [SimpleNamespace(is_default_outbound=True, credentials={})]

    with patch("api.db.db_client") as db_client:
        db_client.list_telephony_configurations_by_provider = AsyncMock(
            return_value=configs
        )
        client_id, has_config = await resolve_org_voicelink_client_id(11)

    assert client_id is None
    assert has_config is True


@pytest.mark.asyncio
async def test_resolve_client_id_handles_no_voicelink_config():
    from api.services.voicelink_kyc import resolve_org_voicelink_client_id

    with patch("api.db.db_client") as db_client:
        db_client.list_telephony_configurations_by_provider = AsyncMock(
            return_value=[]
        )
        client_id, has_config = await resolve_org_voicelink_client_id(11)

    assert client_id is None
    assert has_config is False
