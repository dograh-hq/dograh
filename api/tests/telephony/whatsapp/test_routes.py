"""Tests for Meta's webhook verification handshake."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

from api.db import db_client
from api.services.telephony.providers.whatsapp.routes import (
    handle_webhook_verification,
    router,
)


class TestWhatsAppWebhookVerification(IsolatedAsyncioTestCase):
    def test_webhook_route_path(self):
        paths = [route.path for route in router.routes if getattr(route, "path", None)]
        self.assertIn("/whatsapp/webhook", paths)

    async def test_echoes_challenge_for_a_configured_token(self):
        config = MagicMock()
        config.id = 42
        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_verify_token",
            AsyncMock(return_value=config),
        ) as lookup:
            response = await handle_webhook_verification(
                hub_mode="subscribe",
                hub_verify_token="org-token",
                hub_challenge="123456789",
            )

        lookup.assert_awaited_once_with("org-token")
        self.assertIsInstance(response, PlainTextResponse)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"123456789")

    async def test_rejects_an_unknown_token(self):
        with patch.object(
            db_client,
            "get_whatsapp_configuration_by_verify_token",
            AsyncMock(return_value=None),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_webhook_verification(
                    hub_mode="subscribe",
                    hub_verify_token="wrong-token",
                    hub_challenge="123456789",
                )

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_rejects_a_mode_other_than_subscribe(self):
        with self.assertRaises(HTTPException) as ctx:
            await handle_webhook_verification(
                hub_mode="unsubscribe",
                hub_verify_token="org-token",
                hub_challenge="123456789",
            )
        self.assertEqual(ctx.exception.status_code, 400)
