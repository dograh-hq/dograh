"""WhatsApp provider tests."""

import hashlib
import hmac
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import Response

from api.enums import TelephonyCallStatus
from api.services.telephony.base import NormalizedInboundData
from api.services.telephony.providers.whatsapp.provider import WhatsAppProvider


def _provider(**kwargs) -> WhatsAppProvider:
    """Create a test WhatsApp provider with minimal configuration."""
    default_config = {
        "access_token": "test_token",
        "phone_number_id": "test_phone_id",
        "webhook_verify_token": "test_verify_token",
        "app_secret": "test_secret",
        "from_numbers": ["+15551234567"],
    }
    default_config.update(kwargs)
    return WhatsAppProvider(default_config)


class TestWhatsAppProvider(IsolatedAsyncioTestCase):
    """Test suite for WhatsAppProvider implementation."""

    async def test_initialization_with_valid_config(self):
        """Test provider initializes correctly with valid configuration."""
        provider = _provider(
            access_token="test_token",
            phone_number_id="test_phone_id",
            webhook_verify_token="test_verify_token",
            app_secret="test_secret",
        )
        self.assertEqual(provider.access_token, "test_token")
        self.assertEqual(provider.phone_number_id, "test_phone_id")
        self.assertEqual(provider.PROVIDER_NAME, "whatsapp")
        self.assertTrue(provider.validate_config())

    async def test_initialization_with_missing_required_fields(self):
        """Test provider fails initialization with missing required fields."""
        with self.assertRaises(ValueError):
            WhatsAppProvider({
                "access_token": "test_token"
                # Missing phone_number_id, webhook_verify_token, app_secret
            })

    async def test_initialization_with_missing_phone_number_id(self):
        """Test provider fails initialization with missing phone_number_id."""
        with self.assertRaisesRegex(ValueError, "phone_number_id"):
            _provider(phone_number_id="")

    async def test_initialization_with_missing_app_secret(self):
        """Test provider fails initialization with missing app_secret."""
        with self.assertRaisesRegex(ValueError, "app_secret"):
            _provider(app_secret="")

    async def test_initialization_with_missing_webhook_verify_token(self):
        """Test provider fails initialization with missing webhook_verify_token."""
        with self.assertRaisesRegex(ValueError, "webhook_verify_token"):
            _provider(webhook_verify_token="")

    async def test_initiate_call_raises_under_development(self):
        """Test initiate_call raises HTTPException indicating outbound is under development."""
        provider = _provider()
        with self.assertRaises(HTTPException) as ctx:
            await provider.initiate_call(
                to_number="+15551234567",
                webhook_url="https://example.test/webhook",
                workflow_run_id=123,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("under development", ctx.exception.detail)

    def test_whatsapp_setup_checklist_blocks_outbound(self):
        """Test WhatsApp setup checklist resolver explicitly reports outbound is blocked/under development."""
        from api.services.telephony.providers.whatsapp import _whatsapp_setup_checklist
        from api.services.telephony.registry import ConfigurationSetupState

        checklist = _whatsapp_setup_checklist(
            {},
            ConfigurationSetupState(
                active_phone_number_count=1,
                inbound_routed_phone_number_count=1,
                enabled_trunk_count=0,
                unassigned_active_phone_number_count=0,
            ),
        )
        self.assertFalse(checklist.ready_for_outbound)
        self.assertIn("under development", checklist.outbound_blocked_reason)

    async def test_request_call_permission_payload(self):
        """Test _request_call_permission constructs correct payload with messaging_product."""
        provider = _provider(business_initiated_calls_enabled=True)
        captured_payload = {}

        class DummyResponse:
            status = 200
            async def json(self):
                return {"id": "call_999", "status": "permission_requested"}
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        class DummySession:
            def post(self, url, headers=None, json=None):
                nonlocal captured_payload
                captured_payload = json
                return DummyResponse()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass

        with patch("aiohttp.ClientSession", return_value=DummySession()):
            res = await provider._request_call_permission(
                to_number="+15551234567",
                workflow_run_id=789,
            )
            self.assertEqual(res["id"], "call_999")
            self.assertEqual(captured_payload.get("messaging_product"), "whatsapp")
            self.assertEqual(captured_payload.get("to"), "15551234567")
            self.assertEqual(captured_payload.get("biz_opaque_callback_data"), "789")

    async def test_supports_transfers_is_false(self):
        """Test WhatsApp provider indicates it does not support transfers."""
        provider = _provider()
        self.assertFalse(provider.supports_transfers())

    async def test_transfer_call_returns_failure(self):
        """Test transfer_call returns failed status."""
        provider = _provider()
        res = await provider.transfer_call(
            destination="+15551234567",
            transfer_id="transfer_123",
            conference_name="conf_123",
        )
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["provider"], "whatsapp")

    async def test_get_call_cost_returns_default(self):
        """Test get_call_cost returns default 0 cost."""
        provider = _provider()
        cost = await provider.get_call_cost("call_123")
        self.assertEqual(cost["cost_usd"], 0.0)

    async def test_configure_inbound(self):
        """Test configure_inbound returns ok status."""
        provider = _provider()
        result = await provider.configure_inbound("+15551234567", "https://example.com/webhook")
        self.assertTrue(result.ok)

    async def test_can_handle_webhook(self):
        """Test webhook detection for whatsapp."""
        self.assertTrue(WhatsAppProvider.can_handle_webhook(
            {"object": "whatsapp_business_account"}, {}
        ))
        self.assertTrue(WhatsAppProvider.can_handle_webhook(
            {"entry": [{"changes": [{"field": "calls"}]}]}, {}
        ))
        self.assertTrue(WhatsAppProvider.can_handle_webhook(
            {"entry": [{"changes": [{"value": {"messaging_product": "whatsapp"}}]}]}, {}
        ))
        self.assertFalse(WhatsAppProvider.can_handle_webhook(
            {"entry": [{"changes": []}]}, {}
        ))
        self.assertFalse(WhatsAppProvider.can_handle_webhook(
            {"entry": [{"changes": [{"field": "feed"}]}]}, {}
        ))
        self.assertFalse(WhatsAppProvider.can_handle_webhook(
            {"something_else": True}, {}
        ))

    async def test_validate_account_id(self):
        """Test matching inbound webhook by phone_number_id."""
        config = {"phone_number_id": "phone_123"}
        self.assertTrue(WhatsAppProvider.validate_account_id(config, "phone_123"))
        self.assertFalse(WhatsAppProvider.validate_account_id(config, "other_id"))
        self.assertFalse(WhatsAppProvider.validate_account_id(config, ""))

    async def test_parse_inbound_webhook(self):
        """Test parsing inbound webhook payload into NormalizedInboundData."""
        whatsapp_webhook = {
            "entry": [{
                "changes": [{
                    "field": "calls",
                    "value": {
                        "metadata": {
                            "display_phone_number": "+15551234567",
                            "phone_number_id": "test_phone_id",
                        },
                        "call": {
                            "id": "call_id_123",
                            "direction": "inbound",
                            "from": "+15559876543",
                            "to": "+15551234567",
                            "status": "ringing",
                        },
                    },
                }],
            }],
        }
        res = WhatsAppProvider.parse_inbound_webhook(whatsapp_webhook)
        self.assertEqual(res.provider, "whatsapp")
        self.assertEqual(res.call_id, "call_id_123")
        self.assertEqual(res.from_number, "+15559876543")
        self.assertEqual(res.to_number, "+15551234567")
        self.assertEqual(res.direction, "inbound")
        self.assertEqual(res.call_status, "ringing")
        self.assertEqual(res.account_id, "test_phone_id")

        # Also test with 'calls' array format
        calls_array_webhook = {
            "entry": [{
                "changes": [{
                    "field": "calls",
                    "value": {
                        "metadata": {
                            "display_phone_number": "+15551234567",
                            "phone_number_id": "test_phone_id",
                        },
                        "calls": [{
                            "id": "call_id_999",
                            "direction": "inbound",
                            "from": "+15559876543",
                            "to": "+15551234567",
                            "status": "ringing",
                        }],
                    },
                }],
            }],
        }
        res_arr = WhatsAppProvider.parse_inbound_webhook(calls_array_webhook)
        self.assertEqual(res_arr.provider, "whatsapp")
        self.assertEqual(res_arr.call_id, "call_id_999")

    async def test_normalize_inbound_data_converts_whatsapp_webhook_to_standard_format(self):
        """Test WhatsApp webhook payloads convert to NormalizedInboundData."""
        provider = _provider()
        whatsapp_webhook = {
            "entry": [{
                "changes": [{
                    "field": "calls",
                    "value": {
                        "display_phone_number": "+15551234567",
                        "call": {
                            "id": "call_id_123",
                            "direction": "inbound",
                            "from": "+15559876543",
                            "to": "+15551234567",
                            "status": "ringing",
                        },
                    },
                }],
            }],
        }
        result = provider.normalize_inbound_data(whatsapp_webhook)
        self.assertEqual(result.provider, "whatsapp")
        self.assertEqual(result.call_id, "call_id_123")
        self.assertEqual(result.from_number, "+15559876543")
        self.assertEqual(result.to_number, "+15551234567")
        self.assertEqual(result.direction, "inbound")
        self.assertEqual(result.call_status, "ringing")

    async def test_normalize_inbound_data_handles_missing_call_id(self):
        """Test normalization fails when call_id is missing."""
        provider = _provider()
        whatsapp_webhook = {
            "entry": [{
                "changes": [{
                    "field": "calls",
                    "value": {
                        "call": {
                            "direction": "inbound",
                            "from": "+15559876543",
                        },
                    },
                }],
            }],
        }
        with self.assertRaisesRegex(ValueError, "Missing call_id"):
            provider.normalize_inbound_data(whatsapp_webhook)

    async def test_normalize_inbound_data_handles_invalid_structure(self):
        """Test normalization fails with invalid webhook structure."""
        provider = _provider()
        invalid_webhook = {"invalid": "structure"}
        with self.assertRaisesRegex(ValueError, "Invalid webhook data structure"):
            provider.normalize_inbound_data(invalid_webhook)

    async def test_verify_inbound_signature(self):
        """Test inbound signature verification with HMAC-SHA256."""
        secret = "my_app_secret"
        provider = _provider(app_secret=secret)
        body = '{"entry": []}'
        expected_sig = hmac.new(
            secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()

        # Valid signature
        headers = {"x-hub-signature-256": f"sha256={expected_sig}"}
        self.assertTrue(
            await provider.verify_inbound_signature(
                url="https://example.com/webhook",
                webhook_data={},
                headers=headers,
                body=body,
            )
        )

        # Invalid signature
        bad_headers = {"x-hub-signature-256": "sha256=invalid_digest"}
        self.assertFalse(
            await provider.verify_inbound_signature(
                url="https://example.com/webhook",
                webhook_data={},
                headers=bad_headers,
                body=body,
            )
        )

        # Missing header
        self.assertFalse(
            await provider.verify_inbound_signature(
                url="https://example.com/webhook",
                webhook_data={},
                headers={},
                body=body,
            )
        )

    def test_map_whatsapp_status_to_dograh(self):
        """Test WhatsApp call statuses map correctly to Dograh TelephonyCallStatus."""
        provider = _provider()
        status_mappings = [
            ("queued", TelephonyCallStatus.INITIATED),
            ("ringing", TelephonyCallStatus.RINGING),
            ("in-progress", TelephonyCallStatus.IN_PROGRESS),
            ("answered", TelephonyCallStatus.ANSWERED),
            ("completed", TelephonyCallStatus.COMPLETED),
            ("failed", TelephonyCallStatus.FAILED),
            ("busy", TelephonyCallStatus.BUSY),
            ("no-answer", TelephonyCallStatus.NO_ANSWER),
            ("canceled", TelephonyCallStatus.CANCELED),
            ("permission_requested", TelephonyCallStatus.INITIATED),
            ("permission_denied", TelephonyCallStatus.FAILED),
        ]
        for whatsapp_status, expected_dograh_status in status_mappings:
            result = provider._map_whatsapp_status_to_dograh(whatsapp_status)
            self.assertEqual(result, expected_dograh_status, f"Failed for {whatsapp_status}")

    def test_map_whatsapp_status_unknown_maps_to_error(self):
        """Test unknown WhatsApp status maps to ERROR."""
        provider = _provider()
        result = provider._map_whatsapp_status_to_dograh("unknown_status")
        self.assertEqual(result, TelephonyCallStatus.ERROR)

    def test_generate_error_response(self):
        """Test generating error response."""
        res = WhatsAppProvider.generate_error_response("ERR", "Something failed")
        self.assertIsInstance(res, Response)
        self.assertEqual(res.media_type, "application/json")

    def test_generate_validation_error_response(self):
        """Test generating validation error response."""
        res = WhatsAppProvider.generate_validation_error_response("AUTH_FAILED")
        self.assertIsInstance(res, Response)
        self.assertEqual(res.media_type, "application/json")
        body = json.loads(res.body.decode())
        self.assertEqual(body["error"], "AUTH_FAILED")
        self.assertIn("message", body)

    async def test_get_available_phone_numbers_direct(self):
        """Test fetching available phone numbers via direct phone number query."""
        provider = _provider()

        class DummyResponse:
            def __init__(self, status, payload):
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

        class DummySession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            def get(self, url, **kwargs):
                if url.endswith("/test_phone_id"):
                    return DummyResponse(200, {
                        "display_phone_number": "+1 555-123-4567",
                        "verified_name": "Test Business",
                    })
                return DummyResponse(404, {})

        with patch("aiohttp.ClientSession", return_value=DummySession()):
            numbers = await provider.get_available_phone_numbers()
            self.assertEqual(numbers, ["+15551234567"])

    async def test_get_available_phone_numbers_fallback_edge(self):
        """Test fetching numbers from /phone_numbers edge when direct query has no number."""
        provider = _provider()

        class DummyResponse:
            def __init__(self, status, payload):
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

        class DummySession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                pass

            def get(self, url, **kwargs):
                if url.endswith("/test_phone_id"):
                    return DummyResponse(400, {"error": "Not a phone number ID"})
                elif "test_phone_id/phone_numbers" in url:
                    return DummyResponse(200, {
                        "data": [
                            {"display_phone_number": "+1 555-987-6543"},
                        ]
                    })
                return DummyResponse(404, {})

        with patch("aiohttp.ClientSession", return_value=DummySession()):
            numbers = await provider.get_available_phone_numbers()
            self.assertEqual(numbers, ["+15559876543"])

    async def test_validate_phone_number_success(self):
        """Test validate_phone_number returns ok when number is owned."""
        provider = _provider()
        with patch.object(
            provider, "get_available_phone_numbers", AsyncMock(return_value=["+15551234567"])
        ):
            res = await provider.validate_phone_number("+1 (555) 123-4567")
            self.assertTrue(res.ok)

    async def test_validate_phone_number_not_owned(self):
        """Test validate_phone_number returns ok=False when number is not owned."""
        provider = _provider()
        with patch.object(
            provider, "get_available_phone_numbers", AsyncMock(return_value=["+15551234567"])
        ):
            res = await provider.validate_phone_number("+15559999999")
            self.assertFalse(res.ok)

    async def test_create_whatsapp_transport_success(self):
        """Test create_transport constructs a valid FastAPIWebsocketTransport with 16 kHz audio config."""
        from api.services.pipecat.audio_config import AudioConfig
        from api.services.telephony.providers.whatsapp.transport import create_transport

        mock_websocket = MagicMock()
        audio_config = AudioConfig(
            transport_in_sample_rate=16000,
            transport_out_sample_rate=16000,
            pipeline_sample_rate=16000,
        )
        mock_credentials = {
            "access_token": "valid_token",
            "phone_number_id": "12345",
        }

        with patch(
            "api.services.telephony.providers.whatsapp.transport.load_credentials_for_transport",
            AsyncMock(return_value=mock_credentials),
        ), patch(
            "api.services.telephony.providers.whatsapp.transport.build_audio_out_mixer",
            AsyncMock(return_value=None),
        ):
            transport = await create_transport(
                websocket=mock_websocket,
                workflow_run_id=101,
                audio_config=audio_config,
                organization_id=10,
                call_id="call_test_123",
            )
            self.assertIsNotNone(transport)
            self.assertEqual(transport._params.audio_in_sample_rate, 16000)
            self.assertEqual(transport._params.audio_out_sample_rate, 16000)


class TestWhatsAppConfigurationDisplayAndMerge(IsolatedAsyncioTestCase):
    """Test credential masking and edit-merge preservation for WhatsApp configurations."""

    def setUp(self):
        from api.routes.organization import _credentials_for_display, preserve_masked_fields
        self._credentials_for_display = _credentials_for_display
        self.preserve_masked_fields = preserve_masked_fields

        self.stored = {
            "provider": "whatsapp",
            "phone_number_id": "106540352242922",
            "access_token": "secret_access_token_value",
            "app_secret": "secret_app_secret_value",
            "webhook_verify_token": "secret_verify_token_value",
            "business_initiated_calls_enabled": True,
            "call_icon_visibility": "business_hours",
        }

    def test_credentials_for_display_masks_secrets_without_dropping_them(self):
        """Display credentials must include access_token and app_secret in masked form."""
        displayed = self._credentials_for_display("whatsapp", self.stored)

        self.assertEqual(displayed["provider"], "whatsapp")
        self.assertEqual(displayed["phone_number_id"], "106540352242922")
        self.assertTrue(displayed["business_initiated_calls_enabled"])
        self.assertEqual(displayed["call_icon_visibility"], "business_hours")

        # Secrets must be present and masked (not equal to original secret)
        self.assertIn("access_token", displayed)
        self.assertIn("app_secret", displayed)
        self.assertIn("webhook_verify_token", displayed)
        self.assertNotEqual(displayed["access_token"], "secret_access_token_value")
        self.assertNotEqual(displayed["app_secret"], "secret_app_secret_value")
        self.assertNotEqual(displayed["webhook_verify_token"], "secret_verify_token_value")

    def test_preserve_masked_fields_restores_stored_secrets_when_masked(self):
        """When UI submits masked secrets back, stored unmasked values are restored."""
        from api.routes.organization import _get_model_fields_set_paths
        from api.services.telephony.providers.whatsapp.config import WhatsAppConfigurationRequest

        displayed = self._credentials_for_display("whatsapp", self.stored)
        req = WhatsAppConfigurationRequest.model_validate(displayed)
        request_dict = req.model_dump()
        fields_set = _get_model_fields_set_paths(req)

        self.preserve_masked_fields("whatsapp", request_dict, self.stored, fields_set=fields_set)

        self.assertEqual(request_dict["access_token"], "secret_access_token_value")
        self.assertEqual(request_dict["app_secret"], "secret_app_secret_value")
        self.assertEqual(request_dict["webhook_verify_token"], "secret_verify_token_value")

        # The round-trip has to carry the non-sensitive settings too. The save
        # path replaces credentials wholesale, so a flag that GET drops (or that
        # the response schema stops declaring) would come back as the request
        # schema's default and silently overwrite what is stored.
        self.assertIs(request_dict["business_initiated_calls_enabled"], True)
        self.assertEqual(request_dict["call_icon_visibility"], "business_hours")

    def test_preserve_masked_fields_accepts_new_secrets_when_updated(self):
        """When user provides a new real secret, it is not overwritten by existing value."""
        from api.routes.organization import _get_model_fields_set_paths
        from api.services.telephony.providers.whatsapp.config import WhatsAppConfigurationRequest

        req = WhatsAppConfigurationRequest.model_validate({
            "phone_number_id": "106540352242922",
            "access_token": "new_rotated_access_token",
            "app_secret": "new_rotated_app_secret",
            "webhook_verify_token": "new_rotated_verify_token",
        })
        request_dict = req.model_dump()
        fields_set = _get_model_fields_set_paths(req)

        self.preserve_masked_fields("whatsapp", request_dict, self.stored, fields_set=fields_set)

        self.assertEqual(request_dict["access_token"], "new_rotated_access_token")
        self.assertEqual(request_dict["app_secret"], "new_rotated_app_secret")
        self.assertEqual(request_dict["webhook_verify_token"], "new_rotated_verify_token")

    def test_preserve_masked_fields_allows_clearing_optional_secrets(self):
        """When an optional sensitive credential is set to None or empty, it is not restored."""
        from api.routes.organization import _get_model_fields_set_paths
        from api.services.configuration.masking import mask_key
        from api.services.telephony.providers.vonage.config import VonageConfigurationRequest

        stored_vonage = {
            "api_key": "k1",
            "api_secret": "secret_key_12345",
            "application_id": "app1",
            "private_key": "private_key_data_here",
            "signature_secret": "existing_sig_secret",
        }
        req = VonageConfigurationRequest.model_validate({
            "api_key": "k1",
            "api_secret": mask_key(stored_vonage["api_secret"]),
            "application_id": "app1",
            "private_key": mask_key(stored_vonage["private_key"]),
            "signature_secret": None,  # user explicitly cleared
        })
        request_dict = req.model_dump()
        fields_set = _get_model_fields_set_paths(req)
        self.preserve_masked_fields("vonage", request_dict, stored_vonage, fields_set=fields_set)
        self.assertEqual(request_dict["api_secret"], "secret_key_12345")
        self.assertEqual(request_dict["private_key"], "private_key_data_here")
        self.assertIsNone(request_dict["signature_secret"])

    def test_whatsapp_configuration_request_requires_fields(self):
        """WhatsAppConfigurationRequest enforces all required credential fields directly."""
        from api.services.telephony.providers.whatsapp.config import WhatsAppConfigurationRequest
        from pydantic import ValidationError

        with self.assertRaises(ValidationError) as ctx:
            WhatsAppConfigurationRequest(phone_number_id="12345")
        errors = {e["loc"][0] for e in ctx.exception.errors()}
        self.assertIn("access_token", errors)
        self.assertIn("app_secret", errors)
        self.assertIn("webhook_verify_token", errors)

    def test_whatsapp_configuration_request_accepts_masked_values_for_update(self):
        """WhatsAppConfigurationRequest accepts masked values populated by edit dialog."""
        from api.routes.organization import _get_model_fields_set_paths
        from api.services.telephony.providers.whatsapp.config import WhatsAppConfigurationRequest

        displayed = self._credentials_for_display("whatsapp", self.stored)
        req = WhatsAppConfigurationRequest(**displayed)
        self.assertTrue(req.access_token.startswith("****"))

        req_dict = req.model_dump()
        fields_set = _get_model_fields_set_paths(req)
        self.preserve_masked_fields("whatsapp", req_dict, self.stored, fields_set=fields_set)
        self.assertEqual(req_dict["access_token"], "secret_access_token_value")
        self.assertEqual(req_dict["app_secret"], "secret_app_secret_value")

    def test_preserve_masked_fields_preserves_omitted_secrets_on_partial_update(self):
        """When an update omits an optional sensitive field, stored value is preserved."""
        from api.routes.organization import _get_model_fields_set_paths
        from api.services.configuration.masking import mask_key
        from api.services.telephony.providers.vonage.config import VonageConfigurationRequest

        stored_vonage = {
            "api_key": "k1",
            "api_secret": "secret_key_12345",
            "application_id": "app1",
            "private_key": "private_key_data_here",
            "signature_secret": "existing_sig_secret",
        }
        # Update payload omits signature_secret
        req = VonageConfigurationRequest.model_validate({
            "api_key": "k1",
            "api_secret": mask_key(stored_vonage["api_secret"]),
            "application_id": "app1",
            "private_key": mask_key(stored_vonage["private_key"]),
        })
        fields_set = _get_model_fields_set_paths(req)
        request_dict = req.model_dump()
        self.assertNotIn("signature_secret", fields_set)
        self.assertIsNone(request_dict["signature_secret"])

        self.preserve_masked_fields("vonage", request_dict, stored_vonage, fields_set=fields_set)
        self.assertEqual(request_dict["signature_secret"], "existing_sig_secret")
        self.assertEqual(request_dict["api_secret"], "secret_key_12345")

    def test_preserve_masked_fields_allows_explicit_null_clearing_with_fields_set(self):
        """When signature_secret is explicitly set to None, it is not restored."""
        from api.routes.organization import _get_model_fields_set_paths
        from api.services.configuration.masking import mask_key
        from api.services.telephony.providers.vonage.config import VonageConfigurationRequest

        stored_vonage = {
            "api_key": "k1",
            "api_secret": "secret_key_12345",
            "application_id": "app1",
            "private_key": "private_key_data_here",
            "signature_secret": "existing_sig_secret",
        }
        req = VonageConfigurationRequest.model_validate({
            "api_key": "k1",
            "api_secret": mask_key(stored_vonage["api_secret"]),
            "application_id": "app1",
            "private_key": mask_key(stored_vonage["private_key"]),
            "signature_secret": None,
        })
        fields_set = _get_model_fields_set_paths(req)
        request_dict = req.model_dump()
        self.assertIn("signature_secret", fields_set)

        self.preserve_masked_fields("vonage", request_dict, stored_vonage, fields_set=fields_set)
        self.assertIsNone(request_dict["signature_secret"])
        self.assertEqual(request_dict["api_secret"], "secret_key_12345")


