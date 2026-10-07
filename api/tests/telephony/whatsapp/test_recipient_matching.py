"""Tests for matching a WhatsApp recipient number against parked campaign leads.

is_same_recipient_number is a *consent* gate: a match lets a permission
webhook activate or fail a parked campaign run. Campaign leads are guaranteed
to be stored as international numbers (CampaignSourceSyncService
.validate_source_data, api/services/campaign/source_sync.py, rejects any CSV
row whose phone_number does not start with "+"), and WhatsApp webhook
recipients arrive as a Meta ``wa_id``, which is always a full international
number too. Because both sides are guaranteed a country code, the matcher
requires canonical digit equality and deliberately does NOT infer a missing
one - a number missing its country code is a different, unverifiable
subscriber, not a formatting variant of one.
"""

import re
from unittest import TestCase

from api.db.campaign_client import is_same_recipient_number
from api.utils.telephony_address import normalize_telephony_address


def _matches(candidate: str, target: str) -> bool:
    target_digits = re.sub(r"\D", "", target)
    try:
        target_canonical = normalize_telephony_address(target).canonical
    except Exception:
        target_canonical = f"+{target_digits}"
    return is_same_recipient_number(
        candidate,
        target_digits=target_digits,
        target_canonical=target_canonical,
        target_no_plus=target_canonical.lstrip("+"),
    )


class TestIsSameRecipientNumber(TestCase):
    def test_matches_formatting_variants(self):
        for candidate in (
            "+1 (555) 123-4567",
            "1-555-123-4567",
            "15551234567",
            "+15551234567",
        ):
            with self.subTest(candidate=candidate):
                self.assertTrue(_matches(candidate, "+15551234567"))

    def test_matches_exact_equal_numbers(self):
        self.assertTrue(_matches("+919876543210", "+919876543210"))
        self.assertTrue(_matches("+33 6 12 34 56 78", "+33612345678"))

    def test_rejects_missing_country_code(self):
        """Deliberate change vs. the old suffix heuristic.

        A lead stored without its country code no longer reactivates against
        a webhook number that carries one (and vice versa): "+441234567890"
        and "+1234567890" may be unrelated subscribers, and a permission
        grant/denial from one must never move the other's run. Both sides are
        guaranteed a country code at ingest/webhook time (see the module
        docstring), so this should not arise for data that went through the
        normal CSV ingest path. If a legacy or malformed row without a country
        code ever needs to reactivate, the fix is to normalize it at ingest
        with a country hint (a campaign- or lead-level country field, via
        normalize_telephony_address(raw, country_hint=...) in
        api/utils/telephony_address.py) - not to relax this matcher.
        """
        self.assertFalse(_matches("5551234567", "+15551234567"))
        self.assertFalse(_matches("+919876543210", "9876543210"))
        self.assertFalse(_matches("+441234567890", "+1234567890"))

    def test_rejects_different_subscriber(self):
        self.assertFalse(_matches("+15559994567", "+15551234567"))

    def test_rejects_unrelated_number_sharing_a_long_suffix(self):
        """A shared tail is coincidence, not the same lead, once both sides
        are full international numbers."""
        self.assertFalse(_matches("+442079461234567", "+15551234567"))
        self.assertFalse(_matches("+8613800001234567", "+15551234567"))

    def test_rejects_short_and_empty_candidates(self):
        self.assertFalse(_matches("", "+15551234567"))
        self.assertFalse(_matches("123456", "+15551234567"))
