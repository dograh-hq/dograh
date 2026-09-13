"""Regression tests for WhatsApp webhook verification."""

from contextlib import asynccontextmanager
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
        with (
            patch(
                "api.services.telephony.providers.whatsapp.routes.WHATSAPP_WEBHOOK_VERIFY_TOKEN",
                "verify-me",
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_whatsapp_configuration_by_verify_token",
                AsyncMock(return_value=None),
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await handle_webhook_verification(
                    hub_mode="subscribe",
                    hub_verify_token="wrong-token",
                    hub_challenge="123456789",
                )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Invalid verification token")

    async def test_verify_token_lookup_refuses_an_ambiguous_match(self):
        """A token two tenants share identifies neither, so the lookup returns None.

        Meta's handshake carries only hub.verify_token - no app or phone-number
        id - so taking the first matching row would complete one tenant's
        subscription against another tenant's configuration.
        """
        from api.db.telephony_configuration_client import TelephonyConfigurationClient

        client = TelephonyConfigurationClient()
        mock_session = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [
            MagicMock(id=1),
            MagicMock(id=2),
        ]
        mock_session.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def fake_session():
            yield mock_session

        client.async_session = fake_session

        self.assertIsNone(
            await client.get_whatsapp_configuration_by_verify_token("shared-token")
        )

    async def test_whatsapp_webhook_verification_matches_db_token(self):
        mock_config = MagicMock()
        mock_config.id = 42
        with (
            patch(
                "api.services.telephony.providers.whatsapp.routes.WHATSAPP_WEBHOOK_VERIFY_TOKEN",
                "",
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_whatsapp_configuration_by_verify_token",
                AsyncMock(return_value=mock_config),
            ),
        ):
            response = await handle_webhook_verification(
                hub_mode="subscribe",
                hub_verify_token="db-verify-token",
                hub_challenge="challenge-from-db",
            )

        self.assertIsInstance(response, PlainTextResponse)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"challenge-from-db")

    def test_whatsapp_permission_routes_are_registered(self):
        paths = [route.path for route in router.routes if getattr(route, "path", None)]
        self.assertIn("/whatsapp/permissions/check", paths)
        self.assertIn("/whatsapp/permissions/request", paths)

    async def test_check_permission_restricted_country(self):
        from api.services.telephony.providers.whatsapp.routes import (
            check_whatsapp_permission,
        )

        mock_user = MagicMock(selected_organization_id=1)
        res = await check_whatsapp_permission(
            telephony_configuration_id=10,
            recipient_phone_number="+16502530000",
            current_user=mock_user,
        )
        self.assertFalse(res.can_call)
        self.assertTrue(res.restricted_country)
        self.assertEqual(res.status, "restricted_country")
        self.assertIn("United States and Canada", res.restriction_reason)

    async def test_check_permission_granted_db(self):
        from datetime import datetime, timedelta, timezone

        from api.services.telephony.providers.whatsapp.routes import (
            check_whatsapp_permission,
        )

        mock_user = MagicMock(selected_organization_id=1)
        mock_config = MagicMock(
            id=10,
            organization_id=1,
            provider="whatsapp",
            credentials={
                "phone_number_id": "test_phone_id",
                "access_token": "test_token",
            },
        )
        mock_perm = MagicMock(
            status="granted_temporary",
            permission_type="temporary",
            expires_at=datetime.now(timezone.utc) + timedelta(days=3),
            recipient_phone_number="+447123456789",
        )
        mock_client = MagicMock()
        mock_client.check_call_permission = AsyncMock(
            return_value={
                "permission": {
                    "status": "granted_temporary",
                    "expiration_time": int(
                        (datetime.now(timezone.utc) + timedelta(days=3)).timestamp()
                    ),
                },
                "actions": [{"action_name": "start_call", "can_perform_action": True}],
            }
        )
        with (
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_telephony_configuration_for_org",
                AsyncMock(return_value=mock_config),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_whatsapp_call_permission",
                AsyncMock(return_value=mock_perm),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.upsert_whatsapp_call_permission",
                AsyncMock(),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client",
                return_value=mock_client,
            ),
        ):
            res = await check_whatsapp_permission(
                telephony_configuration_id=10,
                recipient_phone_number="+447123456789",
                current_user=mock_user,
            )
            self.assertTrue(res.can_call)
            self.assertEqual(res.status, "granted_temporary")
            self.assertFalse(res.restricted_country)
            self.assertIsNotNone(res.hours_remaining)

    async def test_send_permission_request_success(self):
        from api.services.telephony.providers.whatsapp.routes import (
            WhatsAppPermissionRequestPayload,
            send_whatsapp_permission_request,
        )

        mock_user = MagicMock(selected_organization_id=1)
        mock_config = MagicMock(
            id=10,
            organization_id=1,
            provider="whatsapp",
            credentials={
                "phone_number_id": "test_phone_id",
                "access_token": "test_token",
                "business_initiated_calls_enabled": True,
            },
        )
        payload = WhatsAppPermissionRequestPayload(
            telephony_configuration_id=10,
            recipient_phone_number="+447123456789",
            body_text="May we call you?",
        )
        with (
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_telephony_configuration_for_org",
                AsyncMock(return_value=mock_config),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client"
            ) as mock_get_client,
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.upsert_whatsapp_call_permission",
                AsyncMock(),
            ),
        ):
            mock_client = MagicMock()
            mock_client.send_call_permission_request = AsyncMock(
                return_value={"messages": [{"id": "wamid.12345"}]}
            )
            mock_get_client.return_value = mock_client

            res = await send_whatsapp_permission_request(
                payload=payload,
                current_user=mock_user,
            )
            self.assertTrue(res["success"])
            self.assertEqual(res["status"], "pending")
            self.assertEqual(res["message_id"], "wamid.12345")
            self.assertEqual(res["hours_remaining"], 168.0)
            mock_client.send_call_permission_request.assert_called_once_with(
                to="447123456789",
                body_text="May we call you?",
            )

    async def test_send_permission_request_uses_config_default_message(self):
        from api.services.telephony.providers.whatsapp.routes import (
            WhatsAppPermissionRequestPayload,
            send_whatsapp_permission_request,
        )

        mock_user = MagicMock(selected_organization_id=1)
        mock_config = MagicMock(
            id=10,
            organization_id=1,
            provider="whatsapp",
            credentials={
                "phone_number_id": "test_phone_id",
                "access_token": "test_token",
                "business_initiated_calls_enabled": True,
                "default_permission_message": "Custom configured permission message from org",
            },
        )
        payload = WhatsAppPermissionRequestPayload(
            telephony_configuration_id=10,
            recipient_phone_number="+447123456789",
            body_text=None,
        )
        with (
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_telephony_configuration_for_org",
                AsyncMock(return_value=mock_config),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client"
            ) as mock_get_client,
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.upsert_whatsapp_call_permission",
                AsyncMock(),
            ),
        ):
            mock_client = MagicMock()
            mock_client.send_call_permission_request = AsyncMock(
                return_value={"messages": [{"id": "wamid.12345"}]}
            )
            mock_get_client.return_value = mock_client

            res = await send_whatsapp_permission_request(
                payload=payload,
                current_user=mock_user,
            )
            self.assertTrue(res["success"])
            mock_client.send_call_permission_request.assert_called_once_with(
                to="447123456789",
                body_text="Custom configured permission message from org",
            )

    async def test_send_permission_request_uses_global_crisp_default_message(self):
        from api.services.telephony.providers.whatsapp.config import (
            DEFAULT_WHATSAPP_PERMISSION_MESSAGE,
        )
        from api.services.telephony.providers.whatsapp.routes import (
            WhatsAppPermissionRequestPayload,
            send_whatsapp_permission_request,
        )

        mock_user = MagicMock(selected_organization_id=1)
        mock_config = MagicMock(
            id=10,
            organization_id=1,
            provider="whatsapp",
            credentials={
                "phone_number_id": "test_phone_id",
                "access_token": "test_token",
                "business_initiated_calls_enabled": True,
            },
        )
        payload = WhatsAppPermissionRequestPayload(
            telephony_configuration_id=10,
            recipient_phone_number="+447123456789",
            body_text=None,
        )
        with (
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_telephony_configuration_for_org",
                AsyncMock(return_value=mock_config),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client"
            ) as mock_get_client,
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.upsert_whatsapp_call_permission",
                AsyncMock(),
            ),
        ):
            mock_client = MagicMock()
            mock_client.send_call_permission_request = AsyncMock(
                return_value={"messages": [{"id": "wamid.12345"}]}
            )
            mock_get_client.return_value = mock_client

            res = await send_whatsapp_permission_request(
                payload=payload,
                current_user=mock_user,
            )
            self.assertTrue(res["success"])
            mock_client.send_call_permission_request.assert_called_once_with(
                to="447123456789",
                body_text=DEFAULT_WHATSAPP_PERMISSION_MESSAGE,
            )

    async def test_check_permission_token_expired(self):
        from api.services.telephony.providers.whatsapp.routes import (
            check_whatsapp_permission,
        )

        mock_user = MagicMock(selected_organization_id=1)
        mock_config = MagicMock(
            id=10,
            organization_id=1,
            provider="whatsapp",
            credentials={
                "phone_number_id": "test_phone_id",
                "access_token": "expired_token",
            },
        )
        mock_client = MagicMock()
        mock_client.check_call_permission = AsyncMock(
            side_effect=HTTPException(
                status_code=401,
                detail="Meta API Error (190): Error validating access token: Session has expired. The WhatsApp access token has expired or is invalid. Please generate a fresh token in Meta Business Manager and update your Telephony Configuration.",
            )
        )
        with (
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_telephony_configuration_for_org",
                AsyncMock(return_value=mock_config),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_whatsapp_call_permission",
                AsyncMock(return_value=None),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client",
                return_value=mock_client,
            ),
        ):
            res = await check_whatsapp_permission(
                telephony_configuration_id=10,
                recipient_phone_number="+447123456789",
                current_user=mock_user,
            )
            self.assertFalse(res.can_call)
            self.assertEqual(res.status, "token_expired")
            self.assertIn(
                "The WhatsApp access token has expired or is invalid",
                res.delivery_error,
            )

    async def test_send_permission_request_token_expired(self):
        from api.services.telephony.providers.whatsapp.routes import (
            WhatsAppPermissionRequestPayload,
            send_whatsapp_permission_request,
        )

        mock_user = MagicMock(selected_organization_id=1)
        mock_config = MagicMock(
            id=10,
            organization_id=1,
            provider="whatsapp",
            credentials={
                "phone_number_id": "test_phone_id",
                "access_token": "expired_token",
                "business_initiated_calls_enabled": True,
            },
        )
        payload = WhatsAppPermissionRequestPayload(
            telephony_configuration_id=10,
            recipient_phone_number="+447123456789",
            body_text="May we call you?",
        )
        mock_client = MagicMock()
        mock_client.send_call_permission_request = AsyncMock(
            side_effect=HTTPException(
                status_code=401,
                detail="Meta API Error (190): Error validating access token. The WhatsApp access token has expired or is invalid.",
            )
        )
        with (
            patch(
                "api.services.telephony.providers.whatsapp.routes.db_client.get_telephony_configuration_for_org",
                AsyncMock(return_value=mock_config),
            ),
            patch(
                "api.services.telephony.providers.whatsapp.routes._get_or_create_whatsapp_client",
                return_value=mock_client,
            ),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await send_whatsapp_permission_request(
                    payload=payload,
                    current_user=mock_user,
                )
            self.assertEqual(ctx.exception.status_code, 401)
            self.assertIn("access token has expired", ctx.exception.detail)

    def test_whatsapp_campaign_sync_permission_routes_registered(self):
        """Verify the provider package registers both sync-permissions and sync-whatsapp-permissions endpoints."""
        paths = [route.path for route in router.routes if getattr(route, "path", None)]
        self.assertIn("/whatsapp/campaigns/{campaign_id}/sync-permissions", paths)
        self.assertIn(
            "/whatsapp/campaigns/{campaign_id}/sync-whatsapp-permissions", paths
        )

    def test_campaign_routes_and_app_do_not_import_whatsapp_routes(self):
        """Verify repository directives:
        1. Keep registration import driven (app lifespan does not import or call install_whatsapp_pipeline_runner).
        2. Keep provider routes isolated (api.routes.campaign does not import whatsapp.routes).

        Checked against each module's parsed import statements rather than its
        source text: matching the dotted path as a substring passes for
        ``from api.services.telephony.providers.whatsapp import routes``, which
        is the same forbidden import written differently.
        """
        import ast
        import inspect

        import api.app as app_mod
        import api.routes.campaign as campaign_mod

        forbidden = "api.services.telephony.providers.whatsapp.routes"

        def imported_modules(module) -> set[str]:
            """Every module name imported anywhere in ``module``, however spelled."""
            names: set[str] = set()
            for node in ast.walk(ast.parse(inspect.getsource(module))):
                if isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    # `from x.y import z` can import module x.y.z or a name in x.y;
                    # record both so either spelling is caught.
                    names.add(node.module)
                    names.update(f"{node.module}.{alias.name}" for alias in node.names)
            return names

        app_imports = imported_modules(app_mod)
        self.assertNotIn(forbidden, app_imports)
        self.assertNotIn("install_whatsapp_pipeline_runner", inspect.getsource(app_mod))

        campaign_imports = imported_modules(campaign_mod)
        self.assertNotIn(forbidden, campaign_imports)
        self.assertNotIn("sync-whatsapp-permissions", inspect.getsource(campaign_mod))
