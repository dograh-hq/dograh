import unittest

from api.utils.telephony_address import (
    canonicalize_e164,
    is_e164,
    normalize_telephony_address,
)


class TestTelephonyAddress(unittest.TestCase):
    def test_is_e164_valid_numbers(self):
        """Valid E.164 numbers with '+' and 8-15 digits starting with non-zero."""
        self.assertTrue(is_e164("+14155552671"))
        self.assertTrue(is_e164("+917505327482"))
        self.assertTrue(is_e164("+442071838750"))
        self.assertTrue(is_e164("+84912345678"))
        self.assertTrue(is_e164("+201012345678"))
        self.assertTrue(is_e164("+2348012345678"))

    def test_is_e164_rejects_surrounding_whitespace(self):
        """is_e164 must strictly reject surrounding whitespace rather than silently tolerating it."""
        self.assertFalse(is_e164(" +14155552671"))
        self.assertFalse(is_e164("+14155552671 "))
        self.assertFalse(is_e164(" +14155552671 "))
        self.assertFalse(is_e164("\t+14155552671\n"))

    def test_is_e164_rejects_non_ascii_digits(self):
        """E.164 requires ASCII 0-9 digits; non-ASCII decimal digits must be rejected."""
        self.assertFalse(is_e164("+1١٢٣٤٥٦٧٨"))
        self.assertFalse(is_e164("+91७५०५३२७४८२"))

    def test_is_e164_rejects_extra_plus_signs(self):
        """Numbers with duplicate or misplaced '+' must be rejected."""
        self.assertFalse(is_e164("+1++4155552671"))
        self.assertFalse(is_e164("++14155552671"))
        self.assertFalse(is_e164("+1415+5552671"))
        self.assertFalse(is_e164("+"))
        self.assertFalse(is_e164("++"))

    def test_is_e164_rejects_unnormalized_punctuation_and_whitespace(self):
        """Numbers containing internal spaces, parentheses, hyphens must be rejected."""
        self.assertFalse(is_e164("+1 (415) 555-2671"))
        self.assertFalse(is_e164("+1-415-555-2671"))
        self.assertFalse(is_e164("+1 415 555 2671"))
        self.assertFalse(is_e164("+91 7505 327482"))

    def test_is_e164_rejects_missing_plus_or_invalid_digits(self):
        """Numbers without leading '+', starting with zero, or with letters must be rejected."""
        self.assertFalse(is_e164("14155552671"))
        self.assertFalse(is_e164("917505327482"))
        self.assertFalse(is_e164("+0123456789"))
        self.assertFalse(is_e164("+1415555abcd"))
        self.assertFalse(is_e164(""))
        self.assertFalse(is_e164(None))

    def test_canonicalize_e164_accepts_formatted_numbers(self):
        """A formatted lead is the same number, so ingest stores the strict form.

        Campaign upload accepts anything starting with "+", but the WhatsApp
        dial path requires strict E.164 - without this, a valid contact was
        accepted at upload and failed at dial time.
        """
        self.assertEqual(canonicalize_e164("+44 7123 456789"), "+447123456789")
        self.assertEqual(canonicalize_e164("+1 (415) 555-2671"), "+14155552671")
        self.assertEqual(canonicalize_e164("+1-415-555-2671"), "+14155552671")
        self.assertEqual(canonicalize_e164(" +14155552671 "), "+14155552671")
        # Already canonical values pass through untouched.
        self.assertEqual(canonicalize_e164("+14155552671"), "+14155552671")

    def test_canonicalize_e164_refuses_what_is_not_a_number(self):
        """Only formatting is removed - nothing is guessed."""
        self.assertIsNone(canonicalize_e164("14155552671"))  # no country code
        self.assertIsNone(canonicalize_e164("++14155552671"))
        self.assertIsNone(canonicalize_e164("+1 (415) 555-267a"))
        self.assertIsNone(canonicalize_e164("+0415555267"))  # zero country code
        self.assertIsNone(canonicalize_e164(""))
        self.assertIsNone(canonicalize_e164(None))

    def test_normalize_telephony_address_still_handles_pstn(self):
        """normalize_telephony_address continues to normalize formatted PSTN inputs."""
        normalized = normalize_telephony_address("+1 (415) 555-2671")
        self.assertEqual(normalized.canonical, "+14155552671")
        self.assertEqual(normalized.address_type, "pstn")


if __name__ == "__main__":
    unittest.main()
