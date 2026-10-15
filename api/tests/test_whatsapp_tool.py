"""Tests for WhatsApp template messaging tool schema and execution."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.routes.tool import unarchive_tool
from api.schemas.tool import CreateToolRequest
from api.services.tool_management import ToolManagementError, create_tool_for_user
from api.services.workflow.tools.custom_tool import tool_to_function_schema
from api.services.workflow.tools.whatsapp_tool import execute_whatsapp_tool


class MockTool:
    def __init__(self, name: str, config: dict, tool_uuid: str = "tool-123"):
        self.name = name
        self.tool_uuid = tool_uuid
        self.description = f"Send {name}"
        self.definition = {
            "type": "whatsapp",
            "config": config,
        }


def test_whatsapp_tool_schema_multi_template():
    config = {
        "execution_mode": "async",
        "templates": [
            {
                "id": "confirm_booking",
                "name": "Booking Confirmation",
                "description": "Send when caller confirms booking",
                "template_name": "booking_confirm_template",
                "language_code": "en_US",
                "from_phone_number_id": "phone_123",
                "variable_mappings": [
                    {
                        "index": 1,
                        "name": "customer_name",
                        "source": "context",
                        "value": "{{initial_context.lead_name}}",
                    },
                    {
                        "index": 2,
                        "name": "booking_date",
                        "source": "llm",
                        "value": "Confirmed date and time of booking",
                    },
                ],
            },
            {
                "id": "send_brochure",
                "name": "Send Brochure",
                "description": "Send product brochure link",
                "template_name": "brochure_template",
                "language_code": "en_US",
                "from_phone_number_id": "phone_456",
                "variable_mappings": [
                    {
                        "index": 1,
                        "name": "brochure_topic",
                        "source": "llm",
                        "value": "Topic or product line",
                    },
                ],
            },
        ],
    }
    tool = MockTool("Send WhatsApp", config)
    schema = tool_to_function_schema(tool)

    assert schema["type"] == "function"
    func = schema["function"]
    assert func["name"] == "send_whatsapp"
    props = func["parameters"]["properties"]

    # template_id enum with both templates
    assert "template_id" in props
    assert props["template_id"]["enum"] == ["confirm_booking", "send_brochure"]
    assert "confirm_booking" in props["template_id"]["description"]
    assert "send_brochure" in props["template_id"]["description"]

    # Parameters from both templates exposed
    assert "booking_date" in props
    assert "brochure_topic" in props
    assert "customer_name" in props  # exposed as optional fallback parameter
    assert "customer_name" not in func["parameters"]["required"]

    # Recipient override is exposed as optional
    assert "recipient_phone_number" in props
    assert func["parameters"]["required"] == ["template_id"]


def test_whatsapp_tool_schema_single_template_no_template_id_required():
    config = {
        "execution_mode": "async",
        "templates": [
            {
                "id": "default_temp",
                "name": "OTP",
                "template_name": "otp_code",
                "variable_mappings": [
                    {
                        "index": 1,
                        "name": "code",
                        "source": "llm",
                        "value": "6-digit OTP code",
                    }
                ],
            }
        ],
    }
    tool = MockTool("Send OTP", config)
    schema = tool_to_function_schema(tool)

    func = schema["function"]
    props = func["parameters"]["properties"]
    assert "template_id" in props
    assert "template_id" not in func["parameters"].get("required", [])
    assert "code" in props
    assert func["parameters"]["required"] == ["code"]


@pytest.mark.asyncio
async def test_execute_whatsapp_tool_multi_template_selection_and_sender_id():
    config = {
        "execution_mode": "sync",
        "templates": [
            {
                "id": "reminder",
                "name": "Appointment Reminder",
                "template_name": "reminder_template",
                "language_code": "en_US",
                "from_phone_number_id": "sender_phone_abc",
                "variable_mappings": [
                    {
                        "index": 1,
                        "name": "client_name",
                        "source": "context",
                        "value": "{{initial_context.lead_name}}",
                    },
                    {
                        "index": 2,
                        "name": "time_slot",
                        "source": "llm",
                        "value": "Time of appointment",
                    },
                ],
            },
            {
                "id": "feedback",
                "name": "Feedback Link",
                "template_name": "feedback_template",
                "from_phone_number_id": "sender_phone_xyz",
            },
        ],
    }
    tool = MockTool("WhatsApp Notifications", config)
    call_context = {
        "direction": "outbound",
        "called_number": "+1 (555) 333-4444",
        "lead_name": "John Doe",
    }

    mock_provider = MagicMock()
    mock_provider.send_template_message = AsyncMock(
        return_value={"messages": [{"id": "wamid.REMINDER"}]}
    )

    with (
        patch(
            "api.services.workflow.tools.whatsapp_tool.db_client.list_telephony_configurations_by_provider",
            AsyncMock(return_value=[MagicMock(id=10)]),
        ),
        patch(
            "api.services.workflow.tools.whatsapp_tool.get_telephony_provider_by_id",
            AsyncMock(return_value=mock_provider),
        ),
    ):
        result = await execute_whatsapp_tool(
            tool=tool,
            arguments={
                "template_id": "reminder",
                "time_slot": "Tomorrow 3pm",
            },
            call_context_vars=call_context,
            organization_id=1,
        )

    assert result["status"] == "delivered"
    # Destination automatically picked called_number because direction == "outbound"
    mock_provider.send_template_message.assert_awaited_once_with(
        to_number="15553334444",
        template_name="reminder_template",
        language_code="en_US",
        parameters=["John Doe", "Tomorrow 3pm"],
        from_phone_number_id="sender_phone_abc",
    )


@pytest.mark.asyncio
async def test_execute_whatsapp_tool_automatic_recipient_routing():
    # Inbound call: defaults to caller_number
    config = {
        "execution_mode": "sync",
        "templates": [
            {
                "id": "info",
                "template_name": "info_template",
            }
        ],
    }
    tool = MockTool("Send Info", config)

    mock_provider = MagicMock()
    mock_provider.send_template_message = AsyncMock(
        return_value={"messages": [{"id": "wamid.INBOUND"}]}
    )

    with (
        patch(
            "api.services.workflow.tools.whatsapp_tool.db_client.list_telephony_configurations_by_provider",
            AsyncMock(return_value=[MagicMock(id=10)]),
        ),
        patch(
            "api.services.workflow.tools.whatsapp_tool.get_telephony_provider_by_id",
            AsyncMock(return_value=mock_provider),
        ),
    ):
        # 1. Inbound call uses caller_number
        inbound_context = {"direction": "inbound", "caller_number": "+1 (555) 111-2222"}
        await execute_whatsapp_tool(
            tool=tool,
            arguments={},
            call_context_vars=inbound_context,
            organization_id=1,
        )
        mock_provider.send_template_message.assert_awaited_with(
            to_number="15551112222",
            template_name="info_template",
            language_code="en_US",
            parameters=[],
            from_phone_number_id=None,
        )

        # 2. Argument override takes highest precedence
        mock_provider.send_template_message.reset_mock()
        await execute_whatsapp_tool(
            tool=tool,
            arguments={"recipient_phone_number": "+1 (555) 999-0000"},
            call_context_vars=inbound_context,
            organization_id=1,
        )
        mock_provider.send_template_message.assert_awaited_with(
            to_number="15559990000",
            template_name="info_template",
            language_code="en_US",
            parameters=[],
            from_phone_number_id=None,
        )


@pytest.mark.asyncio
async def test_execute_whatsapp_tool_async_queues_immediately():
    config = {
        "execution_mode": "async",
        "templates": [
            {
                "id": "followup",
                "template_name": "lead_followup",
            }
        ],
    }
    tool = MockTool("Send Followup", config)
    call_context = {"caller_number": "+1 (555) 234-5678"}

    mock_provider = MagicMock()
    mock_provider.send_template_message = AsyncMock(
        return_value={"messages": [{"id": "wamid.123"}]}
    )

    with (
        patch(
            "api.services.workflow.tools.whatsapp_tool.db_client.list_telephony_configurations_by_provider",
            AsyncMock(return_value=[MagicMock(id=5)]),
        ),
        patch(
            "api.services.workflow.tools.whatsapp_tool.get_telephony_provider_by_id",
            AsyncMock(return_value=mock_provider),
        ),
    ):
        result = await execute_whatsapp_tool(
            tool=tool,
            arguments={"parameters": ["https://dograh.com"]},
            call_context_vars=call_context,
            organization_id=1,
        )

    assert result["status"] == "queued"
    assert "15552345678" in result["message"]
    # Allow background task to execute
    await asyncio.sleep(0.05)
    mock_provider.send_template_message.assert_awaited_once_with(
        to_number="15552345678",
        template_name="lead_followup",
        language_code="en_US",
        parameters=["https://dograh.com"],
        from_phone_number_id=None,
    )


@pytest.mark.asyncio
async def test_execute_whatsapp_tool_missing_recipient_returns_error():
    config = {
        "templates": [{"id": "t1", "template_name": "test"}],
    }
    tool = MockTool("Test Tool", config)
    result = await execute_whatsapp_tool(
        tool=tool,
        arguments={},
        call_context_vars={},
        organization_id=1,
    )
    assert result["status"] == "error"
    assert "Could not resolve a valid recipient phone number" in result["error"]


@pytest.mark.asyncio
async def test_create_whatsapp_tool_enforces_single_active_tool_per_org():
    user = MagicMock()
    user.id = 1
    user.provider_id = "provider_1"
    user.selected_organization_id = 42

    request = CreateToolRequest(
        name="Send WhatsApp",
        category="whatsapp",
        definition={
            "schema_version": 1,
            "type": "whatsapp",
            "config": {
                "execution_mode": "async",
                "templates": [],
            },
        },
    )

    # 1. When an active whatsapp tool already exists, creation must be refused
    with patch(
        "api.services.tool_management.db_client.get_tools_for_organization",
        AsyncMock(return_value=[MagicMock(id=99, category="whatsapp")]),
    ):
        with pytest.raises(ToolManagementError) as exc_info:
            await create_tool_for_user(request, user)
        assert exc_info.value.error_code == "whatsapp_tool_already_exists"
        assert "A WhatsApp Messaging tool already exists" in exc_info.value.message

    # 2. When no WhatsApp telephony configuration exists, creation must be refused
    with (
        patch(
            "api.services.tool_management.db_client.get_tools_for_organization",
            AsyncMock(return_value=[]),
        ),
        patch(
            "api.services.tool_management.db_client.list_telephony_configurations_by_provider",
            AsyncMock(return_value=[]),
        ),
    ):
        with pytest.raises(ToolManagementError) as exc_info:
            await create_tool_for_user(request, user)
        assert exc_info.value.error_code == "whatsapp_telephony_not_configured"
        assert "WhatsApp is not configured in Telephony settings" in exc_info.value.message

    # 3. When WhatsApp telephony configuration exists, creation proceeds
    now = datetime.now(UTC)
    mock_created_tool = SimpleNamespace(
        id=100,
        tool_uuid="tool-uuid-100",
        name="Send WhatsApp",
        description=None,
        category="whatsapp",
        icon="message-square",
        icon_color="#25D366",
        status="active",
        definition=request.definition.model_dump(),
        created_at=now,
        updated_at=now,
        created_by_user=None,
    )
    with (
        patch(
            "api.services.tool_management.db_client.get_tools_for_organization",
            AsyncMock(return_value=[]),
        ),
        patch(
            "api.services.tool_management.db_client.list_telephony_configurations_by_provider",
            AsyncMock(return_value=[MagicMock(id=1)]),
        ),
        patch(
            "api.services.tool_management.db_client.create_tool",
            AsyncMock(return_value=mock_created_tool),
        ),
        patch("api.services.tool_management.capture_event"),
    ):
        tool_resp = await create_tool_for_user(request, user)
        assert tool_resp.category == "whatsapp"
        assert tool_resp.tool_uuid == "tool-uuid-100"


@pytest.mark.asyncio
async def test_unarchive_whatsapp_tool_enforces_single_active_tool_per_org():
    user = MagicMock()
    user.id = 1
    user.selected_organization_id = 42

    archived_wa_tool = MagicMock(
        id=88,
        category="whatsapp",
        tool_uuid="archived-wa",
        definition={"type": "whatsapp", "config": {}},
    )

    # 1. When another active whatsapp tool already exists, unarchive is refused
    with (
        patch(
            "api.routes.tool.db_client.get_tool_by_uuid",
            AsyncMock(return_value=archived_wa_tool),
        ),
        patch(
            "api.routes.tool.db_client.get_tools_for_organization",
            AsyncMock(return_value=[MagicMock(id=99, category="whatsapp")]),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await unarchive_tool("archived-wa", user)
        assert exc_info.value.status_code == 400
        assert "An active WhatsApp Messaging tool already exists" in exc_info.value.detail

    # 2. When WhatsApp telephony configuration is deleted/missing, unarchive is refused
    with (
        patch(
            "api.routes.tool.db_client.get_tool_by_uuid",
            AsyncMock(return_value=archived_wa_tool),
        ),
        patch(
            "api.routes.tool.db_client.get_tools_for_organization",
            AsyncMock(return_value=[]),
        ),
        patch(
            "api.routes.tool.db_client.list_telephony_configurations_by_provider",
            AsyncMock(return_value=[]),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await unarchive_tool("archived-wa", user)
        assert exc_info.value.status_code == 400
        assert "Cannot unarchive WhatsApp tool because WhatsApp is not configured in Telephony settings" in exc_info.value.detail

    # 3. When telephony config exists and no active whatsapp tool exists, unarchive succeeds
    now = datetime.now(UTC)
    unarchived_tool = SimpleNamespace(
        id=88,
        tool_uuid="archived-wa",
        name="Restored WA",
        description=None,
        category="whatsapp",
        icon="message-square",
        icon_color="#25D366",
        status="active",
        definition={"type": "whatsapp", "config": {}},
        created_at=now,
        updated_at=now,
        created_by_user=None,
    )
    with (
        patch(
            "api.routes.tool.db_client.get_tool_by_uuid",
            AsyncMock(return_value=archived_wa_tool),
        ),
        patch(
            "api.routes.tool.db_client.get_tools_for_organization",
            AsyncMock(return_value=[]),
        ),
        patch(
            "api.routes.tool.db_client.list_telephony_configurations_by_provider",
            AsyncMock(return_value=[MagicMock(id=1)]),
        ),
        patch(
            "api.routes.tool.db_client.unarchive_tool",
            AsyncMock(return_value=unarchived_tool),
        ),
    ):
        res = await unarchive_tool("archived-wa", user)
        assert res.tool_uuid == "archived-wa"


@pytest.mark.asyncio
async def test_delete_telephony_configuration_blocks_when_active_whatsapp_tool_exists():
    from api.db.telephony_configuration_client import (
        TelephonyConfigurationClient,
        TelephonyConfigurationInUseError,
    )

    with patch("api.db.base_client.create_async_engine"):
        client = TelephonyConfigurationClient()

    mock_row = MagicMock()
    mock_row.id = 55
    mock_row.provider = "whatsapp"
    mock_row.organization_id = 10

    # 1. When an active WhatsApp tool exists, deletion must raise TelephonyConfigurationInUseError
    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_row)

    mock_campaign_result = MagicMock()
    mock_campaign_result.first = MagicMock(return_value=None)

    mock_tool_result = MagicMock()
    mock_tool_result.scalar_one_or_none = MagicMock(return_value="Send WhatsApp Message")

    mock_session.execute = AsyncMock(side_effect=[mock_campaign_result, mock_tool_result])

    with patch.object(client, "async_session", return_value=mock_session):
        mock_session.__aenter__.return_value = mock_session
        with pytest.raises(TelephonyConfigurationInUseError) as exc_info:
            await client.delete_telephony_configuration(55, 10)
        assert "Telephony configuration is in use by active tool 'Send WhatsApp Message'" in str(exc_info.value)
        assert "Please archive or delete the tool first" in str(exc_info.value)

    # 2. When no active WhatsApp tool exists, deletion proceeds
    mock_tool_result_empty = MagicMock()
    mock_tool_result_empty.scalar_one_or_none = MagicMock(return_value=None)

    mock_session.execute = AsyncMock(side_effect=[mock_campaign_result, mock_tool_result_empty])
    mock_session.delete = AsyncMock()
    mock_session.commit = AsyncMock()

    with patch.object(client, "async_session", return_value=mock_session):
        mock_session.__aenter__.return_value = mock_session
        deleted = await client.delete_telephony_configuration(55, 10)
        assert deleted is True
        mock_session.delete.assert_awaited_with(mock_row)
        mock_session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_execute_whatsapp_tool_static_and_context_fallback_to_llm():
    config = {
        "execution_mode": "sync",
        "telephony_configuration_id": 42,
        "templates": [
            {
                "id": "order_temp",
                "name": "Order Notice",
                "template_name": "order_notice",
                "language_code": "en_US",
                "variable_mappings": [
                    {
                        "index": 1,
                        "name": "store_name",
                        "source": "literal",
                        "value": "Jasper's Market",
                    },
                    {
                        "index": 2,
                        "name": "customer_name",
                        "source": "context",
                        "value": "{{initial_context.lead_name}}",
                    },
                    {
                        "index": 3,
                        "name": "delivery_time",
                        "source": "llm",
                        "value": "Delivery window",
                    },
                ],
            }
        ],
    }
    tool = MockTool("WhatsApp Orders", config)
    mock_provider = AsyncMock()
    mock_provider.send_template_message = AsyncMock(return_value={"id": "msg_xyz"})

    # Case 1: When lead_name IS in context, context is used, static is taken from config without LLM
    with patch(
        "api.services.workflow.tools.whatsapp_tool.get_telephony_provider_by_id",
        return_value=mock_provider,
    ):
        result = await execute_whatsapp_tool(
            tool=tool,
            arguments={
                "delivery_time": "Tomorrow 5pm",
                "recipient_phone_number": "15551234567",
            },
            call_context_vars={"lead_name": "Alice in Context"},
            organization_id=1,
        )
        assert result["status"] == "delivered"
        mock_provider.send_template_message.assert_awaited_with(
            to_number="15551234567",
            template_name="order_notice",
            language_code="en_US",
            parameters=["Jasper's Market", "Alice in Context", "Tomorrow 5pm"],
            from_phone_number_id=None,
        )

    # Case 2: When lead_name is NOT in context (e.g. None), it falls back to the LLM argument
    with patch(
        "api.services.workflow.tools.whatsapp_tool.get_telephony_provider_by_id",
        return_value=mock_provider,
    ):
        result = await execute_whatsapp_tool(
            tool=tool,
            arguments={
                "customer_name": "Bob (from LLM fallback)",
                "delivery_time": "Friday 6pm",
                "recipient_phone_number": "15551234567",
            },
            call_context_vars={},
            organization_id=1,
        )
        assert result["status"] == "delivered"
        mock_provider.send_template_message.assert_awaited_with(
            to_number="15551234567",
            template_name="order_notice",
            language_code="en_US",
            parameters=["Jasper's Market", "Bob (from LLM fallback)", "Friday 6pm"],
            from_phone_number_id=None,
        )


@pytest.mark.asyncio
async def test_execute_whatsapp_tool_fallback_values_for_context_and_lead_phone():
    config = {
        "execution_mode": "sync",
        "telephony_configuration_id": 42,
        "fallback_recipient_phone_number": "+15558889999",
        "templates": [
            {
                "id": "promo_temp",
                "name": "Promo Notice",
                "template_name": "promo_notice",
                "language_code": "en_US",
                "variable_mappings": [
                    {
                        "index": 1,
                        "name": "customer_name",
                        "source": "context",
                        "value": "{{initial_context.lead_name}}",
                        "fallback_value": "Valued Customer",
                    },
                    {
                        "index": 2,
                        "name": "customer_phone",
                        "source": "lead_phone",
                        "fallback_value": "your registered phone",
                    },
                    {
                        "index": 3,
                        "name": "promo_code",
                        "source": "literal",
                        "value": "PROMO-2026",
                    },
                ],
            }
        ],
    }
    tool = MockTool("WhatsApp Promo", config)
    mock_provider = AsyncMock()
    mock_provider.send_template_message = AsyncMock(return_value={"id": "msg_promo_1"})

    # Case 1: Browser test / missing context -> uses fallback_recipient_phone_number and variable fallback_value
    with patch(
        "api.services.workflow.tools.whatsapp_tool.get_telephony_provider_by_id",
        return_value=mock_provider,
    ):
        result = await execute_whatsapp_tool(
            tool=tool,
            arguments={},
            call_context_vars={},
            organization_id=1,
        )
        assert result["status"] == "delivered"
        mock_provider.send_template_message.assert_awaited_with(
            to_number="15558889999",
            template_name="promo_notice",
            language_code="en_US",
            parameters=["Valued Customer", "your registered phone", "PROMO-2026"],
            from_phone_number_id=None,
        )

    # Case 2: Inbound call with caller_number and lead_name in context -> uses caller_number
    with patch(
        "api.services.workflow.tools.whatsapp_tool.get_telephony_provider_by_id",
        return_value=mock_provider,
    ):
        result = await execute_whatsapp_tool(
            tool=tool,
            arguments={},
            call_context_vars={
                "direction": "inbound",
                "caller_number": "+15553334444",
                "lead_name": "Sarah Connor",
            },
            organization_id=1,
        )
        assert result["status"] == "delivered"
        mock_provider.send_template_message.assert_awaited_with(
            to_number="15553334444",
            template_name="promo_notice",
            language_code="en_US",
            parameters=["Sarah Connor", "15553334444", "PROMO-2026"],
            from_phone_number_id=None,
        )

    # Case 3: When lead_phone has no fallback_val and no caller in context, uses clean_recipient
    config_no_phone_fb = {
        "execution_mode": "sync",
        "telephony_configuration_id": 42,
        "fallback_recipient_phone_number": "+15558889999",
        "templates": [
            {
                "id": "promo_temp",
                "name": "Promo Notice",
                "template_name": "promo_notice",
                "language_code": "en_US",
                "variable_mappings": [
                    {
                        "index": 1,
                        "name": "customer_phone",
                        "source": "lead_phone",
                        "fallback_value": None,
                    },
                ],
            }
        ],
    }
    tool_no_phone_fb = MockTool("WhatsApp Promo No FB", config_no_phone_fb)
    with patch(
        "api.services.workflow.tools.whatsapp_tool.get_telephony_provider_by_id",
        return_value=mock_provider,
    ):
        result = await execute_whatsapp_tool(
            tool=tool_no_phone_fb,
            arguments={},
            call_context_vars={},
            organization_id=1,
        )
        assert result["status"] == "delivered"
        mock_provider.send_template_message.assert_awaited_with(
            to_number="15558889999",
            template_name="promo_notice",
            language_code="en_US",
            parameters=["15558889999"],
            from_phone_number_id=None,
        )


def test_whatsapp_tool_schema_omits_context_vars_with_configured_fallback():
    from api.services.workflow.tools.custom_tool import tool_to_function_schema

    tool = MockTool(
        "WhatsApp Tool",
        {
            "templates": [
                {
                    "id": "1",
                    "name": "Template 1",
                    "template_name": "tpl_1",
                    "variable_mappings": [
                        {
                            "index": 1,
                            "name": "customer_name",
                            "source": "context",
                            "value": "{{initial_context.lead_name}}",
                            "fallback_value": "Valued Customer",
                        },
                        {
                            "index": 2,
                            "name": "booking_date",
                            "source": "llm",
                            "value": "Booking date",
                        },
                    ],
                }
            ]
        },
    )
    schema = tool_to_function_schema(tool)
    parameters = schema["function"]["parameters"]["properties"]

    # customer_name has a configured fallback_value, so it shouldn't clutter the LLM parameters schema
    assert "customer_name" not in parameters
    # booking_date is LLM sourced, so it must be present
    assert "booking_date" in parameters


