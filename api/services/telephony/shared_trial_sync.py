"""Shared trial platform telephony synchronization.

Synchronizes active 'shared_trial' platform inventory numbers and their underlying
telephony configurations to customer organizations so users can test workflow phone
calls directly after registration without having to connect a carrier account first.
"""

from typing import Dict, List
from loguru import logger
from sqlalchemy import select

from api.db import db_client
from api.db.models import TelephonyConfigurationModel, TelephonyPhoneNumberModel


async def sync_shared_trial_telephony_for_org(organization_id: int) -> bool:
    """Ensure that active shared_trial platform inventory numbers and their configurations
    are provisioned for the specified organization so users can immediately test workflow
    phone calls out of the box.

    Returns True if at least one shared trial config was provisioned or updated.
    """
    if not organization_id:
        return False

    async with db_client.async_session() as session:
        # Find active shared trial platform numbers and their active parent configurations
        stmt = (
            select(TelephonyPhoneNumberModel, TelephonyConfigurationModel)
            .join(
                TelephonyConfigurationModel,
                TelephonyPhoneNumberModel.telephony_configuration_id == TelephonyConfigurationModel.id,
            )
            .where(
                TelephonyPhoneNumberModel.is_platform_inventory == True,
                TelephonyPhoneNumberModel.is_active == True,
                TelephonyPhoneNumberModel.pool_type == "shared_trial",
                TelephonyConfigurationModel.inactive == False,
            )
            .order_by(TelephonyPhoneNumberModel.created_at)
        )
        result = await session.execute(stmt)
        rows = result.all()
        if not rows:
            return False

        # Group numbers by their source config
        configs_map: Dict[TelephonyConfigurationModel, List[TelephonyPhoneNumberModel]] = {}
        for num, config in rows:
            configs_map.setdefault(config, []).append(num)

        # Check if the user org already has any active default outbound config
        default_outbound_exists = (
            await session.execute(
                select(TelephonyConfigurationModel.id).where(
                    TelephonyConfigurationModel.organization_id == organization_id,
                    TelephonyConfigurationModel.is_default_outbound == True,
                    TelephonyConfigurationModel.inactive == False,
                )
            )
        ).first() is not None

        changed = False

        for source_cfg, numbers in configs_map.items():
            # If the current organization is the platform owner/source org, it already owns
            # the underlying configuration and inventory numbers directly; do not duplicate.
            if source_cfg.organization_id == organization_id:
                continue

            config_name = f"Platform - {source_cfg.name}"

            # Check if organization already has this configuration
            stmt_cfg = select(TelephonyConfigurationModel).where(
                TelephonyConfigurationModel.organization_id == organization_id,
                TelephonyConfigurationModel.name == config_name,
                TelephonyConfigurationModel.is_platform_inventory == False,
            )
            org_cfg = (await session.execute(stmt_cfg)).scalar_one_or_none()

            if not org_cfg:
                org_cfg = TelephonyConfigurationModel(
                    organization_id=organization_id,
                    name=config_name,
                    provider=source_cfg.provider,
                    credentials=source_cfg.credentials,
                    is_default_outbound=not default_outbound_exists,
                )
                session.add(org_cfg)
                await session.flush()
                changed = True
                if not default_outbound_exists:
                    default_outbound_exists = True
            else:
                # Keep credentials and provider in sync with platform configuration
                if (
                    org_cfg.credentials != source_cfg.credentials
                    or org_cfg.provider != source_cfg.provider
                    or org_cfg.inactive != source_cfg.inactive
                ):
                    org_cfg.credentials = source_cfg.credentials
                    org_cfg.provider = source_cfg.provider
                    org_cfg.inactive = source_cfg.inactive
                    changed = True

            # Check if this config already has any active default caller ID
            has_default_caller = (
                await session.execute(
                    select(TelephonyPhoneNumberModel.id).where(
                        TelephonyPhoneNumberModel.telephony_configuration_id == org_cfg.id,
                        TelephonyPhoneNumberModel.is_default_caller_id == True,
                        TelephonyPhoneNumberModel.is_active == True,
                    )
                )
            ).first() is not None

            for num in numbers:
                stmt_num = select(TelephonyPhoneNumberModel).where(
                    TelephonyPhoneNumberModel.organization_id == organization_id,
                    TelephonyPhoneNumberModel.address_normalized == num.address_normalized,
                    TelephonyPhoneNumberModel.is_platform_inventory == False,
                )
                org_num = (await session.execute(stmt_num)).scalar_one_or_none()

                if not org_num:
                    org_num = TelephonyPhoneNumberModel(
                        organization_id=organization_id,
                        telephony_configuration_id=org_cfg.id,
                        address=num.address,
                        address_normalized=num.address_normalized,
                        address_type=num.address_type,
                        country_code=num.country_code,
                        label=num.label or f"Platform Test {num.address}",
                        is_active=True,
                        is_default_caller_id=not has_default_caller,
                        pool_type="shared_trial",
                        is_platform_inventory=False,
                        monthly_price_cents=0,
                    )
                    session.add(org_num)
                    changed = True
                    if not has_default_caller:
                        has_default_caller = True
                else:
                    if (
                        org_num.telephony_configuration_id != org_cfg.id
                        or not org_num.is_active
                        or org_num.pool_type != "shared_trial"
                    ):
                        org_num.telephony_configuration_id = org_cfg.id
                        org_num.is_active = True
                        org_num.pool_type = "shared_trial"
                        changed = True
                    if not has_default_caller and not org_num.is_default_caller_id:
                        org_num.is_default_caller_id = True
                        has_default_caller = True
                        changed = True

        if changed:
            await session.commit()
            logger.info(
                f"Synchronized shared trial platform telephony for organization {organization_id}"
            )

        return changed
