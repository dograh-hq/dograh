"""Tests for WhatsApp template listing routes."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from api.db.models import TelephonyConfigurationModel, UserModel
from api.routes.organization import (
    list_whatsapp_templates,
    list_whatsapp_templates_for_config,
)
from api.services.telephony.base import ProviderPhoneNumber


class TestWhatsAppTemplateRoutes(IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = MagicMock(spec=UserModel)
        self.user.id = 1
        self.user.selected_organization_id = 42

    async def test_list_templates_for_config_success(self):
        cfg = MagicMock(spec=TelephonyConfigurationModel)
        cfg.id = 10
        cfg.provider = "whatsapp"
        cfg.credentials = {"waba_id": "test_waba_123"}

        mock_provider = MagicMock()
        mock_provider.list_message_templates = AsyncMock(
            return_value=[
                {
                    "id": "tpl_1",
                    "name": "welcome_message",
                    "status": "APPROVED",
                    "category": "MARKETING",
                    "language": "en_US",
                    "body_text": "Hello {{1}}",
                    "components": [],
                    "variables": [
                        {
                            "index": 1,
                            "name": "variable_1",
                            "example": "Bob",
                            "component": "body",
                        }
                    ],
                }
            ]
        )
        mock_provider.list_provider_phone_numbers = AsyncMock(
            return_value=[
                ProviderPhoneNumber(
                    address="+15551234",
                    external_id="pid_1",
                    label="Support",
                    metadata={"phone_number_id": "pid_1", "verified_name": "Support"},
                )
            ]
        )

        with (
            patch(
                "api.routes.organization._ensure_config_belongs_to_org",
                AsyncMock(return_value=cfg),
            ),
            patch(
                "api.routes.organization.get_telephony_provider_by_id",
                AsyncMock(return_value=mock_provider),
            ),
            patch(
                "api.routes.organization.db_client.list_phone_numbers_for_config",
                AsyncMock(return_value=[]),
            ),
        ):
            res = await list_whatsapp_templates_for_config(
                config_id=10, user=self.user
            )

        self.assertEqual(res.config_id, 10)
        self.assertEqual(res.waba_id, "test_waba_123")
        self.assertEqual(len(res.templates), 1)
        self.assertEqual(res.templates[0].id, "tpl_1")
        self.assertEqual(res.templates[0].name, "welcome_message")
        self.assertEqual(res.templates[0].variables[0].name, "variable_1")
        self.assertEqual(len(res.phone_numbers), 1)
        self.assertEqual(res.phone_numbers[0].phone_number_id, "pid_1")
        self.assertEqual(res.phone_numbers[0].display_phone_number, "+15551234")
        self.assertEqual(res.phone_numbers[0].verified_name, "Support")

    async def test_list_templates_for_config_wrong_provider_raises(self):
        cfg = MagicMock(spec=TelephonyConfigurationModel)
        cfg.id = 10
        cfg.provider = "twilio"

        with (
            patch(
                "api.routes.organization._ensure_config_belongs_to_org",
                AsyncMock(return_value=cfg),
            ),
            self.assertRaises(HTTPException) as ctx,
        ):
            await list_whatsapp_templates_for_config(
                config_id=10, user=self.user
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not a WhatsApp provider", ctx.exception.detail)

    async def test_list_templates_no_config_returns_empty(self):
        with patch(
            "api.routes.organization.db_client.list_telephony_configurations_by_provider",
            AsyncMock(return_value=[]),
        ):
            res = await list_whatsapp_templates(config_id=None, user=self.user)

        self.assertEqual(res.templates, [])

    async def test_list_templates_resolves_default_whatsapp_config(self):
        cfg = MagicMock(spec=TelephonyConfigurationModel)
        cfg.id = 15
        cfg.provider = "whatsapp"
        cfg.credentials = {"waba_id": "auto_waba"}

        mock_provider = MagicMock()
        mock_provider.list_message_templates = AsyncMock(
            return_value=[
                {
                    "id": "tpl_2",
                    "name": "auto_template",
                    "status": "APPROVED",
                    "category": "UTILITY",
                    "language": "en_US",
                    "body_text": "Code: {{1}}",
                    "components": [],
                    "variables": [],
                }
            ]
        )
        mock_provider.list_provider_phone_numbers = AsyncMock(return_value=[])

        with (
            patch(
                "api.routes.organization.db_client.list_telephony_configurations_by_provider",
                AsyncMock(return_value=[cfg]),
            ),
            patch(
                "api.routes.organization._ensure_config_belongs_to_org",
                AsyncMock(return_value=cfg),
            ),
            patch(
                "api.routes.organization.get_telephony_provider_by_id",
                AsyncMock(return_value=mock_provider),
            ),
            patch(
                "api.routes.organization.db_client.list_phone_numbers_for_config",
                AsyncMock(return_value=[]),
            ),
        ):
            res = await list_whatsapp_templates(config_id=None, user=self.user)

        self.assertEqual(res.config_id, 15)
        self.assertEqual(res.waba_id, "auto_waba")
        self.assertEqual(len(res.templates), 1)
        self.assertEqual(res.templates[0].name, "auto_template")

    async def test_list_templates_db_fallback_with_scalar_provider_metadata(self):
        cfg = MagicMock(spec=TelephonyConfigurationModel)
        cfg.id = 10
        cfg.provider = "whatsapp"
        cfg.credentials = {"waba_id": "test_waba"}

        mock_provider = MagicMock()
        mock_provider.list_message_templates = AsyncMock(return_value=[])
        # Provider listing fails, triggering DB fallback
        mock_provider.list_provider_phone_numbers = AsyncMock(side_effect=RuntimeError("Provider unreachable"))

        mock_db_num = MagicMock()
        mock_db_num.address = "+15559876"
        mock_db_num.label = "Backup Line"
        # provider is a string scalar, not a dict
        mock_db_num.extra_metadata = {"provider": "whatsapp"}

        with (
            patch(
                "api.routes.organization._ensure_config_belongs_to_org",
                AsyncMock(return_value=cfg),
            ),
            patch(
                "api.routes.organization.get_telephony_provider_by_id",
                AsyncMock(return_value=mock_provider),
            ),
            patch(
                "api.routes.organization.db_client.list_phone_numbers_for_config",
                AsyncMock(return_value=[mock_db_num]),
            ),
        ):
            res = await list_whatsapp_templates_for_config(config_id=10, user=self.user)

        self.assertEqual(len(res.phone_numbers), 1)
        self.assertEqual(res.phone_numbers[0].phone_number_id, "+15559876")
        self.assertEqual(res.phone_numbers[0].display_phone_number, "+15559876")
        self.assertEqual(res.phone_numbers[0].verified_name, "Backup Line")

    async def test_list_templates_auth_failure_status_code_surfaces_clean_message(self):
        cfg = MagicMock(spec=TelephonyConfigurationModel)
        cfg.id = 10
        cfg.provider = "whatsapp"

        class MetaOAuthError(Exception):
            status_code = 401

        with (
            patch(
                "api.routes.organization._ensure_config_belongs_to_org",
                AsyncMock(return_value=cfg),
            ),
            patch(
                "api.routes.organization.get_telephony_provider_by_id",
                AsyncMock(side_effect=MetaOAuthError("(#190) Error validating access token")),
            ),
            self.assertRaises(HTTPException) as ctx,
        ):
            await list_whatsapp_templates_for_config(config_id=10, user=self.user)

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("Authentication failed with Meta Graph API", ctx.exception.detail)

