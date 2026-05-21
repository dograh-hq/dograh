from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.factory import (
    get_telephony_provider_for_run,
    load_credentials_for_transport,
    load_telephony_config_by_id,
)


@pytest.mark.asyncio
async def test_get_telephony_provider_for_run_casts_numeric_string_config_id():
    workflow_run = SimpleNamespace(
        initial_context={"telephony_configuration_id": "213"}
    )

    with (
        patch(
            "api.services.telephony.factory.get_telephony_provider_by_id",
            new_callable=AsyncMock,
            return_value="provider",
        ) as get_provider,
        patch(
            "api.services.telephony.factory.get_default_telephony_provider",
            new_callable=AsyncMock,
        ) as get_default,
    ):
        result = await get_telephony_provider_for_run(workflow_run, 2617)

    assert result == "provider"
    get_provider.assert_awaited_once_with("213", 2617)
    get_default.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_telephony_provider_for_run_rejects_non_numeric_string_config_id():
    workflow_run = SimpleNamespace(
        initial_context={"telephony_configuration_id": "twilio-main"}
    )

    with patch(
        "api.services.telephony.factory.get_default_telephony_provider",
        new_callable=AsyncMock,
    ) as get_default:
        with pytest.raises(
            ValueError,
            match="telephony_configuration_id must be an integer",
        ):
            await get_telephony_provider_for_run(workflow_run, 2617)

    get_default.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_credentials_for_transport_casts_numeric_string_config_id():
    with (
        patch(
            "api.services.telephony.factory.load_telephony_config_by_id",
            new_callable=AsyncMock,
            return_value={"provider": "twilio"},
        ) as load_by_id,
        patch(
            "api.services.telephony.factory.load_default_telephony_config",
            new_callable=AsyncMock,
        ) as load_default,
    ):
        result = await load_credentials_for_transport(2617, "213", "twilio")

    assert result == {"provider": "twilio"}
    load_by_id.assert_awaited_once_with("213", 2617)
    load_default.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_telephony_config_by_id_casts_numeric_string_before_db_lookup():
    row = SimpleNamespace(id=213)

    with (
        patch(
            "api.services.telephony.factory.db_client.get_telephony_configuration_for_org",
            new_callable=AsyncMock,
            return_value=row,
        ) as get_config,
        patch(
            "api.services.telephony.factory._normalize_with_phone_numbers",
            new_callable=AsyncMock,
            return_value={"provider": "twilio"},
        ) as normalize,
    ):
        result = await load_telephony_config_by_id("213", 2617)

    assert result == {"provider": "twilio"}
    get_config.assert_awaited_once_with(213, 2617)
    normalize.assert_awaited_once_with(row)
