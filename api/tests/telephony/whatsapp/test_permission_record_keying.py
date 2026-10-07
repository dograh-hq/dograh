"""Tests for the format a WhatsApp permission row is keyed by.

uq_whatsapp_perm_config_recipient constrains the stored recipient string, so it
only prevents duplicates if every writer stores one agreed spelling. These pin
that spelling down, and that lookups still find it.
"""

from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock

from api.db.telephony_configuration_client import (
    UNSET,
    _apply_permission_updates,
    _canonical_recipient_number,
    _get_recipient_number_candidates,
    _select_permission_row,
    _supplied_or_none,
)

EQUIVALENT_SPELLINGS = [
    "+447123456789",
    "447123456789",
    "+44 7123 456789",
    "+44-7123-456789",
    " +447123456789 ",
]


class TestCanonicalRecipientNumber(TestCase):
    def test_equivalent_spellings_collapse_to_one_key(self):
        keys = {_canonical_recipient_number(s) for s in EQUIVALENT_SPELLINGS}
        self.assertEqual(
            len(keys), 1, f"equivalent numbers must share one stored key, got {keys}"
        )

    def test_stored_key_is_reachable_from_every_spelling(self):
        """A row written from one spelling has to be found by a lookup from any other."""
        stored = _canonical_recipient_number("+44 7123 456789")
        for spelling in EQUIVALENT_SPELLINGS:
            with self.subTest(lookup=spelling):
                self.assertIn(stored, _get_recipient_number_candidates(spelling))

    def test_distinct_numbers_do_not_collapse(self):
        self.assertNotEqual(
            _canonical_recipient_number("+447123456789"),
            _canonical_recipient_number("+447123456780"),
        )

    def test_unparseable_input_still_yields_a_stable_key(self):
        self.assertEqual(
            _canonical_recipient_number("44 (7123) 456789"),
            _canonical_recipient_number("447123456789"),
        )


class TestSelectPermissionRow(IsolatedAsyncioTestCase):
    """Rows predating canonical storage can still collide; readers must agree on one."""

    @staticmethod
    def _session_returning(rows):
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        session.execute = AsyncMock(return_value=result)
        return session

    async def test_returns_none_when_no_rows(self):
        session = self._session_returning([])
        self.assertIsNone(
            await _select_permission_row(session, MagicMock(), "+447123456789")
        )

    async def test_duplicates_resolve_to_the_same_row_for_every_reader(self):
        rows = [MagicMock(id=7), MagicMock(id=3), MagicMock(id=11)]
        session = self._session_returning(rows)

        picked = await _select_permission_row(session, MagicMock(), "+447123456789")

        # The query is ordered by id, so whatever the DB returns first is the
        # row every caller gets - the point is that it is not arbitrary.
        self.assertIs(picked, rows[0])
        session.execute.return_value.scalars.assert_called_once()

    async def test_orders_the_query_by_id(self):
        query = MagicMock()
        session = self._session_returning([MagicMock(id=1)])

        await _select_permission_row(session, query, "+447123456789")

        query.order_by.assert_called_once()
        session.execute.assert_awaited_once_with(query.order_by.return_value)


class _Row:
    """Stand-in for WhatsAppCallPermissionModel: same attributes, no ORM setup."""

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def _granted_row():
    return _Row(
        status="granted_temporary",
        phone_number_id="old-phone-id",
        permission_type="temporary",
        meta_message_id="wamid.old",
        expires_at="old-expiry",
        granted_at="old-grant",
        updated_at=None,
    )


class TestSuppliedOrNone(TestCase):
    """UNSET is how a caller says "I didn't mention this field"."""

    def test_unset_reads_as_none_for_a_new_row(self):
        self.assertIsNone(_supplied_or_none(UNSET))

    def test_explicit_none_is_not_confused_with_unset(self):
        self.assertIsNone(_supplied_or_none(None))

    def test_a_supplied_value_passes_through(self):
        self.assertEqual(_supplied_or_none("granted_permanent"), "granted_permanent")


class TestApplyPermissionUpdates(TestCase):
    """A denial or revocation must be able to wipe a stale future expiry."""

    def test_omitted_fields_keep_their_stored_value(self):
        row = _granted_row()

        _apply_permission_updates(row, status="denied", now="now")

        self.assertEqual(row.status, "denied")
        self.assertEqual(row.updated_at, "now")
        self.assertEqual(row.phone_number_id, "old-phone-id")
        self.assertEqual(row.permission_type, "temporary")
        self.assertEqual(row.meta_message_id, "wamid.old")
        self.assertEqual(row.expires_at, "old-expiry")
        self.assertEqual(row.granted_at, "old-grant")

    def test_explicit_none_clears_the_field_instead_of_being_ignored(self):
        row = _granted_row()

        _apply_permission_updates(
            row,
            status="denied",
            now="now",
            permission_type=None,
            expires_at=None,
            granted_at=None,
        )

        self.assertIsNone(row.permission_type)
        self.assertIsNone(row.expires_at)
        self.assertIsNone(row.granted_at)
        # A field this call never mentioned is still left alone.
        self.assertEqual(row.meta_message_id, "wamid.old")

    def test_a_supplied_value_overwrites_the_stored_one(self):
        row = _granted_row()

        _apply_permission_updates(
            row, status="granted_permanent", now="now", phone_number_id="new-phone-id"
        )

        self.assertEqual(row.phone_number_id, "new-phone-id")
        self.assertEqual(row.status, "granted_permanent")
