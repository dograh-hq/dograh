"""Regression tests for WhatsApp webhook verification."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

from api.services.telephony.providers.whatsapp.routes import (
    handle_webhook_verification,
    router,
)


class TestWhatsAppRoutes(IsolatedAsyncioTestCase):
    def test_whatsapp_webhook_route_path_is_webhook(self):
        paths = [route.path for route in router.routes if getattr(route, "path", None)]
        self.assertIn("/whatsapp/webhook", paths)

    async def test_whatsapp_webhook_verification_returns_plain_text_challenge(self):
        with patch(
            "api.services.telephony.providers.whatsapp.routes.WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            "verify-me",
        ):
            response = await handle_webhook_verification(
                hub_mode="subscribe",
                hub_verify_token="verify-me",
                hub_challenge="123456789",
            )

        self.assertIsInstance(response, PlainTextResponse)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"123456789")

    async def test_whatsapp_webhook_verification_rejects_bad_token(self):
        with patch(
            "api.services.telephony.providers.whatsapp.routes.WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            "verify-me",
        ), patch(
            "api.services.telephony.providers.whatsapp.routes.db_client.get_whatsapp_configuration_by_verify_token",
            AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_webhook_verification(
                    hub_mode="subscribe",
                    hub_verify_token="wrong-token",
                    hub_challenge="123456789",
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Invalid verification token")

    async def test_whatsapp_webhook_verification_matches_db_token(self):
        mock_config = MagicMock()
        mock_config.id = 42
        with patch(
            "api.services.telephony.providers.whatsapp.routes.WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            "",
        ), patch(
            "api.services.telephony.providers.whatsapp.routes.db_client.get_whatsapp_configuration_by_verify_token",
            AsyncMock(return_value=mock_config),
        ):
            response = await handle_webhook_verification(
                hub_mode="subscribe",
                hub_verify_token="db-verify-token",
                hub_challenge="challenge-from-db",
            )

        self.assertIsInstance(response, PlainTextResponse)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"challenge-from-db")
