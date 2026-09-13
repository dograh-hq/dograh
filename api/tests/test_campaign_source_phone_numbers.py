"""Campaign ingest holds every lead to one phone-number form.

Upload validation and the dial path used to disagree about the same cell: a
number was accepted as long as it started with "+", stored exactly as typed,
and then rejected at dial time by providers that require strict E.164. Ingest
now canonicalises, which makes the stored value the thing to validate against -
including when deciding whether two rows are the same contact.
"""

import unittest

from api.services.campaign.source_sync import CampaignSourceSyncService
from api.services.campaign.sources.csv import CSVSyncService


class TestCampaignSourcePhoneNumbers(unittest.TestCase):
    def test_duplicates_are_detected_across_formatting_variants(self):
        """Two spellings of one number are one contact, not two leads.

        Comparing the raw cells let both through, and the campaign then placed
        two calls to the same person.
        """
        result = CampaignSourceSyncService.validate_source_data(
            ["phone_number", "name"],
            [
                ["+44 7123 456789", "Ada"],
                ["+447123456789", "Ada again"],
            ],
        )
        self.assertFalse(result.is_valid)
        self.assertIn("Duplicate", result.error.message)
        self.assertEqual(result.error.invalid_rows, [3])

    def test_distinct_numbers_still_pass(self):
        result = CampaignSourceSyncService.validate_source_data(
            ["phone_number"],
            [["+44 7123 456789"], ["+447123456780"]],
        )
        self.assertTrue(result.is_valid)

    def test_row_is_stored_in_canonical_form(self):
        """What is stored is what will be dialled."""
        context = CSVSyncService._build_context_variables(
            ["phone_number", "name"], ["+44 (0) 20 7946 0958", "Ada"]
        )
        # The bracketed trunk prefix is dropped, not kept as a leading zero:
        # "+4402079460958" looks like valid E.164 and dials the wrong number.
        self.assertEqual(context["phone_number"], "+442079460958")

    def test_row_that_cannot_be_canonicalised_is_left_alone(self):
        """Validation reports it the same way it always did."""
        context = CSVSyncService._build_context_variables(
            ["phone_number"], ["not-a-number"]
        )
        self.assertEqual(context["phone_number"], "not-a-number")


if __name__ == "__main__":
    unittest.main()
