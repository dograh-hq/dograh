import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

if "fastapi" not in sys.modules:
    fastapi_mock = MagicMock()

    class HTTPException(Exception):
        def __init__(self, status_code: int = 400, detail: str = ""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)

    fastapi_mock.HTTPException = HTTPException
    sys.modules["fastapi"] = fastapi_mock

if "starlette" not in sys.modules:
    sys.modules["starlette"] = MagicMock()
    sys.modules["starlette.responses"] = MagicMock()

if "api.constants" not in sys.modules:
    const_mock = MagicMock()
    const_mock.COUNTRY_CODES = {}
    sys.modules["api.constants"] = const_mock

# Load restrictions.py directly
RESTRICTIONS_PATH = (
    Path(__file__).resolve().parents[3]
    / "services"
    / "telephony"
    / "providers"
    / "whatsapp"
    / "restrictions.py"
)
spec = importlib.util.spec_from_file_location("restrictions", RESTRICTIONS_PATH)
restrictions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restrictions)

RESTRICTED_BIC_COUNTRIES = restrictions.RESTRICTED_BIC_COUNTRIES
RESTRICTED_BIC_PREFIXES = restrictions.RESTRICTED_BIC_PREFIXES
is_restricted_country = restrictions.is_restricted_country
validate_destination_country = restrictions.validate_destination_country
HTTPException = getattr(restrictions, "HTTPException", Exception)


class TestWhatsAppRestrictions(unittest.TestCase):
    """Test suite for WhatsApp destination country restrictions."""

    def test_restricted_prefixes_defined(self):
        """Ensure all required restricted country prefixes are configured."""
        self.assertIn("+1", RESTRICTED_BIC_PREFIXES)
        self.assertIn("+20", RESTRICTED_BIC_PREFIXES)
        self.assertIn("+84", RESTRICTED_BIC_PREFIXES)
        self.assertIn("+234", RESTRICTED_BIC_PREFIXES)

    def test_us_and_canada_are_restricted(self):
        """Test +1 numbers (US and Canada) are flagged as restricted."""
        is_restr, reason = is_restricted_country("+16502530000")
        self.assertTrue(is_restr)
        self.assertIn("United States and Canada", reason)

        # Bare numbers are no longer classified here at all: a country code is
        # now required upstream (campaign ingest via validate_source_data, and
        # is_e164 on the test-call / public API routes), so this function never
        # has to guess. "16502530000" is still never dialled - it is rejected at
        # the edge instead of guessed at by leading digits.
        is_restr_no_plus, _ = is_restricted_country("16502530000")
        self.assertFalse(is_restr_no_plus)

        with self.assertRaises(HTTPException) as ctx:
            validate_destination_country("+14155552671")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("not permitted", ctx.exception.detail)

    def test_egypt_is_restricted(self):
        """Test +20 numbers (Egypt) are flagged as restricted."""
        is_restr, reason = is_restricted_country("+201012345678")
        self.assertTrue(is_restr)
        self.assertIn("Egypt", reason)

        with self.assertRaises(HTTPException) as ctx:
            validate_destination_country("+201012345678")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_vietnam_is_restricted(self):
        """Test +84 numbers (Vietnam) are flagged as restricted."""
        is_restr, reason = is_restricted_country("+84912345678")
        self.assertTrue(is_restr)
        self.assertIn("Vietnam", reason)

        with self.assertRaises(HTTPException) as ctx:
            validate_destination_country("+84912345678")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_nigeria_is_restricted(self):
        """Test +234 numbers (Nigeria) are flagged as restricted."""
        is_restr, reason = is_restricted_country("+2348012345678")
        self.assertTrue(is_restr)
        self.assertIn("Nigeria", reason)

        with self.assertRaises(HTTPException) as ctx:
            validate_destination_country("+2348012345678")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_allowed_countries(self):
        """Test non-restricted countries pass validation without error."""
        allowed = [
            "+447911123456",  # UK
            "+919876543210",  # India
            "+4915123456789",  # Germany
            "+5511999999999",  # Brazil
            "+819012345678",  # Japan
            "+61412345678",  # Australia
        ]
        for number in allowed:
            is_restr, reason = is_restricted_country(number)
            self.assertFalse(is_restr, f"{number} should not be restricted")
            self.assertIsNone(reason)
            # Should not raise
            validate_destination_country(number)

    def test_bare_nanp_area_codes_not_confused_with_egypt_or_nigeria(self):
        """Bare 10-digit NANP numbers must not be misread as Egypt/Nigeria."""
        for number in ("2015551234", "2345551234"):
            is_restr, reason = is_restricted_country(number)
            self.assertFalse(is_restr, f"{number} should not be restricted")
            self.assertIsNone(reason)

    def test_malformed_plus_signs_do_not_bypass_restricted_numbers(self):
        """A duplicated/misplaced '+' must not let a restricted number through.

        The outbound dial path strips ALL non-digit characters (including
        every '+') before dialling, so a stray extra '+' must not change
        whether the number is classified as restricted.
        """
        for number in ("++201555123456", "+ +201555123456", "1+234555123456"):
            is_restr, reason = is_restricted_country(number)
            self.assertTrue(is_restr, f"{number} should be restricted")
            self.assertIsNotNone(reason)

    def test_bare_plus_only_input_is_not_restricted(self):
        """Degenerate '+'/'++' input with no digits can't be classified."""
        for number in ("+", "++"):
            is_restr, reason = is_restricted_country(number)
            self.assertFalse(is_restr)
            self.assertIsNone(reason)

    def test_bare_numbers_are_not_classified_at_all(self):
        """Bare numbers no longer reach a verdict here - the country code is required upstream.

        The collision this used to fail closed on is unresolvable: a bare
        11-digit number starting with "1" is either a US number carrying its
        country code or a Chinese mobile without one, and nothing in the digits
        separates them. Rather than pick which way to be wrong, every path into
        an outbound call now requires E.164 (validate_source_data at campaign
        ingest; is_e164 on the test-call and public API routes), so the
        ambiguous input never arrives.
        """
        for bare in ("16502530000", "13800138000", "2015551234", "2345551234"):
            with self.subTest(number=bare):
                is_restr, reason = is_restricted_country(bare)
                self.assertFalse(is_restr)
                self.assertIsNone(reason)

    def test_malformed_plus_cannot_smuggle_a_restricted_destination(self):
        """Extra "+" characters must not bypass the gate.

        The dial path strips every non-digit and redials f"+{digits}", so a
        number that would actually be placed to a restricted country has to be
        caught here regardless of how its "+" is written.
        """
        for malformed in ("++201555123456", "+ +201555123456", "+20 1555 123456"):
            with self.subTest(number=malformed):
                is_restr, reason = is_restricted_country(malformed)
                self.assertTrue(is_restr)
                self.assertIn("Egypt", reason)


if __name__ == "__main__":
    unittest.main()
