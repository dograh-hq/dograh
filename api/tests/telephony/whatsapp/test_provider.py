"""WhatsApp provider tests."""

import hashlib
import hmac
import json
from contextlib import contextmanager
from typing import Any, Dict
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import ValidationError

from api.db import db_client
from api.services.telephony.base import (
    ProviderPhoneNumber,
    ProviderPhoneNumberLookupError,
)
from api.services.telephony.providers.whatsapp import (
    SPEC,
    _preprocess_credentials_on_save,
)
from api.services.telephony.providers.whatsapp.config import (
    WhatsAppConfigurationRequest,
)
from api.services.telephony.providers.whatsapp.provider import (
    GRAPH_API_BASE_URL,
    PHONE_NUMBER_FIELDS,
    MetaGraphError,
    WhatsAppProvider,
)

WABA = "test_waba"
CREDENTIALS = {"access_token": "t", "waba_id": WABA, "app_secret": "s"}


def _provider(**overrides: Any) -> WhatsAppProvider:
    config = {
        "access_token": "test_token",
        "waba_id": WABA,
        "app_secret": "test_secret",
    }
    config.update(overrides)
    return WhatsAppProvider(config)


def _calls_webhook(
    call: Dict[str, Any], phone_number_id: str = "test_phone_id", waba_id: str = WABA
):
    """A Meta ``calls`` webhook as it is actually delivered."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": waba_id,
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
                ],
            }
        ],
    }


def _meta_number(address: str = "+15551234567", **metadata: Any) -> ProviderPhoneNumber:
    return ProviderPhoneNumber(
        address=address,
        external_id="106540352242922",
        label="Example Business",
        metadata={"phone_number_id": "106540352242922", **metadata},
    )


@contextmanager
def _meta_accepts(subscribe: Any = None):
    """Meta lists the account's numbers and accepts the app subscription."""
    with (
        patch.object(
            WhatsAppProvider,
            "list_provider_phone_numbers",
            AsyncMock(return_value=[_meta_number()]),
        ),
        patch.object(
            WhatsAppProvider, "subscribe_app", subscribe or AsyncMock()
        ) as subscribe_mock,
    ):
        yield subscribe_mock


def _meta_fails(error: ProviderPhoneNumberLookupError):
    return patch.object(
        WhatsAppProvider, "list_provider_phone_numbers", AsyncMock(side_effect=error)
    )


def _account_held_by(*configs: Any):
    return patch.object(
        db_client,
        "list_active_whatsapp_configurations_by_waba_id",
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
        self.requests: list[tuple[str, str, Dict[str, Any]]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    def _serve(self, method: str, url: str, kwargs: Dict[str, Any]):
        self.requests.append((method, url, kwargs))
        for suffix, (status, payload) in self.responses.items():
            if url.endswith(suffix):
                return _DummyResponse(status, payload)
        return _DummyResponse(404, {})

    def get(self, url: str, **kwargs: Any):
        return self._serve("get", url, kwargs)

    def post(self, url: str, **kwargs: Any):
        return self._serve("post", url, kwargs)


def _page(*items: Dict[str, Any], next_url: str | None = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"data": list(items)}
    if next_url:
        payload["paging"] = {"cursors": {"after": "c"}, "next": next_url}
    return payload


def _meta_item(display: str, number_id: str, name: str = "Example Business"):
    return {
        "id": number_id,
        "display_phone_number": display,
        "verified_name": name,
        "platform_type": "CLOUD_API",
        "account_mode": "LIVE",
    }


class TestWhatsAppProvider(IsolatedAsyncioTestCase):
    def test_requires_access_token_waba_id_and_app_secret(self):
        for missing in ("access_token", "waba_id", "app_secret"):
            with self.assertRaisesRegex(ValueError, missing):
                _provider(**{missing: ""})
        self.assertTrue(_provider().validate_config())

    def test_spec_is_inbound_only_and_syncs_numbers(self):
        self.assertFalse(SPEC.supports_outbound)
        self.assertTrue(SPEC.supports_phone_number_sync)
        self.assertEqual(SPEC.account_id_credential_field, "waba_id")

    async def test_initiate_call_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            await _provider().initiate_call(
                to_number="+15551234567", webhook_url="https://example.test/webhook"
            )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_lists_every_number_on_the_account_across_pages(self):
        next_url = f"{GRAPH_API_BASE_URL}/{WABA}/phone_numbers?after=c"
        session = _DummySession(
            {
                "after=c": (200, _page(_meta_item("+44 20 7946 0958", "2"))),
                f"/{WABA}/phone_numbers": (
                    200,
                    _page(_meta_item("+1 555-123-4567", "1"), next_url=next_url),
                ),
            }
        )
        with patch("aiohttp.ClientSession", return_value=session):
            numbers = await _provider().list_provider_phone_numbers()

        self.assertEqual(
            [n.address for n in numbers], ["+15551234567", "+442079460958"]
        )
        self.assertEqual(numbers[0].external_id, "1")
        self.assertEqual(numbers[0].label, "Example Business")
        self.assertEqual(
            numbers[0].metadata,
            {
                "phone_number_id": "1",
                "verified_name": "Example Business",
                "platform_type": "CLOUD_API",
                "account_mode": "LIVE",
            },
        )
        # The first request asks for the fields; the ``next`` link already
        # carries them, so it is followed verbatim.
        first, second = session.requests
        self.assertEqual(first[2]["params"], {"fields": PHONE_NUMBER_FIELDS})
        self.assertEqual(second[1], next_url)
        self.assertIsNone(second[2]["params"])
        self.assertEqual(first[2]["headers"], {"Authorization": "Bearer test_token"})

    async def test_listing_refuses_a_malformed_answer(self):
        """A 200 that is not a listing is not an empty account; the sync would
        report every number as gone, or the handler would crash past the
        lookup-error mapping."""
        for name, payload in (
            ("no data list", {"paging": {}}),
            ("data not a list", {"data": {"id": "1"}}),
            ("paging not an object", {"data": [], "paging": "junk"}),
            ("next not a link", {"data": [], "paging": {"next": 7}}),
        ):
            with self.subTest(name):
                session = _DummySession({f"/{WABA}/phone_numbers": (200, payload)})
                with patch("aiohttp.ClientSession", return_value=session):
                    with self.assertRaises(ProviderPhoneNumberLookupError):
                        await _provider().list_provider_phone_numbers()

    async def test_listing_refuses_to_stop_short_of_the_last_page(self):
        """A truncated listing must fail, not pass for the whole account."""
        from api.services.telephony.providers.whatsapp import provider as module

        base = f"{GRAPH_API_BASE_URL}/{WABA}/phone_numbers"
        endless = _DummySession(
            {
                "after=1": (
                    200,
                    _page(
                        _meta_item("+1 555-000-0002", "2"), next_url=f"{base}?after=2"
                    ),
                ),
                "after=2": (
                    200,
                    _page(
                        _meta_item("+1 555-000-0003", "3"), next_url=f"{base}?after=3"
                    ),
                ),
                f"/{WABA}/phone_numbers": (
                    200,
                    _page(
                        _meta_item("+1 555-000-0001", "1"), next_url=f"{base}?after=1"
                    ),
                ),
            }
        )
        looping = _DummySession(
            {
                "after=1": (
                    200,
                    _page(
                        _meta_item("+1 555-000-0002", "2"), next_url=f"{base}?after=1"
                    ),
                ),
                f"/{WABA}/phone_numbers": (
                    200,
                    _page(
                        _meta_item("+1 555-000-0001", "1"), next_url=f"{base}?after=1"
                    ),
                ),
            }
        )
        for name, session in (("past the cap", endless), ("repeating a page", looping)):
            with self.subTest(name):
                with (
                    patch("aiohttp.ClientSession", return_value=session),
                    patch.object(module, "MAX_PHONE_NUMBER_PAGES", 3),
                ):
                    with self.assertRaises(ProviderPhoneNumberLookupError):
                        await _provider().list_provider_phone_numbers()

    async def test_get_available_phone_numbers_are_the_accounts_numbers(self):
        session = _DummySession(
            {f"/{WABA}/phone_numbers": (200, _page(_meta_item("+1 555-123-4567", "1")))}
        )
        with patch("aiohttp.ClientSession", return_value=session):
            numbers = await _provider().get_available_phone_numbers()
        self.assertEqual(numbers, ["+15551234567"])

    async def test_listing_surfaces_meta_errors(self):
        """A rejected token is the account holder's problem, not an empty account."""
        session = _DummySession(
            {
                f"/{WABA}/phone_numbers": (
                    400,
                    {"error": {"message": "Invalid OAuth token"}},
                )
            }
        )
        with patch("aiohttp.ClientSession", return_value=session):
            with self.assertRaises(ProviderPhoneNumberLookupError) as ctx:
                await _provider().list_provider_phone_numbers()
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Invalid OAuth token", str(ctx.exception))

    async def test_validate_phone_number_accepts_only_numbers_on_the_account(self):
        session = _DummySession(
            {f"/{WABA}/phone_numbers": (200, _page(_meta_item("+1 555-123-4567", "1")))}
        )
        with patch("aiohttp.ClientSession", return_value=session):
            owned = await _provider().validate_phone_number("+1 (555) 123-4567")
            other = await _provider().validate_phone_number("+15559999999")

        self.assertTrue(owned.ok)
        self.assertFalse(other.ok)
        self.assertIn("+15559999999", other.message)
        self.assertIn(WABA, other.message)

    async def test_subscribe_app_posts_to_the_account(self):
        session = _DummySession({f"/{WABA}/subscribed_apps": (200, {"success": True})})
        with patch("aiohttp.ClientSession", return_value=session):
            await _provider().subscribe_app()
        method, url, kwargs = session.requests[0]
        self.assertEqual(method, "post")
        self.assertEqual(url, f"{GRAPH_API_BASE_URL}/{WABA}/subscribed_apps")
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer test_token"})

    async def test_subscribe_app_raises_when_meta_declines(self):
        for status, payload in (
            (403, {"error": {"message": "(#200) Permissions error"}}),
            (200, {"success": False}),
        ):
            with self.subTest(status=status):
                session = _DummySession({f"/{WABA}/subscribed_apps": (status, payload)})
                with patch("aiohttp.ClientSession", return_value=session):
                    with self.assertRaises(MetaGraphError) as ctx:
                        await _provider().subscribe_app()
                if status != 200:
                    self.assertEqual(ctx.exception.status_code, status)

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

    def test_parse_inbound_webhook_keys_on_the_business_account(self):
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
        self.assertEqual(res.account_id, WABA)

    def test_validate_account_id(self):
        config = {"waba_id": "waba_123"}
        self.assertTrue(WhatsAppProvider.validate_account_id(config, "waba_123"))
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
            "waba_id": "1526582272314317",
            "access_token": "secret_access_token_value",
            "app_secret": "secret_app_secret_value",
            "webhook_verify_token": "verify_token_value",
        }

    def test_display_masks_secrets_and_keeps_verify_token_readable(self):
        """The customer has to paste the token into Meta, so it must not mask."""
        displayed = self._credentials_for_display("whatsapp", self.stored)

        self.assertEqual(displayed["waba_id"], "1526582272314317")
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
        with _meta_accepts(), _account_held_by():
            created = await _preprocess_credentials_on_save(dict(CREDENTIALS), None)
            self.assertTrue(created["webhook_verify_token"])

            updated = await _preprocess_credentials_on_save(
                {**CREDENTIALS, "access_token": "t2"}, created
            )
        self.assertEqual(
            updated["webhook_verify_token"], created["webhook_verify_token"]
        )

    async def test_save_subscribes_the_app_to_the_account(self):
        """Without the subscription Meta never delivers the account's calls."""
        with _meta_accepts() as subscribe, _account_held_by():
            await _preprocess_credentials_on_save(dict(CREDENTIALS), None)
        subscribe.assert_awaited_once()

    async def test_save_is_refused_when_meta_rejects_the_credentials(self):
        """An account nobody holds a token for cannot be claimed, and typos fail at save."""
        with _meta_fails(
            ProviderPhoneNumberLookupError(
                "Meta Graph API 401: bad token", status_code=401
            )
        ):
            with self.assertRaises(HTTPException) as ctx:
                await _preprocess_credentials_on_save(dict(CREDENTIALS), None)
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
                    await _preprocess_credentials_on_save(dict(CREDENTIALS), None)
                self.assertEqual(ctx.exception.status_code, 502)

    async def test_save_is_refused_when_meta_declines_the_subscription(self):
        declined = AsyncMock(
            side_effect=MetaGraphError("Meta Graph API 403: denied", status_code=403)
        )
        with _meta_accepts(subscribe=declined), _account_held_by():
            with self.assertRaises(HTTPException) as ctx:
                await _preprocess_credentials_on_save(dict(CREDENTIALS), None)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("subscribe", ctx.exception.detail)

        unreachable = AsyncMock(side_effect=MetaGraphError("connection reset"))
        with _meta_accepts(subscribe=unreachable), _account_held_by():
            with self.assertRaises(HTTPException) as ctx:
                await _preprocess_credentials_on_save(dict(CREDENTIALS), None)
        self.assertEqual(ctx.exception.status_code, 502)

    async def test_save_is_refused_when_another_configuration_holds_the_account(self):
        """Two active configurations on one account would route its calls nowhere."""
        with _meta_accepts() as subscribe, _account_held_by(MagicMock(id=7)):
            with self.assertRaises(HTTPException) as ctx:
                await _preprocess_credentials_on_save(dict(CREDENTIALS), None)
        self.assertEqual(ctx.exception.status_code, 409)
        # A refused save must not touch Meta's subscription either.
        subscribe.assert_not_awaited()

    async def test_update_keeping_its_account_does_not_conflict_with_itself(self):
        existing = {**CREDENTIALS, "webhook_verify_token": "v"}
        with _meta_accepts(), _account_held_by(MagicMock(id=7)) as lookup:
            updated = await _preprocess_credentials_on_save(
                {**CREDENTIALS, "access_token": "t2"}, existing
            )
        lookup.assert_not_called()
        self.assertEqual(updated["webhook_verify_token"], "v")

    async def test_update_moving_to_an_account_another_configuration_holds_is_refused(
        self,
    ):
        existing = {**CREDENTIALS, "webhook_verify_token": "v"}
        with _meta_accepts(), _account_held_by(MagicMock(id=7)):
            with self.assertRaises(HTTPException) as ctx:
                await _preprocess_credentials_on_save(
                    {**CREDENTIALS, "waba_id": "other_waba"}, existing
                )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_request_requires_every_credential(self):
        with self.assertRaises(ValidationError) as ctx:
            WhatsAppConfigurationRequest(waba_id="12345")
        errors = {e["loc"][0] for e in ctx.exception.errors()}
        self.assertEqual(errors, {"access_token", "app_secret"})
