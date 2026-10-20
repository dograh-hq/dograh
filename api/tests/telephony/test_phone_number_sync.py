"""Importing a provider account's numbers into a telephony configuration."""

from typing import Any, Dict, Iterable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects import postgresql

from api.db import db_client
from api.db.telephony_phone_number_client import (
    PROVIDER_METADATA_KEY,
    TelephonyPhoneNumberConflictError,
)
from api.services.telephony.base import ProviderPhoneNumber
from api.services.telephony.phone_number_sync import (
    PhoneNumberSyncUnsupportedError,
    sync_phone_numbers_from_provider,
)

ORG = 10


def _config(provider: str = "whatsapp", config_id: int = 5) -> MagicMock:
    cfg = MagicMock()
    cfg.id = config_id
    cfg.provider = provider
    cfg.credentials = {"waba_id": "waba_1", "account_sid": "AC1"}
    return cfg


def _row(
    address: str,
    label: str | None = None,
    extra: Dict[str, Any] | None = None,
    row_id: int = 1,
) -> MagicMock:
    row = MagicMock()
    row.id = row_id
    row.address = address
    row.address_normalized = address
    row.label = label
    row.extra_metadata = dict(extra or {})
    return row


def _number(
    address: str, number_id: str, label: str | None = "Example"
) -> ProviderPhoneNumber:
    return ProviderPhoneNumber(
        address=address,
        external_id=number_id,
        label=label,
        metadata={"phone_number_id": number_id},
    )


class _FakeDB:
    """Just enough of the phone-number client for the sync to run against."""

    def __init__(
        self,
        rows: Iterable[MagicMock] = (),
        conflicts: Iterable[tuple] = (),
        taken_elsewhere: Iterable[str] = (),
    ):
        self.rows = list(rows)
        self.conflicts = list(conflicts)
        self.taken_elsewhere = set(taken_elsewhere)
        self.created: list[Dict[str, Any]] = []
        self.refreshed: list[tuple[int, Dict[str, Any]]] = []

    async def list_phone_numbers_for_config(self, config_id: int):
        return list(self.rows)

    async def find_inbound_routing_conflicts(self, **kwargs: Any):
        return list(self.conflicts)

    async def create_phone_number(self, **kwargs: Any):
        if kwargs["address"] in self.taken_elsewhere:
            raise TelephonyPhoneNumberConflictError("duplicate key")
        self.created.append(kwargs)
        return _row(
            kwargs["address"],
            label=kwargs.get("label"),
            extra=kwargs.get("extra_metadata"),
            row_id=100 + len(self.created),
        )

    async def refresh_phone_number_from_provider(
        self, phone_number_id: int, configuration_id: int, **kwargs: Any
    ):
        """Records the call; the merge itself is the real client's and is
        tested below against a session."""
        self.refreshed.append((phone_number_id, kwargs))
        return _row("refreshed", row_id=phone_number_id)


def _provider(numbers: Iterable[ProviderPhoneNumber]) -> MagicMock:
    provider = MagicMock()
    provider.list_provider_phone_numbers = AsyncMock(return_value=list(numbers))
    return provider


async def _sync(db: _FakeDB, numbers: Iterable[ProviderPhoneNumber], config=None):
    return await sync_phone_numbers_from_provider(
        configuration=config or _config(),
        organization_id=ORG,
        provider=_provider(numbers),
        db=db,
    )


class TestPhoneNumberSync(IsolatedAsyncioTestCase):
    async def test_creates_rows_for_numbers_the_account_holds(self):
        db = _FakeDB()
        outcome = await _sync(
            db,
            [_number("+15551234567", "1"), _number("+442079460958", "2", label=None)],
        )

        self.assertEqual(len(outcome.created), 2)
        self.assertEqual(outcome.updated, [])
        self.assertEqual(outcome.skipped, [])
        self.assertEqual(outcome.missing_at_provider, [])
        first, second = db.created
        self.assertEqual(first["organization_id"], ORG)
        self.assertEqual(first["telephony_configuration_id"], 5)
        self.assertEqual(first["address"], "+15551234567")
        self.assertEqual(first["label"], "Example")
        # Provider details live under their own key; the rest of the column
        # is the operator's.
        self.assertEqual(
            first["extra_metadata"], {PROVIDER_METADATA_KEY: {"phone_number_id": "1"}}
        )
        self.assertIsNone(second["label"])

    async def test_refreshes_rows_it_already_has_through_the_locked_merge(self):
        kept = _row("+15551234567", label="Boston", extra={"note": "ops"}, row_id=7)
        db = _FakeDB(rows=[kept])

        outcome = await _sync(db, [_number("+15551234567", "1")])

        self.assertEqual(db.created, [])
        self.assertEqual(len(outcome.updated), 1)
        row_id, kwargs = db.refreshed[0]
        self.assertEqual(row_id, 7)
        self.assertEqual(kwargs["provider_metadata"], {"phone_number_id": "1"})
        self.assertEqual(kwargs["label"], "Example")

    async def test_reports_numbers_the_provider_no_longer_lists_without_touching_them(
        self,
    ):
        gone = _row("+15559999999", row_id=3)
        db = _FakeDB(rows=[gone])

        outcome = await _sync(db, [_number("+15551234567", "1")])

        self.assertEqual(outcome.missing_at_provider, [gone])
        self.assertEqual(db.refreshed, [])
        self.assertEqual(len(outcome.created), 1)

    async def test_skips_a_number_already_routed_under_the_same_account(self):
        other = MagicMock()
        other.name = "Old config"
        other.organization_id = ORG
        db = _FakeDB(conflicts=[(other, _row("+15551234567"))])

        outcome = await _sync(db, [_number("+15551234567", "1")])

        self.assertEqual(db.created, [])
        self.assertEqual(len(outcome.skipped), 1)
        self.assertEqual(outcome.skipped[0].address, "+15551234567")
        self.assertIn("Old config", outcome.skipped[0].reason)

    async def test_skips_a_number_another_configuration_in_the_org_holds(self):
        db = _FakeDB(taken_elsewhere={"+15551234567"})

        outcome = await _sync(db, [_number("+15551234567", "1")])

        self.assertEqual(len(outcome.skipped), 1)
        self.assertIn("another configuration", outcome.skipped[0].reason)
        self.assertEqual(outcome.created, [])

    async def test_duplicate_listings_collapse_to_one_row(self):
        db = _FakeDB()

        outcome = await _sync(
            db, [_number("+1 555 123 4567", "1"), _number("+15551234567", "1")]
        )

        self.assertEqual(len(outcome.created), 1)

    async def test_refuses_a_provider_without_a_listing(self):
        with self.assertRaises(PhoneNumberSyncUnsupportedError):
            await _sync(_FakeDB(), [], config=_config(provider="twilio"))


def _sql(session: AsyncMock) -> str:
    """The one statement the session ran, rendered with its literal values."""
    statement = session.execute.await_args.args[0]
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def _session_with(row: MagicMock | None):
    """An ``async_session`` whose one query returns ``row``."""
    result = MagicMock()
    result.scalars.return_value.first.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__.return_value = session
    return session, patch.object(db_client, "async_session", return_value=ctx)


class TestProviderRefreshOnTheRow(IsolatedAsyncioTestCase):
    """The merge runs against the row's live state, under a lock, so a refresh
    cannot undo an operator edit made after the sync listed the row."""

    async def test_locks_the_row_and_replaces_only_the_provider_key(self):
        row = _row("+15551234567", label="Boston", extra={"note": "ops"}, row_id=7)
        session, patched = _session_with(row)
        with patched:
            updated = await db_client.refresh_phone_number_from_provider(
                7, 5, provider_metadata={"phone_number_id": "1"}, label="Example"
            )

        self.assertIs(updated, row)
        self.assertEqual(
            row.extra_metadata,
            {"note": "ops", PROVIDER_METADATA_KEY: {"phone_number_id": "1"}},
        )
        self.assertEqual(row.label, "Boston")
        session.commit.assert_awaited_once()
        self.assertIn("FOR UPDATE", _sql(session))

    async def test_fills_in_a_label_only_while_the_row_has_none(self):
        row = _row(
            "+15551234567", extra={PROVIDER_METADATA_KEY: {"phone_number_id": "1"}}
        )
        session, patched = _session_with(row)
        with patched:
            updated = await db_client.refresh_phone_number_from_provider(
                1, 5, provider_metadata={"phone_number_id": "1"}, label="Example"
            )

        self.assertIs(updated, row)
        self.assertEqual(row.label, "Example")
        session.commit.assert_awaited_once()

    async def test_leaves_a_current_row_alone(self):
        row = _row(
            "+15551234567",
            label="Boston",
            extra={PROVIDER_METADATA_KEY: {"phone_number_id": "1"}},
        )
        session, patched = _session_with(row)
        with patched:
            updated = await db_client.refresh_phone_number_from_provider(
                1, 5, provider_metadata={"phone_number_id": "1"}, label="Example"
            )

        self.assertIsNone(updated)
        session.commit.assert_not_awaited()

    async def test_ignores_a_row_of_another_configuration(self):
        """Ownership is part of the query, not a check after the fact."""
        session, patched = _session_with(None)
        with patched:
            updated = await db_client.refresh_phone_number_from_provider(
                1, 5, provider_metadata={"phone_number_id": "1"}
            )
        self.assertIsNone(updated)
        session.commit.assert_not_awaited()
        sql = _sql(session)
        self.assertIn("telephony_phone_numbers.id = 1", sql)
        self.assertIn("telephony_phone_numbers.telephony_configuration_id = 5", sql)


class TestOperatorUpdateKeepsProviderDetails(IsolatedAsyncioTestCase):
    """The operator's edit path is the other side of the same race: it must
    not replace what a refresh wrote, and it must read the row under the lock."""

    async def test_replaces_operator_keys_but_keeps_the_provider_entry(self):
        row = _row(
            "+15551234567",
            extra={"note": "old", PROVIDER_METADATA_KEY: {"phone_number_id": "1"}},
            row_id=7,
        )
        session, patched = _session_with(row)
        with patched:
            updated = await db_client.update_phone_number(
                7, 5, extra_metadata={"note": "new"}
            )

        self.assertIs(updated, row)
        self.assertEqual(
            row.extra_metadata,
            {"note": "new", PROVIDER_METADATA_KEY: {"phone_number_id": "1"}},
        )
        sql = _sql(session)
        self.assertIn("FOR UPDATE", sql)
        self.assertIn("telephony_phone_numbers.telephony_configuration_id = 5", sql)

    async def test_a_caller_that_sets_the_provider_entry_wins(self):
        row = _row(
            "+15551234567", extra={PROVIDER_METADATA_KEY: {"phone_number_id": "1"}}
        )
        session, patched = _session_with(row)
        with patched:
            await db_client.update_phone_number(
                1, 5, extra_metadata={PROVIDER_METADATA_KEY: {"phone_number_id": "2"}}
            )
        self.assertEqual(
            row.extra_metadata, {PROVIDER_METADATA_KEY: {"phone_number_id": "2"}}
        )
