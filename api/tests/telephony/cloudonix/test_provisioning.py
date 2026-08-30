from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services import organization_bootstrap
from api.services.telephony.providers.cloudonix import provisioning


@pytest.mark.asyncio
async def test_managed_configuration_is_created_from_mps_provisioning(monkeypatch):
    mps_result = {
        "provisioning_id": "11111111-1111-4111-8111-111111111111",
        "domain_name": "oss-dograh-11111111",
        "domain_uuid": "22222222-2222-4222-8222-222222222222",
        "bearer_token": "domain-bearer",
        "status": "ready",
    }
    processed = {
        "bearer_token": "domain-bearer",
        "domain_id": f"{mps_result['domain_name']}.cloudonix.net",
        "domain_uuid": mps_result["domain_uuid"],
        "managed_by": provisioning.MANAGED_BY,
        "provisioning_id": mps_result["provisioning_id"],
        "application_name": "dograh-111111111111411181111111",
        "application_id": 73,
    }
    created = SimpleNamespace(
        id=9,
        name=provisioning.MANAGED_CONFIGURATION_NAME,
        provider="cloudonix",
        credentials=processed,
    )

    monkeypatch.setattr(
        provisioning.mps_service_key_client,
        "ensure_cloudonix_domain",
        AsyncMock(return_value=mps_result),
    )
    monkeypatch.setattr(
        provisioning,
        "_preprocess_credentials_on_save",
        AsyncMock(return_value=processed),
    )
    monkeypatch.setattr(
        provisioning.db_client,
        "list_telephony_configurations",
        AsyncMock(return_value=[]),
    )
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(
        provisioning.db_client, "create_telephony_configuration", create
    )

    result = await provisioning.ensure_managed_cloudonix_configuration(
        42,
        mps_organization_id=None,
        created_by="oss_123_11111111-1111-4111-8111-111111111111",
    )

    assert result is created
    provisioning.mps_service_key_client.ensure_cloudonix_domain.assert_awaited_once_with(
        organization_id=None,
        created_by="oss_123_11111111-1111-4111-8111-111111111111",
    )
    provisioning._preprocess_credentials_on_save.assert_awaited_once_with(
        {
            "bearer_token": "domain-bearer",
            "domain_id": "oss-dograh-11111111.cloudonix.net",
            "domain_uuid": mps_result["domain_uuid"],
            "managed_by": provisioning.MANAGED_BY,
            "provisioning_id": mps_result["provisioning_id"],
        }
    )
    create.assert_awaited_once_with(
        organization_id=42,
        name=provisioning.MANAGED_CONFIGURATION_NAME,
        provider="cloudonix",
        credentials=processed,
        is_default_outbound=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows, expected",
    [
        ([], False),
        (
            [
                SimpleNamespace(
                    name=provisioning.MANAGED_CONFIGURATION_NAME,
                    provider="cloudonix",
                    credentials={"managed_by": provisioning.MANAGED_BY},
                )
            ],
            True,
        ),
        # A customer's own Cloudonix setup is not ours, so an org holding only
        # one still needs managed SIP provisioned.
        (
            [
                SimpleNamespace(
                    name="My Cloudonix",
                    provider="cloudonix",
                    credentials={"bearer_token": "customer-token"},
                )
            ],
            False,
        ),
        # Same name, but not provisioned by us.
        (
            [
                SimpleNamespace(
                    name=provisioning.MANAGED_CONFIGURATION_NAME,
                    provider="cloudonix",
                    credentials={},
                )
            ],
            False,
        ),
    ],
)
async def test_has_managed_configuration_only_counts_our_own(
    monkeypatch, rows, expected
):
    monkeypatch.setattr(
        provisioning.db_client,
        "list_telephony_configurations",
        AsyncMock(return_value=rows),
    )

    assert await provisioning.has_managed_cloudonix_configuration(42) is expected


@pytest.mark.asyncio
async def test_oss_sip_provisioning_failure_is_contained(monkeypatch):
    """A Cloudonix outage must not propagate into organization bootstrap."""
    monkeypatch.setattr(organization_bootstrap, "DEPLOYMENT_MODE", "oss")
    ensure = AsyncMock(side_effect=RuntimeError("Cloudonix unavailable"))
    monkeypatch.setattr(
        provisioning,
        "ensure_managed_cloudonix_configuration",
        ensure,
    )

    provisioned = await organization_bootstrap.provision_managed_sip_connectivity(
        42,
        created_by="oss_123_11111111-1111-4111-8111-111111111111",
    )

    assert provisioned is False
    ensure.assert_awaited_once_with(
        42,
        mps_organization_id=None,
        created_by="oss_123_11111111-1111-4111-8111-111111111111",
    )


@pytest.mark.asyncio
async def test_saas_sip_provisioning_uses_the_organization_owner(monkeypatch):
    monkeypatch.setattr(organization_bootstrap, "DEPLOYMENT_MODE", "saas")
    ensure = AsyncMock()
    monkeypatch.setattr(
        provisioning,
        "ensure_managed_cloudonix_configuration",
        ensure,
    )

    provisioned = await organization_bootstrap.provision_managed_sip_connectivity(
        42,
        created_by="11111111-1111-4111-8111-111111111111",
    )

    assert provisioned is True
    ensure.assert_awaited_once_with(
        42,
        mps_organization_id=42,
        created_by="11111111-1111-4111-8111-111111111111",
    )
