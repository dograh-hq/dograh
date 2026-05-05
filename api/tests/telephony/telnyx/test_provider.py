"""Tests for the Telnyx telephony provider."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.telephony.providers.telnyx.provider import TelnyxProvider


class TestTelnyxProvider:
    """Test suite for TelnyxProvider."""

    @pytest.fixture
    def valid_config(self):
        return {
            "api_key": "KEY0123456789ABCDEF0123456789AB",
            "connection_id": "conn_1234567890",
            "from_numbers": ["+14155551234"],
        }

    @pytest.fixture
    def provider(self, valid_config):
        return TelnyxProvider(valid_config)

    # ------------------------------------------------------------------
    # validate_config
    # ------------------------------------------------------------------

    def test_validate_config_passes(self, provider):
        assert provider.validate_config() is True

    def test_validate_config_missing_api_key(self, valid_config):
        cfg = {**valid_config, "api_key": ""}
        provider = TelnyxProvider(cfg)
        assert provider.validate_config() is False

    def test_validate_config_missing_connection_id(self, valid_config):
        cfg = {**valid_config, "connection_id": ""}
        provider = TelnyxProvider(cfg)
        assert provider.validate_config() is False

    def test_validate_config_missing_from_numbers(self, valid_config):
        cfg = {**valid_config, "from_numbers": []}
        provider = TelnyxProvider(cfg)
        assert provider.validate_config() is False

    def test_validate_config_warns_on_bad_prefix(self, valid_config, caplog):
        cfg = {**valid_config, "api_key": "sk-12345"}
        provider = TelnyxProvider(cfg)
        with caplog.at_level("WARNING"):
            provider.validate_config()
        assert "does not start with 'KEY'" in caplog.text

    # ------------------------------------------------------------------
    # initiate_call payload construction
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_initiate_call_payload(self, provider):
        with patch(
            "api.services.telephony.providers.telnyx.provider.get_backend_endpoints",
            new_callable=AsyncMock,
            return_value=("https://api.example.com", "wss://ws.example.com"),
        ):
            with patch("aiohttp.ClientSession.post") as mock_post:
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.json = AsyncMock(
                    return_value={
                        "data": {
                            "call_control_id": "cc-123",
                            "call_leg_id": "leg-456",
                            "call_session_id": "sess-789",
                        }
                    }
                )
                mock_post.return_value.__aenter__ = AsyncMock(
                    return_value=mock_response
                )
                mock_post.return_value.__aexit__ = AsyncMock(return_value=False)

                result = await provider.initiate_call(
                    to_number="+14155555678",
                    webhook_url="https://example.com/webhook",
                    workflow_run_id=42,
                    workflow_id=1,
                    user_id=1,
                )

                assert result.call_id == "cc-123"
                assert result.status == "initiated"
                assert result.provider_metadata["call_session_id"] == "sess-789"

                call_args = mock_post.call_args
                payload = call_args.kwargs["json"]
                assert payload["to"] == "+14155555678"
                assert payload["from"] == "+14155551234"
                assert payload["connection_id"] == "conn_1234567890"
                assert "wss://ws.example.com" in payload["stream_url"]
                assert payload["stream_bidirectional_codec"] == "PCMU"

    # ------------------------------------------------------------------
    # parse_status_callback
    # ------------------------------------------------------------------

    def test_parse_status_callback_initiated(self, provider):
        data = {
            "data": {
                "event_type": "call.initiated",
                "payload": {
                    "call_control_id": "cc-123",
                    "from": "+14155551234",
                    "to": "+14155555678",
                    "direction": "outgoing",
                },
            }
        }
        parsed = provider.parse_status_callback(data)
        assert parsed["call_id"] == "cc-123"
        assert parsed["status"] == "initiated"
        assert parsed["direction"] == "outgoing"

    def test_parse_status_callback_hangup_busy(self, provider):
        data = {
            "data": {
                "event_type": "call.hangup",
                "payload": {
                    "call_control_id": "cc-123",
                    "hangup_cause": "busy",
                },
            }
        }
        parsed = provider.parse_status_callback(data)
        assert parsed["status"] == "busy"

    def test_parse_status_callback_hangup_no_answer(self, provider):
        data = {
            "data": {
                "event_type": "call.hangup",
                "payload": {
                    "call_control_id": "cc-123",
                    "hangup_cause": "no_answer",
                },
            }
        }
        parsed = provider.parse_status_callback(data)
        assert parsed["status"] == "no-answer"

    def test_parse_status_callback_streaming(self, provider):
        data = {
            "data": {
                "event_type": "streaming_started",
                "payload": {"call_control_id": "cc-123"},
            }
        }
        parsed = provider.parse_status_callback(data)
        assert parsed["status"] == "streaming-started"

    def test_parse_status_callback_underscore_to_dot(self, provider):
        data = {
            "data": {
                "event_type": "call_answered",
                "payload": {"call_control_id": "cc-123"},
            }
        }
        parsed = provider.parse_status_callback(data)
        assert parsed["status"] == "in-progress"

    # ------------------------------------------------------------------
    # verify_webhook_signature
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_verify_webhook_signature_absent(self, provider):
        result = await provider.verify_webhook_signature(
            "https://example.com", {}, ""
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_webhook_signature_no_library(self, provider):
        with patch.object(
            provider, "_verify_ed25519_nacl", return_value=None
        ), patch.object(
            provider, "_verify_ed25519_cryptography", return_value=None
        ):
            result = await provider.verify_webhook_signature(
                "https://example.com",
                {"_raw_body": "{}"},
                "dGVzdHNpZw==",
            )
            assert result is True

    @pytest.mark.asyncio
    async def test_verify_webhook_signature_nacl_valid(self, provider):
        nacl = pytest.importorskip("nacl.signing")

        signer = nacl.signing.SigningKey.generate()
        pubkey = signer.verify_key
        pubkey_b64 = base64.b64encode(bytes(pubkey)).decode()

        body = '{"test": true}'
        message = f"1234567890.{body}"
        sig = signer.sign(message.encode())
        sig_b64 = base64.b64encode(sig.signature).decode()

        result = await provider.verify_webhook_signature(
            "https://example.com",
            {
                "_raw_body": body,
                "telnyx_timestamp": "1234567890",
                "telnyx_public_key": pubkey_b64,
            },
            sig_b64,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_webhook_signature_nacl_invalid(self, provider):
        nacl = pytest.importorskip("nacl.signing")

        signer = nacl.signing.SigningKey.generate()
        pubkey = signer.verify_key
        pubkey_b64 = base64.b64encode(bytes(pubkey)).decode()

        bad_sig = b"x" * 64
        bad_sig_b64 = base64.b64encode(bad_sig).decode()

        result = await provider.verify_webhook_signature(
            "https://example.com",
            {
                "_raw_body": "{}",
                "telnyx_timestamp": "1234567890",
                "telnyx_public_key": pubkey_b64,
            },
            bad_sig_b64,
        )
        assert result is False

    # ------------------------------------------------------------------
    # get_call_status error handling
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_call_status_raises_http_exception(self, provider):
        """get_call_status raises HTTPException on non-200 response."""
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status = 404
            mock_response.json = AsyncMock(return_value={"errors": ["Not found"]})
            mock_get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get.return_value.__aexit__ = AsyncMock(return_value=False)

            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await provider.get_call_status("cc-123")
            assert exc_info.value.status_code == 404

    # ------------------------------------------------------------------
    # inbound webhook parsing
    # ------------------------------------------------------------------

    def test_parse_inbound_webhook(self, provider):
        webhook_data = {
            "data": {
                "event_type": "call.initiated",
                "payload": {
                    "call_control_id": "cc-inbound",
                    "from": "+14155555678",
                    "to": "+14155551234",
                    "direction": "incoming",
                    "connection_id": "conn_123",
                },
            }
        }
        normalized = TelnyxProvider.parse_inbound_webhook(webhook_data)
        assert normalized.call_id == "cc-inbound"
        assert normalized.from_number == "+14155555678"
        assert normalized.to_number == "+14155551234"
        assert normalized.direction == "inbound"
        assert normalized.provider == "telnyx"
        assert normalized.account_id == "conn_123"

    def test_parse_inbound_webhook_normalizes_direction(self, provider):
        webhook_data = {
            "data": {
                "event_type": "call.initiated",
                "payload": {
                    "call_control_id": "cc-1",
                    "from": "+14155555678",
                    "to": "+14155551234",
                    "direction": "incoming",
                },
            }
        }
        normalized = TelnyxProvider.parse_inbound_webhook(webhook_data)
        assert normalized.direction == "inbound"

    # ------------------------------------------------------------------
    # get_call_cost CDR lookup
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_call_cost_success(self, provider):
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(
                return_value={
                    "data": [
                        {
                            "attributes": {
                                "cost": 0.05,
                                "duration": 120,
                                "status": "completed",
                            }
                        }
                    ]
                }
            )
            mock_get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await provider.get_call_cost("leg-456")
            assert result["cost_usd"] == 0.05
            assert result["duration"] == 120
            assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_get_call_cost_404(self, provider):
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status = 404
            mock_response.json = AsyncMock(return_value={})
            mock_get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_get.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await provider.get_call_cost("leg-456")
            assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_get_call_cost_exception(self, provider):
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_get.side_effect = Exception("Network error")

            result = await provider.get_call_cost("leg-456")
            assert result["status"] == "error"
            assert "Network error" in result["raw_response"]["error"]

    # ------------------------------------------------------------------
    # validate_account_id
    # ------------------------------------------------------------------

    def test_validate_account_id_matches(self, provider):
        assert provider.validate_account_id(
            {"connection_id": "conn_123"}, "conn_123"
        )

    def test_validate_account_id_mismatch(self, provider):
        assert not provider.validate_account_id(
            {"connection_id": "conn_123"}, "conn_456"
        )

    def test_validate_account_id_empty_webhook(self, provider):
        assert not provider.validate_account_id({"connection_id": "conn_123"}, "")
TESTEOF