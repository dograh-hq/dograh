"""Organization-scoped translation of dispositions into an org's own codes.

Covers the mapping itself and the normalization applied when it is saved. The
places that *use* it -- the pipeline engine, the telephony status callback, the
external-PBX write-back -- are covered by their own suites.
"""

from unittest.mock import AsyncMock, patch

import pytest

from api.schemas.organization_preferences import (
    MAX_DISPOSITION_CODE_LENGTH,
    MAX_DISPOSITION_MAPPING_ENTRIES,
    OrganizationPreferences,
)
from api.services.workflow.disposition_mapping import (
    apply_disposition_mapping,
    get_disposition_mapping,
    map_disposition,
)


def _patch_preferences(preferences: OrganizationPreferences):
    return patch(
        "api.services.workflow.disposition_mapping.get_organization_preferences",
        AsyncMock(return_value=preferences),
    )


# --------------------------------------------------------------------------
# Applying a mapping
# --------------------------------------------------------------------------


def test_a_mapped_disposition_becomes_the_organizations_code():
    assert apply_disposition_mapping({"user_hangup": "HUNGUP"}, "user_hangup") == (
        "HUNGUP"
    )


def test_an_unmapped_disposition_passes_through():
    """A mapping is a set of overrides, not an allowlist.

    Anything the organization did not map keeps behaving exactly as it did
    before the mapping existed, which is what makes enabling the setting safe.
    """
    assert apply_disposition_mapping({"user_hangup": "HUNGUP"}, "user_qualified") == (
        "user_qualified"
    )


def test_lookup_ignores_case_and_surrounding_whitespace():
    """Free-text end-call reasons arrive however the agent spelled them.

    The model chooses that spelling at call time, so the person configuring the
    mapping cannot predict its casing.
    """
    mapping = {"do_not_call": "DNC"}

    assert apply_disposition_mapping(mapping, "  Do_Not_Call  ") == "DNC"
    assert apply_disposition_mapping(mapping, "DO_NOT_CALL") == "DNC"


@pytest.mark.parametrize("disposition", ["", None])
def test_an_empty_disposition_is_returned_unchanged(disposition):
    assert apply_disposition_mapping({"user_hangup": "HUNGUP"}, disposition) == (
        disposition
    )


def test_no_mapping_leaves_every_disposition_alone():
    assert apply_disposition_mapping({}, "user_hangup") == "user_hangup"


# --------------------------------------------------------------------------
# Loading a mapping
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_disabled_mapping_is_not_applied():
    """The toggle is what decides, not whether a mapping was ever configured.

    Turning the setting off has to stop the translation without discarding the
    entries someone spent time on.
    """
    preferences = OrganizationPreferences(
        disposition_mapping_enabled=False,
        disposition_mapping={"user_hangup": "HUNGUP"},
    )

    with _patch_preferences(preferences):
        assert await get_disposition_mapping(7) == {}
        assert await map_disposition(7, "user_hangup") == "user_hangup"


@pytest.mark.asyncio
async def test_an_enabled_mapping_is_applied():
    preferences = OrganizationPreferences(
        disposition_mapping_enabled=True,
        disposition_mapping={"user_hangup": "HUNGUP"},
    )

    with _patch_preferences(preferences):
        assert await map_disposition(7, "user_hangup") == "HUNGUP"


@pytest.mark.asyncio
async def test_a_missing_organization_has_no_mapping():
    """Reached by callers whose run has no resolvable organization."""
    assert await get_disposition_mapping(None) == {}
    assert await map_disposition(None, "user_hangup") == "user_hangup"


# --------------------------------------------------------------------------
# What gets stored
# --------------------------------------------------------------------------


def test_identity_rows_are_not_stored():
    """The editor submits every known disposition, mapped to itself by default.

    Storing those would freeze the mapping against today's disposition catalog:
    a disposition added to the platform later would be absent from the stored
    config and pass through anyway -- exactly what an identity row means.
    """
    preferences = OrganizationPreferences.model_validate(
        {
            "disposition_mapping": {
                "user_hangup": "HUNGUP",
                "user_qualified": "user_qualified",
            }
        }
    )

    assert preferences.disposition_mapping == {"user_hangup": "HUNGUP"}


def test_codes_are_trimmed_and_blank_rows_dropped():
    """The editor lets a row be added and left empty; that is not a mapping."""
    preferences = OrganizationPreferences.model_validate(
        {
            "disposition_mapping": {
                "  callback_requested  ": "  CALLBK  ",
                "wrong_info": "   ",
                "   ": "WRINFO",
            }
        }
    )

    assert preferences.disposition_mapping == {"callback_requested": "CALLBK"}


def test_an_absent_mapping_defaults_to_empty():
    assert OrganizationPreferences().disposition_mapping == {}
    assert (
        OrganizationPreferences.model_validate(
            {"disposition_mapping": None}
        ).disposition_mapping
        == {}
    )


@pytest.mark.parametrize(
    "mapping",
    [
        {"user_hangup": "H" * (MAX_DISPOSITION_CODE_LENGTH + 1)},
        {"u" * (MAX_DISPOSITION_CODE_LENGTH + 1): "HUNGUP"},
    ],
)
def test_an_overlong_code_is_rejected(mapping):
    with pytest.raises(ValueError):
        OrganizationPreferences.model_validate({"disposition_mapping": mapping})


def test_too_many_entries_are_rejected():
    mapping = {
        f"disposition_{index}": f"C{index}"
        for index in range(MAX_DISPOSITION_MAPPING_ENTRIES + 1)
    }

    with pytest.raises(ValueError):
        OrganizationPreferences.model_validate({"disposition_mapping": mapping})


def test_non_string_codes_are_rejected():
    with pytest.raises(ValueError):
        OrganizationPreferences.model_validate({"disposition_mapping": {"a": 1}})
