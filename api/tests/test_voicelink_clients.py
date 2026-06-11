"""Tests for the VoiceLink reseller client-provisioning service.

The VoiceLink HTTP layer is mocked at the ``_send_request`` seam (same
pattern as the KYC tests — no network calls), and the DB layer is mocked
at the ``db_client`` module attribute used by the service.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.services.voicelink_clients import (
    VoiceLinkClientError,
    VoiceLinkClientsClient,
    derive_username,
    provision_voicelink_client,
    provision_voicelink_client_for_signup,
    split_signup_name,
)

API_BASE = "https://app.voicelink.co.in/api"


def _client(**overrides) -> VoiceLinkClientsClient:
    kwargs = {
        "api_base": API_BASE,
        "username": "reseller-user",
        "password": "placeholder-password",
    }
    kwargs.update(overrides)
    return VoiceLinkClientsClient(**kwargs)


def _ok(data=None, message="ok"):
    return (201, {"status": True, "message": message, "data": data or {}})


# ======== USERNAME / NAME DERIVATION ========


def test_derive_username_uses_email_local_part_and_org_suffix():
    assert derive_username("jane.doe@example.test", 11) == "jane.doe.11"


def test_derive_username_strips_unsafe_characters():
    assert derive_username("jane+spam!{}@example.test", 7) == "janespam.7"


def test_derive_username_falls_back_when_local_part_is_unusable():
    assert derive_username("++@example.test", 3) == "client.3"


def test_split_signup_name_splits_first_and_rest():
    assert split_signup_name("Jane Mary Smith", 5) == ("Jane", "Mary Smith")


def test_split_signup_name_falls_back_for_single_token_and_missing():
    assert split_signup_name("Jane", 5) == ("Jane", "Org5")
    assert split_signup_name(None, 5) == ("Client", "Org5")
    assert split_signup_name("   ", 5) == ("Client", "Org5")


# ======== CLIENT: CREATE REQUEST SHAPE ========


@pytest.mark.asyncio
async def test_provision_posts_the_expected_create_payload(monkeypatch):
    monkeypatch.setenv("VOICELINK_DEFAULT_CHANNELS", "3")
    monkeypatch.setenv("VOICELINK_DEFAULT_INBOUND_RATE", "0.5")
    monkeypatch.setenv("VOICELINK_DEFAULT_OUTBOUND_RATE", "0.7")

    client = _client()
    client._access_token = "tok"

    with (
        patch.object(
            client,
            "_send_request",
            new_callable=AsyncMock,
            return_value=_ok({"client_id": 474}),
        ) as send,
        patch("api.services.voicelink_clients.service.db_client") as db,
    ):
        db.update_organization_voicelink = AsyncMock()
        await provision_voicelink_client(
            11,
            email="jane.doe@example.test",
            password="placeholder-pass",
            name="Jane Doe",
            client=client,
        )

    send.assert_awaited_once()
    method, url, payload, token = send.await_args.args[:4]
    assert method == "POST"
    assert url == f"{API_BASE}/v1/reseller/client/create"
    assert token == "tok"
    assert payload == {
        "first_name": "Jane",
        "last_name": "Doe",
        "username": "jane.doe.11",
        "email": "jane.doe@example.test",
        "password": "placeholder-pass",
        "channel_count": 3,
        "negative_threshold": 0,
        "pulse_seconds": 60,
        "inbound_rate": 0.5,
        "outbound_rate": 0.7,
    }


@pytest.mark.asyncio
async def test_provision_defaults_channels_and_rates(monkeypatch):
    monkeypatch.delenv("VOICELINK_DEFAULT_CHANNELS", raising=False)
    monkeypatch.delenv("VOICELINK_DEFAULT_INBOUND_RATE", raising=False)
    monkeypatch.delenv("VOICELINK_DEFAULT_OUTBOUND_RATE", raising=False)

    client = _client()
    client._access_token = "tok"

    with (
        patch.object(
            client, "_send_request", new_callable=AsyncMock, return_value=_ok()
        ) as send,
        patch("api.services.voicelink_clients.service.db_client") as db,
    ):
        db.update_organization_voicelink = AsyncMock()
        await provision_voicelink_client(
            11,
            email="jane@example.test",
            password="placeholder-pass",
            client=client,
        )

    payload = send.await_args.args[2]
    assert payload["channel_count"] == 1
    assert payload["inbound_rate"] == 1.0
    assert payload["outbound_rate"] == 1.0


@pytest.mark.asyncio
async def test_provision_uses_supplied_username_for_retries():
    client = _client()
    client._access_token = "tok"

    with (
        patch.object(
            client, "_send_request", new_callable=AsyncMock, return_value=_ok()
        ) as send,
        patch("api.services.voicelink_clients.service.db_client") as db,
    ):
        db.update_organization_voicelink = AsyncMock()
        await provision_voicelink_client(
            11,
            email="jane@example.test",
            password="placeholder-pass",
            username="stored.username.11",
            client=client,
        )

    assert send.await_args.args[2]["username"] == "stored.username.11"


# ======== OUTCOME PERSISTENCE ========


@pytest.mark.asyncio
async def test_success_stores_provisioned_status_and_client_id():
    client = _client()
    client._access_token = "tok"

    with (
        patch.object(
            client,
            "_send_request",
            new_callable=AsyncMock,
            return_value=_ok({"client_id": 474}),
        ),
        patch("api.services.voicelink_clients.service.db_client") as db,
    ):
        db.update_organization_voicelink = AsyncMock()
        result = await provision_voicelink_client(
            11,
            email="jane@example.test",
            password="placeholder-pass",
            client=client,
        )

    assert result["status"] == "provisioned"
    assert result["client_id"] == "474"
    db.update_organization_voicelink.assert_awaited_once_with(
        11,
        client_id="474",
        username="jane.11",
        status="provisioned",
        error=None,
    )


@pytest.mark.asyncio
async def test_422_no_channels_stores_pending_with_error():
    client = _client()
    client._access_token = "tok"

    with (
        patch.object(
            client,
            "_send_request",
            new_callable=AsyncMock,
            return_value=(
                422,
                {"status": False, "message": "No channels available"},
            ),
        ),
        patch("api.services.voicelink_clients.service.db_client") as db,
    ):
        db.update_organization_voicelink = AsyncMock()
        result = await provision_voicelink_client(
            11,
            email="jane@example.test",
            password="placeholder-pass",
            client=client,
        )

    assert result["status"] == "pending"
    assert result["client_id"] is None
    assert "No channels available" in result["error"]

    update_kwargs = db.update_organization_voicelink.await_args.kwargs
    assert update_kwargs["status"] == "pending"
    assert update_kwargs["error"] == "No channels available"
    assert update_kwargs["username"] == "jane.11"
    # Existing client_id is preserved — a failed retry must not wipe it.
    assert "client_id" not in update_kwargs


@pytest.mark.asyncio
async def test_create_client_raises_with_upstream_status_code():
    client = _client()
    client._access_token = "tok"

    with patch.object(
        client,
        "_send_request",
        new_callable=AsyncMock,
        return_value=(422, {"status": False, "message": "No channels available"}),
    ):
        with pytest.raises(VoiceLinkClientError, match="No channels available") as exc:
            await client.create_client({"username": "u"})

    assert exc.value.status_code == 422


# ======== SIGNUP HOOK ========


@pytest.mark.asyncio
async def test_signup_hook_skips_admin_emails():
    with (
        patch(
            "api.services.voicelink_clients.service.is_admin_email",
            return_value=True,
        ),
        patch(
            "api.services.voicelink_clients.service.provision_voicelink_client",
            new_callable=AsyncMock,
        ) as provision,
    ):
        await provision_voicelink_client_for_signup(
            organization_id=11,
            email="owner@example.test",
            password="placeholder-pass",
        )

    provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_signup_hook_skips_when_reseller_creds_unset():
    with (
        patch(
            "api.services.voicelink_clients.service.get_voicelink_clients_client",
            return_value=_client(username="", password=""),
        ),
        patch(
            "api.services.voicelink_clients.service.provision_voicelink_client",
            new_callable=AsyncMock,
        ) as provision,
    ):
        await provision_voicelink_client_for_signup(
            organization_id=11,
            email="user@example.test",
            password="placeholder-pass",
        )

    provision.assert_not_awaited()


@pytest.mark.asyncio
async def test_signup_hook_never_raises_and_records_pending():
    with (
        patch(
            "api.services.voicelink_clients.service.get_voicelink_clients_client",
            return_value=_client(),
        ),
        patch(
            "api.services.voicelink_clients.service.provision_voicelink_client",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch("api.services.voicelink_clients.service.db_client") as db,
    ):
        db.update_organization_voicelink = AsyncMock()
        # Must not raise — signup never fails on VoiceLink errors.
        await provision_voicelink_client_for_signup(
            organization_id=11,
            email="user@example.test",
            password="placeholder-pass",
        )

    update_kwargs = db.update_organization_voicelink.await_args.kwargs
    assert update_kwargs["status"] == "pending"
