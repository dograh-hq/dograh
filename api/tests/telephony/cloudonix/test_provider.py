from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.routes.organization import (
    _run_preprocess_hook,
    create_telephony_configuration,
)
from api.schemas.telephony_config import (
    TelephonyConfigurationCreateRequest,
    TelephonyConfigurationDetail,
)
from api.services.telephony.factory import get_sip_connectivity_details
from api.services.telephony.providers.cloudonix import (
    CLOUDONIX_API_BASE_URL,
    SPEC,
    CloudonixProvider,
    _ensure_outbound_trunk,
    _preprocess_credentials_on_save,
    _redact_outbound_trunk_payload,
)
from api.services.telephony.providers.cloudonix.config import (
    CloudonixConfigurationRequest,
)
from api.services.telephony.providers.twilio.provider import TwilioProvider

DOMAIN_UUID = "24f9423e-6902-4a48-b1f2-d5953106d4ae"
TRUNK_UUID = "c8d11212-8ec0-49b8-b4e2-083de6e4b74a"


class _FakeResponse:
    def __init__(self, status, payload, response_text=""):
        self.status = status
        self._payload = payload
        self._response_text = response_text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def text(self):
        return self._response_text

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, response):
        self.response = response
        self.get_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, endpoint, **kwargs):
        self.get_calls.append((endpoint, kwargs))
        return self.response

    def post(self, *args, **kwargs):
        raise AssertionError("an existing application should not be recreated")


class _TrunkSession:
    def __init__(self, *, get_responses, post_response=None, put_response=None):
        self.get_responses = list(get_responses)
        self.post_response = post_response
        self.put_response = put_response
        self.get_calls = []
        self.post_calls = []
        self.put_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def get(self, endpoint, **kwargs):
        self.get_calls.append((endpoint, kwargs))
        if not self.get_responses:
            raise AssertionError("unexpected Cloudonix GET request")
        return self.get_responses.pop(0)

    def post(self, endpoint, **kwargs):
        self.post_calls.append((endpoint, kwargs))
        if self.post_response is None:
            raise AssertionError("unexpected Cloudonix POST request")
        return self.post_response

    def put(self, endpoint, **kwargs):
        self.put_calls.append((endpoint, kwargs))
        if self.put_response is None:
            raise AssertionError("unexpected Cloudonix PUT request")
        return self.put_response


def test_sip_connectivity_details_include_regional_transports_and_origin_ips():
    details = get_sip_connectivity_details(
        "cloudonix",
        {
            "bearer_token": "secret-token",
            "domain_id": "friendly-name.cloudonix.net",
            "domain_uuid": DOMAIN_UUID,
        },
    )

    assert details is not None
    assert asdict(details) == expected_details(DOMAIN_UUID)


def test_sip_connectivity_details_are_absent_without_a_domain_uuid():
    assert (
        get_sip_connectivity_details(
            "cloudonix", {"domain_id": "friendly-name.cloudonix.net"}
        )
        is None
    )


def test_provider_without_sip_capability_is_not_instantiated():
    with patch.object(
        TwilioProvider,
        "__init__",
        side_effect=AssertionError("provider should not be instantiated"),
    ):
        details = get_sip_connectivity_details(
            "twilio", {"account_sid": "AC123", "auth_token": "secret"}
        )

    assert details is None


def test_sip_connectivity_details_serialize_on_configuration_detail():
    details = get_sip_connectivity_details(
        "cloudonix",
        {
            "domain_id": "test-domain.cloudonix.net",
            "domain_uuid": DOMAIN_UUID,
        },
    )
    now = datetime.now(UTC)

    response = TelephonyConfigurationDetail(
        id=1,
        name="Cloudonix",
        provider="cloudonix",
        is_default_outbound=False,
        credentials={
            "domain_id": "test-domain.cloudonix.net",
            "domain_uuid": DOMAIN_UUID,
        },
        sip_connectivity=details,
        created_at=now,
        updated_at=now,
    )

    assert response.model_dump(mode="json")["sip_connectivity"] == expected_details(
        DOMAIN_UUID
    )


@pytest.mark.asyncio
async def test_preprocess_fetches_domain_uuid_when_it_is_missing():
    session = _FakeSession(_FakeResponse(200, {"uuid": f"  {DOMAIN_UUID}  "}))
    credentials = {
        "bearer_token": "secret-token",
        "domain_id": "friendly-name.cloudonix.net",
        "application_name": "existing-app",
    }

    with patch(
        "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await _preprocess_credentials_on_save(credentials)

    expected_endpoint = (
        f"{CLOUDONIX_API_BASE_URL}/customers/self/domains/{credentials['domain_id']}"
    )
    assert result == {**credentials, "domain_uuid": DOMAIN_UUID}
    assert session.get_calls == [
        (
            expected_endpoint,
            {
                "headers": {
                    "Authorization": "Bearer secret-token",
                    "Accept": "application/json",
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_preprocess_reuses_existing_domain_uuid_without_fetching():
    credentials = {
        "bearer_token": "secret-token",
        "domain_id": "friendly-name.cloudonix.net",
        "domain_uuid": DOMAIN_UUID,
        "application_name": "existing-app",
    }

    with patch(
        "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
        side_effect=AssertionError("domainGet should only run once"),
    ):
        result = await _preprocess_credentials_on_save(credentials)

    assert result == credentials


@pytest.mark.asyncio
async def test_managed_preprocess_creates_app_and_sets_domain_default():
    app_uuid = "33333333-3333-4333-8333-333333333333"
    session = _TrunkSession(
        get_responses=[_FakeResponse(200, [])],
        post_response=_FakeResponse(
            201,
            {
                "id": 5278,
                "name": "dograh-111111111111411181111111",
                "uuid": app_uuid,
            },
        ),
        put_response=_FakeResponse(200, {"defaultApplication": 5278}),
    )
    credentials = {
        "bearer_token": "domain-bearer",
        "domain_id": "oss-dograh-123.cloudonix.net",
        "domain_uuid": DOMAIN_UUID,
        "managed_by": "dograh-mps",
        "provisioning_id": "11111111-1111-4111-8111-111111111111",
    }

    with (
        patch(
            "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.cloudonix.get_backend_endpoints",
            AsyncMock(
                return_value=("https://api.example.com", "wss://api.example.com")
            ),
        ),
    ):
        result = await _preprocess_credentials_on_save(credentials)

    collection = (
        f"{CLOUDONIX_API_BASE_URL}/customers/self/domains/"
        "oss-dograh-123.cloudonix.net/applications"
    )
    assert session.get_calls[0][0] == collection
    assert session.post_calls[0][0] == collection
    assert session.post_calls[0][1]["json"] == {
        "name": "dograh-111111111111411181111111",
        "type": "cxml",
        "url": "https://api.example.com/api/v1/telephony/inbound/run",
        "method": "POST",
    }
    assert session.put_calls[0][0] == (
        f"{CLOUDONIX_API_BASE_URL}/customers/self/domains/oss-dograh-123.cloudonix.net"
    )
    assert session.put_calls[0][1]["json"] == {"defaultApplication": 5278}
    assert result["application_id"] == 5278
    assert result["application_uuid"] == app_uuid


@pytest.mark.asyncio
async def test_managed_preprocess_recovers_existing_app_without_duplicate_create():
    app = {
        "id": 5278,
        "name": "dograh-111111111111411181111111",
        "uuid": "33333333-3333-4333-8333-333333333333",
    }
    session = _TrunkSession(
        get_responses=[_FakeResponse(200, [app])],
        put_response=_FakeResponse(200, {"defaultApplication": 5278}),
    )
    credentials = {
        "bearer_token": "domain-bearer",
        "domain_id": "oss-dograh-123.cloudonix.net",
        "domain_uuid": DOMAIN_UUID,
        "managed_by": "dograh-mps",
        "provisioning_id": "11111111-1111-4111-8111-111111111111",
    }

    with (
        patch(
            "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.cloudonix.get_backend_endpoints",
            AsyncMock(
                return_value=("https://api.example.com", "wss://api.example.com")
            ),
        ),
    ):
        result = await _preprocess_credentials_on_save(credentials)

    assert session.post_calls == []
    assert session.put_calls[0][1]["json"] == {"defaultApplication": 5278}
    assert result["application_name"] == app["name"]
    assert result["application_uuid"] == app["uuid"]


@pytest.mark.asyncio
async def test_update_preserves_server_managed_domain_uuid():
    incoming_credentials = {
        "bearer_token": "secret-token",
        "domain_id": "renamed-domain.cloudonix.net",
        "domain_uuid": "client-supplied-value",
        "outbound_trunk_uuid": "client-supplied-trunk-uuid",
        "application_name": "existing-app",
    }
    stored_credentials = {
        "domain_uuid": DOMAIN_UUID,
        "outbound_trunk_uuid": TRUNK_UUID,
    }

    with patch(
        "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
        side_effect=AssertionError("domainGet should not run during an update"),
    ):
        result = await _run_preprocess_hook(
            "cloudonix",
            incoming_credentials,
            stored_credentials,
        )

    assert result == {
        **incoming_credentials,
        "domain_uuid": DOMAIN_UUID,
        "outbound_trunk_uuid": TRUNK_UUID,
    }


@pytest.mark.asyncio
async def test_create_configuration_persists_fetched_domain_uuid():
    session = _FakeSession(_FakeResponse(200, {"uuid": DOMAIN_UUID}))
    now = datetime.now(UTC)
    stored_credentials = {
        "bearer_token": "secret-token",
        "domain_id": "friendly-name.cloudonix.net",
        "application_name": "existing-app",
        "outbound_trunk": None,
        "domain_uuid": DOMAIN_UUID,
    }
    row = SimpleNamespace(
        id=41,
        name="Cloudonix",
        provider="cloudonix",
        credentials=stored_credentials,
        is_default_outbound=False,
        inactive=False,
        inactive_since=None,
        inactive_reason=None,
        created_at=now,
        updated_at=now,
    )
    request = TelephonyConfigurationCreateRequest.model_validate(
        {
            "name": "Cloudonix",
            "config": {
                "provider": "cloudonix",
                "bearer_token": "secret-token",
                "domain_id": "friendly-name",
                "domain_uuid": "client-supplied-value",
                "application_name": "existing-app",
            },
        }
    )

    with (
        patch(
            "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
            return_value=session,
        ),
        patch("api.routes.organization.db_client") as db_client,
        patch("api.routes.organization.capture_event"),
    ):
        db_client.create_telephony_configuration = AsyncMock(return_value=row)
        response = await create_telephony_configuration(
            request,
            SimpleNamespace(selected_organization_id=7, provider_id="user-123"),
        )

    db_client.create_telephony_configuration.assert_awaited_once_with(
        organization_id=7,
        name="Cloudonix",
        provider="cloudonix",
        credentials=stored_credentials,
        is_default_outbound=False,
    )
    assert response.credentials["domain_uuid"] == DOMAIN_UUID
    assert response.sip_connectivity is not None
    assert response.sip_connectivity.regions[0].inbound_transports[0].hostname == (
        f"{DOMAIN_UUID}.in.dimi.tel"
    )


@pytest.mark.asyncio
async def test_preprocess_rejects_domain_get_response_without_uuid():
    session = _FakeSession(
        _FakeResponse(200, {"domain": "friendly-name.cloudonix.net"})
    )

    with (
        patch(
            "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
            return_value=session,
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _preprocess_credentials_on_save(
            {
                "bearer_token": "secret-token",
                "domain_id": "friendly-name.cloudonix.net",
                "application_name": "existing-app",
            }
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == (
        "Cloudonix domainGet response did not include a domain UUID"
    )


@pytest.mark.asyncio
async def test_preprocess_propagates_domain_get_http_error():
    session = _FakeSession(_FakeResponse(401, {}, "invalid bearer token"))

    with (
        patch(
            "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
            return_value=session,
        ),
        pytest.raises(HTTPException) as exc_info,
    ):
        await _preprocess_credentials_on_save(
            {
                "bearer_token": "bad-token",
                "domain_id": "friendly-name.cloudonix.net",
                "application_name": "existing-app",
            }
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == ("Failed to fetch Cloudonix domain UUID: HTTP 401")


def test_outbound_trunk_configuration_requires_peer_and_complete_authentication():
    with pytest.raises(ValidationError, match="name and remote SIP address"):
        CloudonixConfigurationRequest.model_validate(
            {
                "bearer_token": "secret-token",
                "domain_id": "friendly-name",
                "outbound_trunk": {"enabled": True},
            }
        )

    with pytest.raises(ValidationError, match="must be provided together"):
        CloudonixConfigurationRequest.model_validate(
            {
                "bearer_token": "secret-token",
                "domain_id": "friendly-name",
                "outbound_trunk": {
                    "enabled": True,
                    "name": "dograh-carrier",
                    "ip": "sip.example.com",
                    "profile": {"authentication": {"username": "carrier-user"}},
                },
            }
        )


def test_cloudonix_domain_normalization_qualifies_short_names():
    custom = CloudonixConfigurationRequest.model_validate(
        {
            "bearer_token": "secret-token",
            "domain_id": "Tenant.Example.Com.",
        }
    )
    legacy = CloudonixConfigurationRequest.model_validate(
        {"bearer_token": "secret-token", "domain_id": "friendly-name"}
    )

    assert custom.domain_id == "tenant.example.com"
    assert legacy.domain_id == "friendly-name.cloudonix.net"
    assert (
        CloudonixProvider._normalize_domain("Tenant.Example.Com.")
        == "tenant.example.com"
    )


def test_cloudonix_webhook_accepts_a_managed_cloudonix_domain():
    webhook = {
        "SessionData": {"token": "session-token"},
        "Domain": "oss-dograh-11111111.cloudonix.net",
        "From": "+15551230001",
        "To": "+15551230002",
    }

    assert CloudonixProvider.can_handle_webhook(webhook, {})
    normalized = CloudonixProvider.parse_inbound_webhook(webhook)
    assert normalized.account_id == "oss-dograh-11111111.cloudonix.net"
    assert CloudonixProvider.validate_account_id(
        {"domain_id": "oss-dograh-11111111.cloudonix.net"},
        normalized.account_id,
    )


def test_cloudonix_metadata_exposes_conditional_outbound_trunk_fields():
    fields = {field.name: field for field in SPEC.ui_metadata.fields}

    assert fields["outbound_trunk.enabled"].type == "boolean"
    assert fields["outbound_trunk.transport"].visible_when.field == (
        "outbound_trunk.enabled"
    )
    assert [option.value for option in fields["outbound_trunk.transport"].options] == [
        "udp",
        "tcp",
        "tls",
    ]
    assert fields["outbound_trunk.profile.authentication.password"].sensitive
    assert SPEC.server_managed_credential_fields == (
        "domain_uuid",
        "application_id",
        "application_uuid",
        "managed_by",
        "provisioning_id",
        "outbound_trunk_uuid",
    )


@pytest.mark.asyncio
async def test_outbound_trunk_create_uses_cloudonix_voice_trunk_schema():
    session = _TrunkSession(
        get_responses=[_FakeResponse(200, [])],
        post_response=_FakeResponse(200, {"uuid": TRUNK_UUID}),
    )
    credentials = {
        "bearer_token": "secret-token",
        "domain_id": "friendly-name.cloudonix.net",
        "outbound_trunk": {
            "enabled": True,
            "name": "dograh-carrier",
            "ip": "sip.example.com",
            "port": 5061,
            "transport": "tls",
            "prefix": "+",
            "profile": {
                "hostname": "border-1.cloudonix.io",
                "domain": "to.example.com",
                "ruri_domain": "ruri.example.com",
                "connection_timeout": 12,
                "provisional_timeout": 3,
                "authentication": {
                    "username": "carrier-user",
                    "password": "carrier-password",
                    "overwrite_from": True,
                },
            },
        },
    }

    with (
        patch(
            "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
            return_value=session,
        ),
        patch("api.services.telephony.providers.cloudonix.logger.info") as log_info,
    ):
        result = await _ensure_outbound_trunk(credentials)

    endpoint = f"{CLOUDONIX_API_BASE_URL}/domains/{credentials['domain_id']}/trunks"
    headers = {
        "Authorization": "Bearer secret-token",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    expected_payload = {
        "name": "dograh-carrier",
        "ip": "sip.example.com",
        "port": 5061,
        "transport": "tls",
        "prefix": "+",
        "direction": "public-outbound",
        "profile": {
            "hostname": "border-1.cloudonix.io",
            "domain": "to.example.com",
            "ruri-domain": "ruri.example.com",
            "connection-timeout": 12,
            "provisional-timeout": 3,
            "authentication": {
                "username": "carrier-user",
                "password": "carrier-password",
                "overwrite-from": True,
            },
        },
    }
    assert session.get_calls == [(endpoint, {"headers": headers})]
    assert session.post_calls == [
        (endpoint, {"json": expected_payload, "headers": headers})
    ]
    assert result == {**credentials, "outbound_trunk_uuid": TRUNK_UUID}

    log_output = "\n".join(str(call.args[0]) for call in log_info.call_args_list)
    assert "secret-token" not in log_output
    assert "carrier-password" not in log_output
    assert "[REDACTED]" in log_output


@pytest.mark.asyncio
async def test_outbound_trunk_reuses_matching_name_without_an_update():
    existing = {
        "uuid": TRUNK_UUID,
        "name": "dograh-carrier",
        "ip": "sip.example.com",
        "port": "5060",
        "transport": "udp",
        "prefix": "",
        "direction": "public-outbound",
        "profile": {"customer-note": "preserve me"},
        "active": True,
    }
    session = _TrunkSession(get_responses=[_FakeResponse(200, [existing])])
    credentials = {
        "bearer_token": "secret-token",
        "domain_id": "friendly-name.cloudonix.net",
        "outbound_trunk": {
            "enabled": True,
            "name": "dograh-carrier",
            "ip": "sip.example.com",
            "port": 5060,
            "transport": "udp",
            "prefix": "",
        },
    }

    with patch(
        "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await _ensure_outbound_trunk(credentials)

    assert session.post_calls == []
    assert session.put_calls == []
    assert result["outbound_trunk_uuid"] == TRUNK_UUID


@pytest.mark.asyncio
async def test_outbound_trunk_updates_by_uuid_and_preserves_unknown_profile_fields():
    existing = {
        "uuid": TRUNK_UUID,
        "name": "old-name",
        "ip": "old.example.com",
        "port": 5060,
        "transport": "udp",
        "prefix": "",
        "direction": "public-outbound",
        "profile": {
            "domain": "old.example.com",
            "authentication": {
                "username": "old-user",
                "password": "old-password",
                "overwrite-from": False,
            },
            "customer-note": "preserve me",
        },
        "active": False,
    }
    session = _TrunkSession(
        get_responses=[_FakeResponse(200, [existing])],
        put_response=_FakeResponse(200, {"uuid": TRUNK_UUID}),
    )
    credentials = {
        "bearer_token": "secret-token",
        "domain_id": "friendly-name.cloudonix.net",
        "outbound_trunk_uuid": TRUNK_UUID,
        "outbound_trunk": {
            "enabled": True,
            "name": "new-name",
            "ip": "new.example.com",
            "port": 5061,
            "transport": "tcp",
            "prefix": "9",
            "profile": {"ruri_domain": "routing.example.com"},
        },
    }

    with patch(
        "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await _ensure_outbound_trunk(credentials)

    collection_endpoint = (
        f"{CLOUDONIX_API_BASE_URL}/domains/{credentials['domain_id']}/trunks"
    )
    assert session.put_calls == [
        (
            f"{collection_endpoint}/{TRUNK_UUID}",
            {
                "json": {
                    "name": "new-name",
                    "ip": "new.example.com",
                    "port": 5061,
                    "transport": "tcp",
                    "prefix": "9",
                    "direction": "public-outbound",
                    "profile": {
                        "customer-note": "preserve me",
                        "ruri-domain": "routing.example.com",
                    },
                    "active": True,
                },
                "headers": {
                    "Authorization": "Bearer secret-token",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            },
        )
    ]
    assert result == credentials


@pytest.mark.asyncio
async def test_disabling_outbound_trunk_deactivates_managed_resource():
    existing = {
        "uuid": TRUNK_UUID,
        "name": "dograh-carrier",
        "direction": "public-outbound",
        "active": True,
    }
    session = _TrunkSession(
        get_responses=[_FakeResponse(200, [existing])],
        put_response=_FakeResponse(200, existing),
    )
    credentials = {
        "bearer_token": "secret-token",
        "domain_id": "friendly-name.cloudonix.net",
        "outbound_trunk_uuid": TRUNK_UUID,
        "outbound_trunk": {"enabled": False},
    }

    with patch(
        "api.services.telephony.providers.cloudonix.aiohttp.ClientSession",
        return_value=session,
    ):
        result = await _ensure_outbound_trunk(credentials)

    assert len(session.put_calls) == 1
    assert session.put_calls[0][1]["json"] == {"active": False}
    assert result == credentials


def test_outbound_trunk_log_redaction_does_not_mutate_request_payload():
    payload = {
        "profile": {
            "authentication": {
                "username": "carrier-user",
                "password": "carrier-password",
            }
        }
    }

    safe_payload = _redact_outbound_trunk_payload(payload)

    assert safe_payload["profile"]["authentication"]["password"] == "[REDACTED]"
    assert payload["profile"]["authentication"]["password"] == "carrier-password"


@pytest.mark.asyncio
async def test_outbound_calls_are_pinned_to_the_managed_trunk_name():
    provider = CloudonixProvider(
        {
            "bearer_token": "secret-token",
            "domain_id": "friendly-name.cloudonix.net",
            "outbound_trunk": {
                "enabled": True,
                "name": "  dograh-carrier  ",
            },
            "outbound_trunk_uuid": f"  {TRUNK_UUID}  ",
            "from_numbers": ["+15551230001"],
        }
    )
    session = _TrunkSession(
        get_responses=[],
        post_response=_FakeResponse(
            200,
            {"token": "session-token", "domainId": 3, "subscriberId": None},
        ),
    )

    with (
        patch(
            "api.services.telephony.providers.cloudonix.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.cloudonix.provider.get_backend_endpoints",
            new=AsyncMock(
                return_value=("https://api.example.com", "wss://ws.example.com")
            ),
        ),
    ):
        result = await provider.initiate_call(
            "+15551230002",
            "unused",
            workflow_run_id=9,
            workflow_id=7,
            organization_id=4,
            trunk=TRUNK_UUID,
        )

    assert result.call_id == "session-token"
    assert session.post_calls[0][1]["json"]["trunk"] == "dograh-carrier"


@pytest.mark.asyncio
async def test_transfer_calls_are_pinned_to_the_managed_trunk_name():
    provider = CloudonixProvider(
        {
            "bearer_token": "secret-token",
            "domain_id": "friendly-name.cloudonix.net",
            "outbound_trunk": {"enabled": True, "name": "dograh-carrier"},
            "outbound_trunk_uuid": TRUNK_UUID,
            "from_numbers": ["+15551230001"],
        }
    )
    session = _TrunkSession(
        get_responses=[],
        post_response=_FakeResponse(200, {"token": "transfer-session-token"}),
    )

    with (
        patch(
            "api.services.telephony.providers.cloudonix.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.cloudonix.provider.get_backend_endpoints",
            new=AsyncMock(
                return_value=("https://api.example.com", "wss://ws.example.com")
            ),
        ),
    ):
        result = await provider.transfer_call(
            destination="+15551230002",
            transfer_id="transfer-1",
            conference_name="conference-1",
            trunk=TRUNK_UUID,
        )

    assert result["call_sid"] == "transfer-session-token"
    assert session.post_calls[0][1]["json"]["trunk"] == "dograh-carrier"


@pytest.mark.asyncio
async def test_disabled_outbound_trunk_is_not_sent_with_outbound_calls():
    provider = CloudonixProvider(
        {
            "bearer_token": "secret-token",
            "domain_id": "friendly-name.cloudonix.net",
            "outbound_trunk": {"enabled": False, "name": "dograh-carrier"},
            "outbound_trunk_uuid": TRUNK_UUID,
            "from_numbers": ["+15551230001"],
        }
    )
    session = _TrunkSession(
        get_responses=[],
        post_response=_FakeResponse(
            200,
            {"token": "session-token", "domainId": 3, "subscriberId": None},
        ),
    )

    with (
        patch(
            "api.services.telephony.providers.cloudonix.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.cloudonix.provider.get_backend_endpoints",
            new=AsyncMock(
                return_value=("https://api.example.com", "wss://ws.example.com")
            ),
        ),
    ):
        await provider.initiate_call(
            "+15551230002",
            "unused",
            workflow_run_id=9,
            workflow_id=7,
            organization_id=4,
            trunk=TRUNK_UUID,
        )

    assert provider.outbound_trunk_name is None
    assert "trunk" not in session.post_calls[0][1]["json"]


def expected_details(domain_uuid):
    india = f"{domain_uuid}.in.dimi.tel"
    uae = f"{domain_uuid}.uae.dimi.tel"
    global_hostname = f"{domain_uuid}.sip.cloudonix.net"
    return {
        "provider_display_name": "Cloudonix",
        "regions": [
            {
                "region": "India",
                "inbound_transports": [
                    {
                        "transport": "UDP",
                        "hostname": india,
                        "port": 9060,
                        "uri": f"{india}:9060",
                    },
                    {
                        "transport": "TCP",
                        "hostname": india,
                        "port": 9060,
                        "uri": f"{india}:9060;transport=tcp;",
                    },
                    {
                        "transport": "TLS",
                        "hostname": india,
                        "port": 9443,
                        "uri": f"{india}:9443;transport=tls;",
                    },
                ],
                "outbound_origin_ip": "128.199.27.19",
            },
            {
                "region": "UAE",
                "inbound_transports": [
                    {
                        "transport": "UDP",
                        "hostname": uae,
                        "port": 9081,
                        "uri": f"{uae}:9081",
                    },
                    {
                        "transport": "TCP",
                        "hostname": uae,
                        "port": 9081,
                        "uri": f"{uae}:9081;transport=tcp;",
                    },
                    {
                        "transport": "TLS",
                        "hostname": uae,
                        "port": 9443,
                        "uri": f"{uae}:9443;transport=tls;",
                    },
                ],
                "outbound_origin_ip": "20.233.60.70",
            },
            {
                "region": "Global",
                "inbound_transports": [
                    {
                        "transport": "UDP",
                        "hostname": global_hostname,
                        "port": 5060,
                        "uri": f"{global_hostname}:5060",
                    },
                    {
                        "transport": "TCP",
                        "hostname": global_hostname,
                        "port": 5060,
                        "uri": f"{global_hostname};transport=tcp;",
                    },
                    {
                        "transport": "TLS",
                        "hostname": global_hostname,
                        "port": 443,
                        "uri": f"{global_hostname}:443;transport=tls;",
                    },
                ],
                "outbound_origin_ip": "18.219.128.166",
            },
        ],
    }
