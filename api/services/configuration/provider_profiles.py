"""Storage and lookup of named, saved model provider profiles.

Profiles are kept as one JSON document per organization under the
``MODEL_PROVIDER_PROFILES`` key of ``organization_configurations``. The
document holds real credentials; anything returned to clients must go through
:func:`mask_profile`.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import ValidationError

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.model_provider_profiles import (
    ModelProviderProfile,
    ModelProviderProfileResponse,
    ModelProviderProfilesDocument,
    parse_profile_config,
    validate_profile_name,
)
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.masking import (
    check_for_masked_keys,
    mask_service_config,
)
from api.services.configuration.merge import merge_service_secrets

_KEY = OrganizationConfigurationKey.MODEL_PROVIDER_PROFILES.value


class ProfileError(Exception):
    """Base class for profile errors."""


class ProfileNotFoundError(ProfileError):
    pass


class ProfileAlreadyExistsError(ProfileError):
    pass


class ProfileConfigError(ProfileError):
    """The supplied config is invalid (maps to HTTP 422)."""

    def __init__(self, detail: Any):
        super().__init__(str(detail))
        self.detail = detail


async def load_profiles(organization_id: int) -> ModelProviderProfilesDocument:
    row = await db_client.get_configuration(organization_id, _KEY)
    if row is None or not row.value:
        return ModelProviderProfilesDocument()
    return ModelProviderProfilesDocument.model_validate(row.value)


def _document_from_stored_value(value: Any) -> ModelProviderProfilesDocument:
    if not value:
        return ModelProviderProfilesDocument()
    return ModelProviderProfilesDocument.model_validate(value)


def mask_profile(profile: ModelProviderProfile) -> ModelProviderProfileResponse:
    parsed = parse_profile_config(profile.service, profile.config)
    return ModelProviderProfileResponse(
        name=profile.name,
        service=profile.service,
        config=mask_service_config(parsed) or {},
    )


async def list_profiles(organization_id: int) -> list[ModelProviderProfile]:
    return (await load_profiles(organization_id)).profiles


async def get_profile(
    organization_id: int, service: str, name: str
) -> ModelProviderProfile:
    for profile in (await load_profiles(organization_id)).profiles:
        if profile.service == service and profile.name == name:
            return profile
    raise ProfileNotFoundError(f"No {service} profile named '{name}'")


def _prepare_config(service: str, config: dict) -> dict:
    """Validate the shape of ``config`` and refuse masked secrets."""
    try:
        parsed = parse_profile_config(service, copy.deepcopy(config))
        normalised = parsed.model_dump(mode="json", exclude_none=True)
        check_for_masked_keys(
            EffectiveAIModelConfiguration.model_validate(
                {service: normalised, "is_realtime": service == "realtime"}
            )
        )
    except (ValueError, ValidationError) as exc:
        raise ProfileConfigError(_error_detail(exc)) from exc
    return normalised


def _error_detail(exc: Exception) -> Any:
    if isinstance(exc, ValidationError):
        return [
            {"loc": list(err["loc"]), "msg": err["msg"]}
            for err in exc.errors(include_url=False, include_context=False)
        ]
    return exc.args[0] if exc.args else str(exc)


async def _validate_credentials(
    organization_id: int, service: str, config: dict, created_by: str | None
) -> None:
    """Run the same provider credential check the default configuration uses."""
    parsed = parse_profile_config(service, config)
    try:
        await UserConfigurationValidator().validate_single_service(
            parsed,
            service,
            organization_id=organization_id,
            created_by=created_by,
        )
    except ValueError as exc:
        raise ProfileConfigError(_error_detail(exc)) from exc


async def create_profile(
    organization_id: int,
    *,
    name: str,
    service: str,
    config: dict,
    created_by: str | None = None,
) -> ModelProviderProfile:
    try:
        validate_profile_name(name)
    except ValueError as exc:
        raise ProfileConfigError(str(exc)) from exc
    normalised = _prepare_config(service, config)

    # Optimistic pre-check for a fast, clear error before the (network)
    # credential check below -- the real, authoritative check happens under
    # the row lock in `_mutate`, so this can't itself cause a lost write.
    document = await load_profiles(organization_id)
    if any(p.service == service and p.name == name for p in document.profiles):
        raise ProfileAlreadyExistsError(f"A {service} profile named '{name}' exists")

    await _validate_credentials(organization_id, service, normalised, created_by)

    profile = ModelProviderProfile(name=name, service=service, config=normalised)

    def _mutate(current_value: Any) -> dict:
        current = _document_from_stored_value(current_value)
        if any(p.service == service and p.name == name for p in current.profiles):
            # Someone else created it between our pre-check and this lock.
            raise ProfileAlreadyExistsError(
                f"A {service} profile named '{name}' exists"
            )
        current.profiles.append(profile)
        return current.model_dump(mode="json", exclude_none=True)

    await db_client.upsert_configuration_with_lock(organization_id, _KEY, _mutate)
    return profile


async def update_profile(
    organization_id: int,
    *,
    service: str,
    name: str,
    config: dict,
    created_by: str | None = None,
) -> ModelProviderProfile:
    document = await load_profiles(organization_id)
    index = next(
        (
            i
            for i, p in enumerate(document.profiles)
            if p.service == service and p.name == name
        ),
        None,
    )
    if index is None:
        raise ProfileNotFoundError(f"No {service} profile named '{name}'")

    # Optimistic merge + (network) credential check for a fast, clear error
    # before the lock. What actually gets persisted is re-merged under the
    # lock in `_mutate`, against the *then-current* stored secret -- not
    # this possibly-stale read -- so a masked/omitted key in this request
    # can never silently restore a key another writer just rotated away in
    # between (see the comment in `_mutate`).
    optimistic_merged = merge_service_secrets(
        copy.deepcopy(config), document.profiles[index].config
    )
    optimistic_normalised = _prepare_config(service, optimistic_merged)
    await _validate_credentials(
        organization_id, service, optimistic_normalised, created_by
    )

    persisted: list[ModelProviderProfile] = []

    def _mutate(current_value: Any) -> dict:
        current = _document_from_stored_value(current_value)
        idx = next(
            (
                i
                for i, p in enumerate(current.profiles)
                if p.service == service and p.name == name
            ),
            None,
        )
        if idx is None:
            raise ProfileNotFoundError(f"No {service} profile named '{name}'")
        # Re-merge against the config the lock is actually protecting.
        authoritative_merged = merge_service_secrets(
            copy.deepcopy(config), current.profiles[idx].config
        )
        authoritative_normalised = _prepare_config(service, authoritative_merged)
        profile = ModelProviderProfile(
            name=name, service=service, config=authoritative_normalised
        )
        persisted.append(profile)
        current.profiles[idx] = profile
        return current.model_dump(mode="json", exclude_none=True)

    await db_client.upsert_configuration_with_lock(organization_id, _KEY, _mutate)
    return persisted[0]


async def delete_profile(organization_id: int, *, service: str, name: str) -> None:
    def _mutate(current_value: Any) -> dict:
        current = _document_from_stored_value(current_value)
        remaining = [
            p for p in current.profiles if not (p.service == service and p.name == name)
        ]
        if len(remaining) == len(current.profiles):
            raise ProfileNotFoundError(f"No {service} profile named '{name}'")
        current.profiles = remaining
        return current.model_dump(mode="json", exclude_none=True)

    await db_client.upsert_configuration_with_lock(organization_id, _KEY, _mutate)
