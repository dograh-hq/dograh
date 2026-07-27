from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routes.organization import (
    _sync_inbound_for_phone_number,
    delete_phone_number,
    update_phone_number,
)
from api.schemas.telephony_phone_number import (
    PhoneNumberUpdateRequest,
    ProviderSyncStatus,
)
from api.services.telephony.base import ProviderSyncResult


@pytest.mark.asyncio
async def test_phone_number_sync_passes_none_to_provider_when_detaching():
    provider = SimpleNamespace(
        configure_inbound=AsyncMock(return_value=ProviderSyncResult(ok=True))
    )

    with (
        patch(
            "api.routes.organization.get_telephony_provider_by_id",
            new_callable=AsyncMock,
            return_value=provider,
        ),
        patch(
            "api.routes.organization.get_backend_endpoints",
            new_callable=AsyncMock,
        ) as get_backend_endpoints,
    ):
        result = await _sync_inbound_for_phone_number(
            7, 11, "+15551230002", attach=False
        )

    assert result.ok
    get_backend_endpoints.assert_not_awaited()
    provider.configure_inbound.assert_awaited_once_with("+15551230002", None)


@pytest.mark.asyncio
async def test_clearing_inbound_workflow_detaches_provider_number():
    existing = SimpleNamespace(address="+15551230002")
    updated = SimpleNamespace(
        address="+15551230002",
        inbound_workflow_id=None,
        is_active=True,
    )
    response = SimpleNamespace(provider_sync=None)
    sync_status = ProviderSyncStatus(ok=True)

    with (
        patch(
            "api.routes.organization._ensure_config_belongs_to_org",
            new_callable=AsyncMock,
        ),
        patch(
            "api.routes.organization.db_client.get_phone_number_for_config",
            new_callable=AsyncMock,
            return_value=existing,
        ),
        patch(
            "api.routes.organization.db_client.update_phone_number",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch(
            "api.routes.organization._phone_number_to_response",
            return_value=response,
        ),
        patch(
            "api.routes.organization._sync_inbound_for_phone_number",
            new_callable=AsyncMock,
            return_value=sync_status,
        ) as sync_inbound,
    ):
        result = await update_phone_number(
            config_id=7,
            phone_number_id=9,
            request=PhoneNumberUpdateRequest(clear_inbound_workflow=True),
            user=SimpleNamespace(selected_organization_id=11),
        )

    assert result.provider_sync == sync_status
    sync_inbound.assert_awaited_once_with(7, 11, "+15551230002", attach=False)


@pytest.mark.asyncio
async def test_deleting_phone_number_detaches_provider_number():
    existing = SimpleNamespace(address="+15551230002")
    sync_status = ProviderSyncStatus(ok=True)

    with (
        patch(
            "api.routes.organization._ensure_config_belongs_to_org",
            new_callable=AsyncMock,
        ),
        patch(
            "api.routes.organization.db_client.get_phone_number_for_config",
            new_callable=AsyncMock,
            return_value=existing,
        ),
        patch(
            "api.routes.organization.db_client.delete_phone_number",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "api.routes.organization._sync_inbound_for_phone_number",
            new_callable=AsyncMock,
            return_value=sync_status,
        ) as sync_inbound,
    ):
        result = await delete_phone_number(
            config_id=7,
            phone_number_id=9,
            user=SimpleNamespace(selected_organization_id=11),
        )

    assert result == {
        "message": "Phone number deleted",
        "provider_sync": {"ok": True, "message": None},
    }
    sync_inbound.assert_awaited_once_with(7, 11, "+15551230002", attach=False)


@pytest.mark.asyncio
async def test_failed_detach_preserves_local_phone_number():
    existing = SimpleNamespace(address="+15551230002")
    delete_local = AsyncMock(return_value=True)

    with (
        patch(
            "api.routes.organization._ensure_config_belongs_to_org",
            new_callable=AsyncMock,
        ),
        patch(
            "api.routes.organization.db_client.get_phone_number_for_config",
            new_callable=AsyncMock,
            return_value=existing,
        ),
        patch(
            "api.routes.organization.db_client.delete_phone_number",
            delete_local,
        ),
        patch(
            "api.routes.organization._sync_inbound_for_phone_number",
            new_callable=AsyncMock,
            return_value=ProviderSyncStatus(
                ok=False, message="Vobiz API 409: number is still assigned"
            ),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await delete_phone_number(
                config_id=7,
                phone_number_id=9,
                user=SimpleNamespace(selected_organization_id=11),
            )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Vobiz API 409: number is still assigned"
    delete_local.assert_not_awaited()
