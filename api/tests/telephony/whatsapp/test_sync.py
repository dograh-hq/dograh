"""Tests for WhatsApp phone number sync and configuration deduplication."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from api.db import db_client
from api.services.telephony.phone_number_sync import (
    sync_available_phone_numbers_for_config,
)


class TestWhatsAppConfigDeduplication(IsolatedAsyncioTestCase):
    async def test_get_whatsapp_config_multiple_phone_rows_same_config_deduplicated(self):
        """When multiple phone rows on the SAME config carry the phone_number_id, it is NOT ambiguous."""
        mock_config = MagicMock()
        mock_config.id = 10

        mock_session = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        res1 = MagicMock()
        res1.scalars.return_value.all.return_value = []
        res2 = MagicMock()
        res2.scalars.return_value.all.return_value = [mock_config, mock_config]

        mock_session.execute = AsyncMock(side_effect=[res1, res2])

        with patch.object(db_client, "async_session", return_value=mock_session_ctx):
            config = await db_client.get_whatsapp_configuration_by_phone_number_id("12345")
            self.assertIsNotNone(config)
            self.assertEqual(config.id, 10)

    async def test_get_whatsapp_config_multiple_different_configs_rejected_as_ambiguous(self):
        """When multiple DIFFERENT configs carry the phone_number_id, it IS rejected as ambiguous."""
        mock_config_1 = MagicMock()
        mock_config_1.id = 10
        mock_config_2 = MagicMock()
        mock_config_2.id = 20

        mock_session = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__.return_value = mock_session
        mock_session_ctx.__aexit__.return_value = None

        res1 = MagicMock()
        res1.scalars.return_value.all.return_value = []
        res2 = MagicMock()
        res2.scalars.return_value.all.return_value = [mock_config_1, mock_config_2]

        mock_session.execute = AsyncMock(side_effect=[res1, res2])

        with patch.object(db_client, "async_session", return_value=mock_session_ctx):
            config = await db_client.get_whatsapp_configuration_by_phone_number_id("12345")
            self.assertIsNone(config)


class TestPhoneNumberSyncLifecycle(IsolatedAsyncioTestCase):
    async def test_sync_handles_unparseable_address_without_crashing(self):
        """Unparseable addresses returned by provider are skipped, and do not crash building discovered set."""
        mock_provider = MagicMock()
        mock_provider.is_full_inventory = True
        mock_provider.get_available_phone_number_records = AsyncMock(
            return_value=[
                {"address": "   "},
                {"address": "+15551234567"},
            ]
        )

        with patch(
            "api.services.telephony.phone_number_sync.get_telephony_provider_by_id",
            AsyncMock(return_value=mock_provider),
        ), patch.object(
            db_client, "list_phone_numbers_for_config", AsyncMock(return_value=[])
        ), patch.object(
            db_client,
            "get_telephony_configuration",
            AsyncMock(return_value=MagicMock(provider="whatsapp", credentials={})),
        ), patch(
            "api.services.telephony.phone_number_sync.assert_no_inbound_routing_conflict",
            AsyncMock(),
        ), patch.object(
            db_client, "create_phone_number", AsyncMock()
        ) as mock_create:
            status = await sync_available_phone_numbers_for_config(1, 100)
            self.assertTrue(status.ok)
            self.assertIn("Imported 1 phone number(s)", status.message)
            self.assertIn("Skipped 1 duplicate or invalid", status.message)
            mock_create.assert_awaited_once()

    async def test_sync_reactivates_inactive_returned_row(self):
        """When an inactive number reappears in provider results, it is reactivated."""
        inactive_row = MagicMock()
        inactive_row.id = 55
        inactive_row.address_normalized = "+15551234567"
        inactive_row.is_active = False
        inactive_row.extra_metadata = {}

        mock_provider = MagicMock()
        mock_provider.get_available_phone_number_records = AsyncMock(
            return_value=[{"address": "+15551234567"}]
        )

        with patch(
            "api.services.telephony.phone_number_sync.get_telephony_provider_by_id",
            AsyncMock(return_value=mock_provider),
        ), patch.object(
            db_client,
            "list_phone_numbers_for_config",
            AsyncMock(return_value=[inactive_row]),
        ), patch.object(
            db_client,
            "get_telephony_configuration",
            AsyncMock(return_value=MagicMock(provider="whatsapp", credentials={})),
        ), patch.object(
            db_client, "update_phone_number", AsyncMock()
        ) as mock_update:
            status = await sync_available_phone_numbers_for_config(1, 100)
            self.assertTrue(status.ok)
            mock_update.assert_awaited_once_with(
                phone_number_id=55,
                telephony_configuration_id=1,
                is_active=True,
            )

    async def test_sync_deactivates_stale_row_and_clears_default_caller_id(self):
        """When a stale row with default caller ID is deactivated, is_default_caller_id is cleared."""
        stale_default_row = MagicMock()
        stale_default_row.id = 77
        stale_default_row.address_normalized = "+15559999999"
        stale_default_row.is_active = True
        stale_default_row.is_default_caller_id = True

        mock_provider = MagicMock()
        mock_provider.get_available_phone_number_records = AsyncMock(
            return_value=[{"address": "+15551234567"}]
        )

        with patch(
            "api.services.telephony.phone_number_sync.get_telephony_provider_by_id",
            AsyncMock(return_value=mock_provider),
        ), patch.object(
            db_client,
            "list_phone_numbers_for_config",
            AsyncMock(return_value=[stale_default_row]),
        ), patch.object(
            db_client,
            "get_telephony_configuration",
            AsyncMock(return_value=MagicMock(provider="whatsapp", credentials={})),
        ), patch(
            "api.services.telephony.phone_number_sync.assert_no_inbound_routing_conflict",
            AsyncMock(),
        ), patch.object(
            db_client, "create_phone_number", AsyncMock()
        ), patch.object(
            db_client, "update_phone_number", AsyncMock()
        ) as mock_update:
            status = await sync_available_phone_numbers_for_config(1, 100)
            self.assertTrue(status.ok)
            mock_update.assert_awaited_once_with(
                phone_number_id=77,
                telephony_configuration_id=1,
                is_active=False,
                is_default_caller_id=False,
            )

    async def test_sync_does_not_deactivate_when_is_full_inventory_false(self):
        """When provider discovery is partial (is_full_inventory=False), valid local numbers are NOT deactivated."""
        active_row = MagicMock()
        active_row.id = 88
        active_row.address_normalized = "+15559999999"
        active_row.is_active = True
        active_row.is_default_caller_id = False

        mock_provider = MagicMock()
        mock_provider.is_full_inventory = False
        mock_provider.get_available_phone_number_records = AsyncMock(
            return_value=[{"address": "+15551234567"}]
        )

        with patch(
            "api.services.telephony.phone_number_sync.get_telephony_provider_by_id",
            AsyncMock(return_value=mock_provider),
        ), patch.object(
            db_client,
            "list_phone_numbers_for_config",
            AsyncMock(return_value=[active_row]),
        ), patch.object(
            db_client,
            "get_telephony_configuration",
            AsyncMock(return_value=MagicMock(provider="whatsapp", credentials={})),
        ), patch(
            "api.services.telephony.phone_number_sync.assert_no_inbound_routing_conflict",
            AsyncMock(),
        ), patch.object(
            db_client, "create_phone_number", AsyncMock()
        ), patch.object(
            db_client, "update_phone_number", AsyncMock()
        ) as mock_update:
            status = await sync_available_phone_numbers_for_config(1, 100)
            self.assertTrue(status.ok)
            # update_phone_number must NOT be called for deactivation
            mock_update.assert_not_called()


class TestWhatsAppWABADiscovery(IsolatedAsyncioTestCase):
    async def test_whatsapp_provider_discovery_queries_waba_when_present(self):
        """When direct lookup returns whatsapp_business_account, provider queries WABA /phone_numbers edge."""
        import json
        from api.services.telephony.providers.whatsapp.provider import WhatsAppProvider

        provider = WhatsAppProvider({
            "access_token": "test_token",
            "phone_number_id": "direct_phone_id",
            "webhook_verify_token": "test_verify_token",
            "app_secret": "test_secret",
        })

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
                if url.endswith("/direct_phone_id"):
                    return DummyResponse(200, {
                        "display_phone_number": "+1 555-123-4567",
                        "verified_name": "Test Business",
                        "id": "direct_phone_id",
                        "whatsapp_business_account": {"id": "waba_999"},
                    })
                elif "waba_999/phone_numbers" in url:
                    return DummyResponse(200, {
                        "data": [
                            {"display_phone_number": "+1 555-123-4567", "id": "direct_phone_id"},
                            {"display_phone_number": "+1 555-987-6543", "id": "second_phone_id"},
                        ]
                    })
                return DummyResponse(404, {})

        with patch("aiohttp.ClientSession", return_value=DummySession()):
            records = await provider.get_available_phone_number_records()
            addresses = [r["address"] for r in records]
            self.assertEqual(len(records), 2)
            self.assertIn("+15551234567", addresses)
            self.assertIn("+15559876543", addresses)
            self.assertTrue(provider.is_full_inventory)

