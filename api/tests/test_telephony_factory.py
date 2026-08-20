from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from api.services.telephony.factory import (
    _normalize_with_phone_numbers,
    find_telephony_config_for_inbound,
    get_telephony_provider_for_run,
    load_credentials_for_transport,
    load_default_telephony_config,
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
    row = SimpleNamespace(id=213, inactive=False)

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
    get_config.assert_awaited_once_with(213, 2617, active_only=True)
    normalize.assert_awaited_once_with(row)


@pytest.mark.asyncio
async def test_load_telephony_config_by_id_rejects_inactive_row():
    row = SimpleNamespace(id=213, inactive=True)

    with (
        patch(
            "api.services.telephony.factory.db_client.get_telephony_configuration_for_org",
            new_callable=AsyncMock,
            return_value=row,
        ) as get_config,
        patch(
            "api.services.telephony.factory._normalize_with_phone_numbers",
            new_callable=AsyncMock,
        ) as normalize,
        pytest.raises(ValueError, match="configuration 213 is inactive"),
    ):
        await load_telephony_config_by_id(213, 2617)

    get_config.assert_awaited_once_with(213, 2617, active_only=True)
    normalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_default_telephony_config_rejects_inactive_row():
    row = SimpleNamespace(id=213, inactive=True)

    with (
        patch(
            "api.services.telephony.factory.db_client.get_default_telephony_configuration",
            new_callable=AsyncMock,
            return_value=row,
        ) as get_default,
        patch(
            "api.services.telephony.factory._normalize_with_phone_numbers",
            new_callable=AsyncMock,
        ) as normalize,
        pytest.raises(ValueError, match="configuration is inactive"),
    ):
        await load_default_telephony_config(2617)

    get_default.assert_awaited_once_with(2617, active_only=True)
    normalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_telephony_config_for_inbound_excludes_inactive_ari_row():
    row = SimpleNamespace(
        id=213,
        inactive=True,
        is_default_outbound=True,
        credentials={"provider": "ari"},
    )
    spec = SimpleNamespace(account_id_credential_field="")

    with (
        patch(
            "api.services.telephony.factory.registry.get_optional",
            return_value=spec,
        ),
        patch(
            "api.services.telephony.factory.db_client.list_telephony_configurations_by_provider",
            new_callable=AsyncMock,
            return_value=[row],
        ) as list_configs,
        patch(
            "api.services.telephony.factory._normalize_with_phone_numbers",
            new_callable=AsyncMock,
        ) as normalize,
    ):
        match = await find_telephony_config_for_inbound(2617, "ari", None)

    assert match is None
    list_configs.assert_awaited_once_with(2617, "ari", active_only=True)
    normalize.assert_not_awaited()


def _config_row() -> SimpleNamespace:
    return SimpleNamespace(id=14, provider="twilio", credentials={"account_sid": "AC1"})


def _normalize_patches(addresses, default_row):
    fake_spec = SimpleNamespace(
        config_loader=lambda raw: dict(raw), supports_trunks=False
    )
    return (
        patch(
            "api.services.telephony.factory.registry.get",
            return_value=fake_spec,
        ),
        patch(
            "api.services.telephony.factory.db_client.list_active_normalized_addresses_for_config",
            new_callable=AsyncMock,
            return_value=addresses,
        ),
        patch(
            "api.services.telephony.factory.db_client.get_default_caller_id",
            new_callable=AsyncMock,
            return_value=default_row,
        ),
    )


@pytest.mark.asyncio
async def test_normalize_attaches_default_from_number_when_flagged_and_active():
    addresses = ["+19789911885", "+18158552169"]
    default_row = SimpleNamespace(address_normalized="+18158552169")

    registry_p, addresses_p, default_p = _normalize_patches(addresses, default_row)
    with registry_p, addresses_p, default_p:
        config = await _normalize_with_phone_numbers(_config_row())

    assert config["from_numbers"] == addresses
    assert config["default_from_number"] == "+18158552169"


@pytest.mark.asyncio
async def test_normalize_skips_default_from_number_when_not_in_active_pool():
    # A default flag left on a deactivated number must not become the caller ID.
    default_row = SimpleNamespace(address_normalized="+18158552169")

    registry_p, addresses_p, default_p = _normalize_patches(
        ["+19789911885"], default_row
    )
    with registry_p, addresses_p, default_p:
        config = await _normalize_with_phone_numbers(_config_row())

    assert "default_from_number" not in config


@pytest.mark.asyncio
async def test_normalize_skips_default_from_number_when_none_flagged():
    registry_p, addresses_p, default_p = _normalize_patches(["+19789911885"], None)
    with registry_p, addresses_p, default_p:
        config = await _normalize_with_phone_numbers(_config_row())

    assert "default_from_number" not in config
