"""WhatsApp provider tests."""

import hashlib
import hmac
import json
from typing import Any, Dict
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import ValidationError

from api.db import db_client
from api.services.telephony.base import ProviderPhoneNumberLookupError
from api.services.telephony.providers.whatsapp import (
    SPEC,
    _preprocess_credentials_on_save,
)
from api.services.telephony.providers.whatsapp.config import (
    WhatsAppConfigurationRequest,
)
from api.services.telephony.providers.whatsapp.provider import WhatsAppProvider


def _provider(**overrides: Any) -> WhatsAppProvider:
    config = {
        "access_token": "test_token",
        "phone_number_id": "test_phone_id",
        "app_secret": "test_secret",
    }
    config.update(overrides)
    return WhatsAppProvider(config)


def _calls_webhook(call: Dict[str, Any], phone_number_id: str = "test_phone_id"):
    """A Meta ``calls`` webhook as it is actually delivered."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "calls",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "+15551234567",
                                "phone_number_id": phone_number_id,
                            },
                            "calls": [call],
                        },
                    }
                ]
            }
        ],
    }


def _meta_accepts():
    return patch.object(
        WhatsAppProvider,
        "get_available_phone_numbers",
        AsyncMock(return_value=["+15551234567"]),
    )


def _meta_fails(error: ProviderPhoneNumberLookupError):
    return patch.object(
        WhatsAppProvider, "get_available_phone_numbers", AsyncMock(side_effect=error)
    )


def _number_held_by(*configs: Any):
    return patch.object(
        db_client,
        "list_active_whatsapp_configurations_by_phone_number_id",
        AsyncMock(return_value=list(configs)),
    )


class _DummyResponse:
    def __init__(self, status: int, payload: Dict[str, Any]):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)


class _DummySession:
    """Serves canned Graph API responses keyed by URL suffix; records requests."""

    def __init__(self, responses: Dict[str, tuple[int, Dict[str, Any]]]):
        self.responses = responses
        self.requests: list[tuple[str, Dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def get(self, url: str, **kwargs: Any):
        self.requests.append((url, kwargs))
        for suffix, (status, payload) in self.responses.items():
            if url.endswith(suffix):
                return _DummyResponse(status, payload)
        return _DummyResponse(404, {})


class TestWhatsAppProvider(IsolatedAsyncioTestCase):
    def test_requires_access_token_phone_number_id_and_app_secret(self):
        for missing in ("access_token", "phone_number_id", "app_secret"):
            with self.assertRaisesRegex(ValueError, missing):
                _provider(**{missing: ""})
        self.assertTrue(_provider().validate_config())

    def test_spec_is_inbound_only(self):
        self.assertFalse(SPEC.supports_outbound)

    async def test_initiate_call_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            await _provider().initiate_call(
                to_number="+15551234567", webhook_url="https://example.test/webhook"
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_get_available_phone_numbers_returns_the_configured_number(self):
        session = _DummySession(
            {"/test_phone_id": (200, {"display_phone_number": "+1 555-123-4567"})}
        )
        with patch("aiohttp.ClientSession", return_value=session):
            numbers = await _provider().get_available_phone_numbers()

        self.assertEqual(numbers, ["+15551234567"])
        _, kwargs = session.requests[0]
        self.assertEqual(kwargs["params"], {"fields": "display_phone_number"})

    async def test_get_available_phone_numbers_surfaces_meta_errors(self):
        """A rejected token is the account holder's problem, not an unowned number."""
        session = _DummySession(
            {"/test_phone_id": (400, {"error": {"message": "Invalid OAuth token"}})}
        )
        with patch("aiohttp.ClientSession", return_value=session):
            with self.assertRaises(ProviderPhoneNumberLookupError) as ctx:
                await _provider().get_available_phone_numbers()
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_validate_phone_number_accepts_only_the_configured_number(self):
        session = _DummySession(
            {"/test_phone_id": (200, {"display_phone_number": "+1 555-123-4567"})}
        )
        with patch("aiohttp.ClientSession", return_value=session):
            owned = await _provider().validate_phone_number("+1 (555) 123-4567")
            other = await _provider().validate_phone_number("+15559999999")

        self.assertTrue(owned.ok)
        self.assertFalse(other.ok)
        self.assertIn("+15559999999", other.message)

    async def test_verify_inbound_signature(self):
        secret = "my_app_secret"
        provider = _provider(app_secret=secret)
        body = '{"entry": []}'
        digest = hmac.new(
            secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        async def verify(headers):
            return await provider.verify_inbound_signature(
                url="https://example.com/webhook",
                webhook_data={},
                headers=headers,
                body=body,
            )

        self.assertTrue(await verify({"X-Hub-Signature-256": f"sha256={digest}"}))
        self.assertFalse(await verify({"x-hub-signature-256": "sha256=bad"}))
        self.assertFalse(await verify({"x-hub-signature-256": digest}))
        self.assertFalse(await verify({}))
        # Non-ASCII header: rejected, not a TypeError turned into a 500.
        self.assertFalse(await verify({"x-hub-signature-256": f"sha256=é{digest[1:]}"}))

    def test_can_handle_webhook_recognises_meta_payloads_only(self):
        self.assertTrue(
            WhatsAppProvider.can_handle_webhook(_calls_webhook({"id": "c1"}), {})
        )
        self.assertFalse(WhatsAppProvider.can_handle_webhook({"CallSid": "CA123"}, {}))

    def test_parse_inbound_webhook(self):
        payload = _calls_webhook(
            {
                "id": "call_1",
                "from": "+15559876543",
                "to": "+15551234567",
                "event": "connect",
                "direction": "inbound",
            }
        )
        res = WhatsAppProvider.parse_inbound_webhook(payload)

        self.assertEqual(res.provider, "whatsapp")
        self.assertEqual(res.call_id, "call_1")
        self.assertEqual(res.from_number, "+15559876543")
        self.assertEqual(res.to_number, "+15551234567")
        self.assertEqual(res.direction, "inbound")
        self.assertEqual(res.call_status, "connect")
        self.assertEqual(res.account_id, "test_phone_id")

    def test_validate_account_id(self):
        config = {"phone_number_id": "phone_123"}
        self.assertTrue(WhatsAppProvider.validate_account_id(config, "phone_123"))
        self.assertFalse(WhatsAppProvider.validate_account_id(config, "other_id"))
        self.assertFalse(WhatsAppProvider.validate_account_id(config, ""))

    def test_parse_status_callback(self):
        payload = _calls_webhook(
            {
                "id": "call_1",
                "event": "terminate",
                "status": "COMPLETED",
                "duration": 42,
            }
        )
        parsed = _provider().parse_status_callback(payload)
        self.assertEqual(parsed["call_id"], "call_1")
        self.assertEqual(parsed["status"], "completed")
        self.assertEqual(parsed["duration"], 42)

    async def test_transfers_are_unsupported(self):
        provider = _provider()
        self.assertFalse(provider.supports_transfers())
        res = await provider.transfer_call(
            destination="+15551234567", transfer_id="t1", conference_name="c1"
        )
        self.assertEqual(res["status"], "failed")

    def test_error_responses_are_json(self):
        res = WhatsAppProvider.generate_error_response("ERR", "Something failed")
        self.assertIsInstance(res, Response)
        self.assertEqual(res.media_type, "application/json")

        res = WhatsAppProvider.generate_validation_error_response("AUTH_FAILED")
        body = json.loads(res.body.decode())
        self.assertEqual(body["error"], "AUTH_FAILED")
        self.assertIn("message", body)


class TestWhatsAppConfigurationSave(IsolatedAsyncioTestCase):
    """Secrets round-trip masked; the verify token is server-owned and readable."""

    def setUp(self):
        from api.routes.organization import (
            _credentials_for_display,
            preserve_masked_fields,
        )

        self._credentials_for_display = _credentials_for_display
        self.preserve_masked_fields = preserve_masked_fields
        self.stored = {
            "phone_number_id": "106540352242922",
            "access_token": "secret_access_token_value",
            "app_secret": "secret_app_secret_value",
            "webhook_verify_token": "verify_token_value",
        }

    def test_display_masks_secrets_and_keeps_verify_token_readable(self):
        """The customer has to paste the token into Meta, so it must not mask."""
        displayed = self._credentials_for_display("whatsapp", self.stored)

        self.assertEqual(displayed["phone_number_id"], "106540352242922")
        self.assertEqual(displayed["webhook_verify_token"], "verify_token_value")
        self.assertNotEqual(displayed["access_token"], "secret_access_token_value")
        self.assertNotEqual(displayed["app_secret"], "secret_app_secret_value")

    def test_masked_secrets_are_restored_on_update(self):
        displayed = self._credentials_for_display("whatsapp", self.stored)
        request_dict = WhatsAppConfigurationRequest.model_validate(
            displayed
        ).model_dump()
        # The request schema does not declare the token, so an edit cannot
        # change it; the save hook carries the stored one forward instead.
        self.assertNotIn("webhook_verify_token", request_dict)

        self.preserve_masked_fields("whatsapp", request_dict, self.stored)

        self.assertEqual(request_dict["access_token"], "secret_access_token_value")
        self.assertEqual(request_dict["app_secret"], "secret_app_secret_value")

    async def test_verify_token_is_minted_on_create_and_carried_on_update(self):
        with _meta_accepts(), _number_held_by():
            created = await _preprocess_credentials_on_save(
                {"access_token": "t", "phone_number_id": "p", "app_secret": "s"}, None
            )
            self.assertTrue(created["webhook_verify_token"])

            updated = await _preprocess_credentials_on_save(
                {"access_token": "t2", "phone_number_id": "p", "app_secret": "s"},
                created,
            )
        self.assertEqual(
            updated["webhook_verify_token"], created["webhook_verify_token"]
        )

    async def test_save_is_refused_when_meta_rejects_the_credentials(self):
        """A number nobody holds a token for cannot be claimed, and typos fail at save."""
        with _meta_fails(
            ProviderPhoneNumberLookupError(
                "Meta Graph API 401: bad token", status_code=401
            )
        ):
            with self.assertRaises(HTTPException) as ctx:
                await _preprocess_credentials_on_save(
                    {"access_token": "t", "phone_number_id": "p", "app_secret": "s"},
                    None,
                )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("bad token", ctx.exception.detail)

    async def test_save_reports_meta_trouble_as_upstream_failure(self):
        """Only a refusal is the customer's fault; outages and throttling are not."""
        for error in (
            ProviderPhoneNumberLookupError("connection reset"),
            ProviderPhoneNumberLookupError("Meta Graph API 500", status_code=500),
            ProviderPhoneNumberLookupError("Meta Graph API 429", status_code=429),
        ):
            with self.subTest(error=str(error)), _meta_fails(error):
                with self.assertRaises(HTTPException) as ctx:
                    await _preprocess_credentials_on_save(
                        {
                            "access_token": "t",
                            "phone_number_id": "p",
                            "app_secret": "s",
                        },
                        None,
                    )
                self.assertEqual(ctx.exception.status_code, 502)

    async def test_save_is_refused_when_another_configuration_holds_the_number(self):
        """Two active configurations on one number would route its calls nowhere."""
        with _meta_accepts(), _number_held_by(MagicMock(id=7)):
            with self.assertRaises(HTTPException) as ctx:
                await _preprocess_credentials_on_save(
                    {"access_token": "t", "phone_number_id": "p", "app_secret": "s"},
                    None,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_update_keeping_its_number_does_not_conflict_with_itself(self):
        existing = {
            "access_token": "t",
            "phone_number_id": "p",
            "app_secret": "s",
            "webhook_verify_token": "v",
        }
        with _meta_accepts(), _number_held_by(MagicMock(id=7)) as lookup:
            updated = await _preprocess_credentials_on_save(
                {"access_token": "t2", "phone_number_id": "p", "app_secret": "s"},
                existing,
            )
        lookup.assert_not_called()
        self.assertEqual(updated["webhook_verify_token"], "v")

    async def test_update_moving_to_a_number_another_configuration_holds_is_refused(
        self,
    ):
        existing = {
            "access_token": "t",
            "phone_number_id": "p",
            "app_secret": "s",
            "webhook_verify_token": "v",
        }
        with _meta_accepts(), _number_held_by(MagicMock(id=7)):
            with self.assertRaises(HTTPException) as ctx:
                await _preprocess_credentials_on_save(
                    {"access_token": "t", "phone_number_id": "q", "app_secret": "s"},
                    existing,
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_request_requires_every_credential(self):
        with self.assertRaises(ValidationError) as ctx:
            WhatsAppConfigurationRequest(phone_number_id="12345")
        errors = {e["loc"][0] for e in ctx.exception.errors()}
        self.assertEqual(errors, {"access_token", "app_secret"})
