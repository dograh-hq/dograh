"""Which destinations a campaign upload accepts.

The rule belongs to the provider that will dial the file. A carrier hands the
call to the PSTN and rejects anything that is not a routable number, so
checking E.164 at upload turns a whole failed campaign into one corrected file.
A PBX dials extensions, SIP URIs and dial strings naming a trunk, and demanding
E.164 there rejects the only addresses its customer has.
"""

import pytest

from api.services.campaign.source_sync import CampaignSourceSyncService
from api.services.telephony.outbound_readiness import requires_e164_destinations

HEADERS = ["phone_number", "name"]


def _validate(rows, *, require_e164):
    return CampaignSourceSyncService.validate_source_data(
        HEADERS, rows, require_e164=require_e164
    )


# --------------------------------------------------------------------------
# Carrier policy
# --------------------------------------------------------------------------


def test_carrier_upload_still_requires_a_country_code():
    result = _validate([["+15550001", "a"], ["5550002", "b"]], require_e164=True)
    assert not result.is_valid
    assert result.error.invalid_rows == [3]
    assert "country code" in result.error.message


def test_carrier_upload_accepts_e164():
    assert _validate([["+15550001", "a"]], require_e164=True).is_valid


# --------------------------------------------------------------------------
# PBX policy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "destination",
    [
        "1001",  # extension
        "+966500000000",  # E.164 still fine
        "sip:1001@pbx.local",  # SIP URI
        "PJSIP/1001@my-trunk",  # dial string naming a trunk
        "Local/1001@from-internal",
    ],
)
def test_pbx_upload_accepts_any_dialable_address(destination):
    assert _validate([[destination, "a"]], require_e164=False).is_valid


@pytest.mark.parametrize(
    "destination",
    [
        "+1555 0001",  # whitespace splits the dial string
        "+15550001,+15550002",  # comma separates arguments
        "1001&1002",  # ampersand forks the call
    ],
)
def test_an_address_that_is_not_one_address_is_rejected(destination):
    """Relaxing E.164 is not "anything goes" — these are malformed rows."""
    result = _validate([[destination, "a"]], require_e164=False)
    assert not result.is_valid
    assert result.error.invalid_rows == [2]


def test_duplicate_rule_is_unchanged_by_the_policy():
    result = _validate([["1001", "a"], ["1001", "b"]], require_e164=False)
    assert not result.is_valid
    assert "unique" in result.error.message


def test_blank_rows_are_skipped_not_rejected():
    """A trailing empty row drops out at sync time; it is not an error."""
    assert _validate([["1001", "a"], ["", ""]], require_e164=False).is_valid


# --------------------------------------------------------------------------
# Where the policy comes from
# --------------------------------------------------------------------------


class _Row:
    def __init__(self, provider: str):
        self.provider = provider


class _DB:
    def __init__(self, row):
        self._row = row

    async def get_telephony_configuration_for_org(self, config_id, organization_id):
        return self._row


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,expected",
    [("ari", False), ("twilio", True)],
)
async def test_policy_is_read_from_the_configuration_that_will_dial(provider, expected):
    assert await requires_e164_destinations(1, 1, db=_DB(_Row(provider))) is expected


@pytest.mark.asyncio
async def test_unknown_configuration_keeps_the_strict_rule():
    """Never accept a destination on the strength of a provider we can't see."""
    assert await requires_e164_destinations(1, 1, db=_DB(None)) is True
    assert await requires_e164_destinations(1, 1, db=_DB(_Row("gone"))) is True
