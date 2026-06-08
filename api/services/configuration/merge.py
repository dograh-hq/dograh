from __future__ import annotations

"""Helpers for merging incoming user-configuration updates with what is already
stored, while honouring masked API keys.
"""

import copy
from typing import Dict

from api.schemas.user_configuration import UserConfiguration
from api.services.configuration.masking import (
    MODEL_OVERRIDE_FIELDS,
    SERVICE_SECRET_FIELDS,
    contains_masked_key,
    resolve_masked_api_keys,
)

SERVICE_FIELDS = ("llm", "tts", "stt", "embeddings", "realtime")


def _same_provider(incoming_cfg: dict, existing_cfg: dict) -> bool:
    return not (
        existing_cfg.get("provider") is not None
        and incoming_cfg.get("provider") is not None
        and incoming_cfg.get("provider") != existing_cfg.get("provider")
    )


def _merge_service_secret_fields(
    incoming_cfg: dict,
    existing_cfg: dict,
    *,
    preserve_missing: bool,
    masked_value_preserves_full_secret: bool = False,
) -> dict:
    """Restore existing real secrets when incoming values are masked.

    If ``preserve_missing`` is true, missing incoming secret fields are also
    copied from the existing config. User config updates need that behavior;
    workflow model overrides leave missing secrets blank so later enrichment can
    copy from the current global config.
    """
    if not _same_provider(incoming_cfg, existing_cfg):
        return incoming_cfg

    for secret_field in SERVICE_SECRET_FIELDS:
        if secret_field not in existing_cfg:
            continue

        incoming_secret = incoming_cfg.get(secret_field)
        existing_secret = existing_cfg[secret_field]
        if incoming_secret is not None:
            if contains_masked_key(incoming_secret):
                incoming_cfg[secret_field] = (
                    existing_secret
                    if masked_value_preserves_full_secret
                    else resolve_masked_api_keys(
                        incoming_secret,
                        existing_secret,
                    )
                )
        elif preserve_missing:
            incoming_cfg[secret_field] = existing_secret

    return incoming_cfg


def merge_user_configurations(
    existing: UserConfiguration, incoming_partial: Dict[str, dict]
) -> UserConfiguration:
    """Merge *incoming_partial* onto *existing* and return a new UserConfiguration.

    *incoming_partial* is the body of the PUT request (already `model_dump()`ed or
    extracted via Pydantic `model_dump`).

    Rules:
    1. If a service block is absent in the request, keep the existing one.
    2. If provider unchanged and the api_key field is either missing or equal to
       the masked placeholder, preserve the existing real key.
    3. If provider changes, the incoming api_key is used verbatim (validation
       will fail later if it is missing).
    4. Non-service top-level fields (e.g. `test_phone_number`) are overwritten
       when supplied.
    """

    merged = existing.model_dump(exclude_none=True)

    def _merge_service_block(service_name: str):
        incoming_cfg = incoming_partial.get(service_name)
        if incoming_cfg is None:
            return  # nothing to do

        old_cfg = merged.get(service_name, {})
        if old_cfg:
            incoming_cfg = _merge_service_secret_fields(
                incoming_cfg,
                old_cfg,
                preserve_missing=True,
            )

        merged[service_name] = incoming_cfg

    for service in SERVICE_FIELDS:
        _merge_service_block(service)

    # other simple fields
    if "is_realtime" in incoming_partial:
        merged["is_realtime"] = incoming_partial["is_realtime"]

    if "test_phone_number" in incoming_partial:
        merged["test_phone_number"] = incoming_partial["test_phone_number"]

    if "timezone" in incoming_partial:
        merged["timezone"] = incoming_partial["timezone"]

    return UserConfiguration.model_validate(merged)


def merge_workflow_configuration_secrets(
    incoming_config: dict | None,
    existing_config: dict | None,
) -> dict | None:
    """Restore persisted workflow override secrets when the client sends masks.

    Workflow model overrides intentionally persist real keys so a workflow keeps
    running after the global provider changes. API responses mask those keys, so
    save requests must merge masked placeholders back to the stored real values.

    Unlike user config updates, a missing workflow override secret is not copied
    from the existing workflow config. Missing means "copy from current global"
    during the later enrichment step.
    """
    if not incoming_config or not existing_config:
        return incoming_config

    merged = copy.deepcopy(incoming_config)
    incoming_overrides = merged.get("model_overrides")
    existing_overrides = existing_config.get("model_overrides")
    if not isinstance(incoming_overrides, dict) or not isinstance(
        existing_overrides, dict
    ):
        return merged

    for section in MODEL_OVERRIDE_FIELDS:
        incoming_section = incoming_overrides.get(section)
        existing_section = existing_overrides.get(section)
        if not isinstance(incoming_section, dict) or not isinstance(
            existing_section, dict
        ):
            continue

        incoming_overrides[section] = _merge_service_secret_fields(
            incoming_section,
            existing_section,
            preserve_missing=False,
            masked_value_preserves_full_secret=True,
        )

    return merged
