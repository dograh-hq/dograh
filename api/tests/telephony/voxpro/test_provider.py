"""Unit tests for the VoxPro telephony provider (mocked HTTP, no live carrier)."""

import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.providers.voxpro.provider import (
    TRANSFER_CONFERENCE_PREFIX,
    VoxProProvider,
)


def _provider(**over):
    cfg = {
        "api_key": "vpk_test_123",
        "tenant_id": "AI_Katha_1783948668",
        "api_base": "https://connector.voxprosolutions.com",
        "from_numbers": ["08071661528"],
    }
    cfg.update(over)
    return VoxProProvider(cfg)


class _FakeResp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append(("POST", url, json, headers))
        return self._resp

    def get(self, url, headers=None):
        self.calls.append(("GET", url, None, headers))
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_validate_config():
    assert _provider().validate_config() is True
    assert _provider(api_key="").validate_config() is False
    assert _provider(from_numbers=[]).validate_config() is False


def test_supports_transfers():
    assert _provider().supports_transfers() is True


@pytest.mark.asyncio
async def test_get_available_phone_numbers():
    p = _provider()
    assert await p.get_available_phone_numbers() == p.from_numbers


@pytest.mark.asyncio
async def test_initiate_call_posts_to_connector():
    resp = _FakeResp(
        201, {"call_id": "sess-abc", "session_uuid": "sess-abc", "state": "ringing"}
    )
    session = _FakeSession(resp)

    with (
        patch(
            "api.services.telephony.providers.voxpro.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.voxpro.provider.get_backend_endpoints",
            new=AsyncMock(
                return_value=("https://api.dograh.example", "wss://api.dograh.example")
            ),
        ),
    ):
        result = await _provider().initiate_call(
            to_number="+919513049206",
            webhook_url="https://api.dograh.example/x",
            workflow_run_id=42,
            workflow_id=7,
            organization_id=3,
        )

    assert result.call_id == "sess-abc"
    assert result.caller_number == "08071661528"
    # call_id must ride in provider_metadata so the dispatcher merges it into
    # gathered_context['call_id'] for transfer_call to recover later.
    assert result.provider_metadata["call_id"] == "sess-abc"
    method, url, body, headers = session.calls[0]
    assert url.endswith("/v1/calls/originate")
    assert headers["X-API-Key"] == "vpk_test_123"
    assert headers["X-Tenant-ID"] == "AI_Katha_1783948668"
    assert body["to_number"] == "919513049206"  # + stripped
    assert body["ws_url"].endswith(
        "/api/v1/telephony/ws/7/3/42"
    )  # generic mounted route
    assert body["workflow_run_id"] == "42"
    assert "status_url" not in body


@pytest.mark.asyncio
async def test_initiate_call_requires_ids():
    with patch(
        "api.services.telephony.providers.voxpro.provider.get_backend_endpoints",
        new=AsyncMock(return_value=("https://x", "wss://x")),
    ):
        with pytest.raises(ValueError):
            await _provider().initiate_call(
                to_number="+91",
                webhook_url="u",
                workflow_run_id=None,
                workflow_id=7,
                organization_id=3,
            )


@pytest.mark.asyncio
async def test_transfer_call_decodes_conference_name():
    # conference_name is "transfer-{original_call_sid}"; VoxPro must transfer the
    # real carrier call, not the generated transfer_id.
    session = _FakeSession(_FakeResp(200, {"ok": True}))
    with (
        patch(
            "api.services.telephony.providers.voxpro.provider.aiohttp.ClientSession",
            return_value=session,
        ),
        patch(
            "api.services.telephony.providers.voxpro.provider.get_backend_endpoints",
            new=AsyncMock(
                return_value=("https://api.dograh.example", "wss://api.dograh.example")
            ),
        ),
    ):
        out = await _provider().transfer_call(
            destination="+918888888888",
            transfer_id="tid-uuid",
            conference_name=f"{TRANSFER_CONFERENCE_PREFIX}CARRIER123",
        )
    assert out["call_sid"] == "CARRIER123"
    _, url, body, _ = session.calls[0]
    assert url.endswith("/v1/calls/CARRIER123/transfer")
    assert body["destination"] == "918888888888"
    # The connector must be told where to report completion, else the shared
    # wait_for_transfer_completion() never gets its event and the transfer times out.
    assert body["result_url"].endswith(
        "/api/v1/telephony/voxpro/transfer-result/tid-uuid"
    )


def test_parse_inbound_webhook():
    data = {
        "call_id": "c1",
        "from": "+919999999999",
        "to": "08071661528",
        "status": "ringing",
        "tenant_id": "AI_Katha_1783948668",
    }
    n = VoxProProvider.parse_inbound_webhook(data)
    assert n.provider == "voxpro"
    assert n.direction == "inbound"
    assert n.account_id == "AI_Katha_1783948668"
    assert n.call_id == "c1"


def test_validate_account_id():
    cfg = {"tenant_id": "AI_Katha_1783948668"}
    assert VoxProProvider.validate_account_id(cfg, "AI_Katha_1783948668") is True
    assert VoxProProvider.validate_account_id(cfg, "someone_else") is False
    assert VoxProProvider.validate_account_id(cfg, "") is False


@pytest.mark.asyncio
async def test_verify_inbound_signature_hmac():
    p = _provider()
    body = json.dumps({"call_id": "c1"}, separators=(",", ":"))
    good = hmac.new(b"vpk_test_123", body.encode(), hashlib.sha256).hexdigest()

    assert (
        await p.verify_inbound_signature(
            "u", {"call_id": "c1"}, {"X-VoxPro-Signature": good}, body=body
        )
        is True
    )
    assert (
        await p.verify_inbound_signature(
            "u", {"call_id": "c1"}, {"X-VoxPro-Signature": "deadbeef"}, body=body
        )
        is False
    )
    assert (
        await p.verify_inbound_signature("u", {"call_id": "c1"}, {}, body=body) is False
    )


# ── Transfer-completion callback (greptile P1) ──────────────────────────────
# A blind transfer completes asynchronously in the connector, which POSTs the
# outcome to /voxpro/transfer-result/{transfer_id}. The route publishes the
# TransferEvent the shared flow's wait_for_transfer_completion() blocks on.


def _transfer_context():
    from api.services.telephony.transfer_event_protocol import TransferContext

    return TransferContext(
        transfer_id="tid-uuid",
        call_sid="CARRIER123",
        target_number="918888888888",
        tool_uuid="tool-1",
        original_call_sid="CARRIER123",
        conference_name=f"{TRANSFER_CONFERENCE_PREFIX}CARRIER123",
        initiated_at=0.0,
        workflow_run_id=42,
    )


def test_build_transfer_event_success():
    from api.services.telephony.providers.voxpro.routes import build_transfer_event
    from api.services.telephony.transfer_event_protocol import TransferEventType

    ev = build_transfer_event(
        "tid-uuid", _transfer_context(), {"outcome": "answered", "call_sid": "DEST9"}
    )
    assert ev.type == TransferEventType.DESTINATION_ANSWERED
    assert ev.action == "destination_answered"  # → tool ends pipeline (success)
    assert ev.original_call_sid == "CARRIER123"
    assert ev.transfer_call_sid == "DEST9"
    assert ev.to_result_dict()["status"] == "success"


def test_build_transfer_event_failure():
    from api.services.telephony.providers.voxpro.routes import build_transfer_event
    from api.services.telephony.transfer_event_protocol import TransferEventType

    ev = build_transfer_event(
        "tid-uuid", _transfer_context(), {"outcome": "failed", "reason": "no_answer"}
    )
    assert ev.type == TransferEventType.TRANSFER_FAILED
    assert ev.action == "transfer_failed"  # → LLM tells the user
    assert ev.reason == "no_answer"
    assert ev.end_call is True


def test_build_transfer_event_no_context():
    # The wait may have already timed out and removed the context; still emit a
    # well-formed event rather than crashing the callback.
    from api.services.telephony.providers.voxpro.routes import build_transfer_event
    from api.services.telephony.transfer_event_protocol import TransferEventType

    ev = build_transfer_event("tid-uuid", None, {"outcome": "answered"})
    assert ev.type == TransferEventType.DESTINATION_ANSWERED
    assert ev.original_call_sid == ""


class _FakeManager:
    def __init__(self, claim=True, context=None):
        self._claim = claim
        self._context = context
        self.published = []
        self.claims = []

    async def claim_transfer_step(self, transfer_id, step, ttl=300):
        self.claims.append((transfer_id, step))
        return self._claim

    async def get_transfer_context(self, transfer_id):
        return self._context

    async def publish_transfer_event(self, event):
        self.published.append(event)


_CALLBACK_KEY = "tenant-api-key"


class _FakeRequest:
    """Request double carrying a raw body, headers and url, like Starlette's."""

    def __init__(self, payload, *, signature=None, sign_with=_CALLBACK_KEY):
        self._raw = json.dumps(payload, separators=(",", ":")).encode()
        self._payload = payload
        if signature is None and sign_with is not None:
            signature = hmac.new(
                sign_with.encode(), self._raw, hashlib.sha256
            ).hexdigest()
        self.headers = {"X-VoxPro-Signature": signature} if signature else {}
        self.url = (
            "https://dograh.test/api/v1/telephony/voxpro/transfer-result/tid-uuid"
        )

    async def body(self):
        return self._raw

    async def json(self):
        return self._payload


def _patch_callback_auth(routes_mod, api_key=_CALLBACK_KEY):
    """Resolve callback credentials to a VoxPro provider holding ``api_key``."""
    from api.services.telephony.providers.voxpro.provider import VoxProProvider

    provider = VoxProProvider({"api_key": api_key, "tenant_id": "T1"})

    class _Run:
        workflow_run_id = 7
        workflow = type("W", (), {"organization_id": 1})()

    return (
        patch.object(
            routes_mod.db_client,
            "get_workflow_run_by_id",
            new=AsyncMock(return_value=_Run()),
        ),
        patch.object(
            routes_mod,
            "get_telephony_provider_for_run",
            new=AsyncMock(return_value=provider),
        ),
    )


@pytest.mark.asyncio
async def test_transfer_result_route_publishes_event():
    import api.services.telephony.providers.voxpro.routes as routes_mod
    from api.services.telephony.transfer_event_protocol import TransferEventType

    mgr = _FakeManager(claim=True, context=_transfer_context())
    run_patch, provider_patch = _patch_callback_auth(routes_mod)
    with (
        patch.object(
            routes_mod, "get_call_transfer_manager", new=AsyncMock(return_value=mgr)
        ),
        run_patch,
        provider_patch,
    ):
        res = await routes_mod.handle_voxpro_transfer_result(
            "tid-uuid", _FakeRequest({"outcome": "answered", "call_sid": "DEST9"})
        )

    assert res["status"] == "ok"
    assert len(mgr.published) == 1
    assert mgr.published[0].type == TransferEventType.DESTINATION_ANSWERED


@pytest.mark.asyncio
async def test_transfer_result_route_is_idempotent():
    import api.services.telephony.providers.voxpro.routes as routes_mod

    # claim_transfer_step returns False on a retried delivery → no second publish.
    mgr = _FakeManager(claim=False, context=_transfer_context())
    run_patch, provider_patch = _patch_callback_auth(routes_mod)
    with (
        patch.object(
            routes_mod, "get_call_transfer_manager", new=AsyncMock(return_value=mgr)
        ),
        run_patch,
        provider_patch,
    ):
        res = await routes_mod.handle_voxpro_transfer_result(
            "tid-uuid", _FakeRequest({"outcome": "answered"})
        )

    assert res["status"] == "duplicate"
    assert mgr.published == []


def test_generate_validation_error_response_exists_and_returns_400():
    """The shared /inbound/run dispatcher calls this on the provider *class*.

    Regression: it was missing, so a misconfigured DID raised AttributeError inside
    the dispatcher and the caller got a bare hangup instead of the real reason.
    """
    from api.errors.telephony_errors import TelephonyError
    from api.services.telephony.providers.voxpro.provider import VoxProProvider

    resp = VoxProProvider.generate_validation_error_response(
        TelephonyError.PHONE_NUMBER_NOT_CONFIGURED
    )
    assert resp.status_code == 400
    body = json.loads(resp.body)
    assert body["success"] is False
    assert str(TelephonyError.PHONE_NUMBER_NOT_CONFIGURED) in body["error"]


@pytest.mark.asyncio
async def test_transfer_result_rejects_unsigned_callback():
    """An unsigned POST must not be able to complete a transfer.

    Without this, anything that learned a transfer_id could post
    {"outcome": "answered"} and the waiting workflow would end its pipeline leg
    as if the destination had picked up, with no carrier confirmation.
    """
    from fastapi import HTTPException

    import api.services.telephony.providers.voxpro.routes as routes_mod

    mgr = _FakeManager(claim=True, context=_transfer_context())
    run_patch, provider_patch = _patch_callback_auth(routes_mod)
    with (
        patch.object(
            routes_mod, "get_call_transfer_manager", new=AsyncMock(return_value=mgr)
        ),
        run_patch,
        provider_patch,
    ):
        with pytest.raises(HTTPException) as exc:
            await routes_mod.handle_voxpro_transfer_result(
                "tid-uuid",
                _FakeRequest({"outcome": "answered"}, sign_with=None),
            )

    assert exc.value.status_code == 401
    assert mgr.published == []
    assert mgr.claims == [], "unauthenticated caller must not burn the one-shot claim"


@pytest.mark.asyncio
async def test_transfer_result_rejects_wrong_key_signature():
    """A signature from any other key is rejected."""
    from fastapi import HTTPException

    import api.services.telephony.providers.voxpro.routes as routes_mod

    mgr = _FakeManager(claim=True, context=_transfer_context())
    run_patch, provider_patch = _patch_callback_auth(routes_mod)
    with (
        patch.object(
            routes_mod, "get_call_transfer_manager", new=AsyncMock(return_value=mgr)
        ),
        run_patch,
        provider_patch,
    ):
        with pytest.raises(HTTPException) as exc:
            await routes_mod.handle_voxpro_transfer_result(
                "tid-uuid",
                _FakeRequest({"outcome": "answered"}, sign_with="attacker-key"),
            )

    assert exc.value.status_code == 401
    assert mgr.published == []


@pytest.mark.asyncio
async def test_transfer_result_rejects_unknown_transfer():
    """No context means no credentials to authenticate against — and nothing to unblock."""
    from fastapi import HTTPException

    import api.services.telephony.providers.voxpro.routes as routes_mod

    mgr = _FakeManager(claim=True, context=None)
    with patch.object(
        routes_mod, "get_call_transfer_manager", new=AsyncMock(return_value=mgr)
    ):
        with pytest.raises(HTTPException) as exc:
            await routes_mod.handle_voxpro_transfer_result(
                "tid-uuid", _FakeRequest({"outcome": "answered"})
            )

    assert exc.value.status_code == 404
    assert mgr.published == []
