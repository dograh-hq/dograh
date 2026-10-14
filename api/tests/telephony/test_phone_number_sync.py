"""Importing a provider account's numbers into a telephony configuration."""

from typing import Any, Dict, Iterable
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from api.db.telephony_phone_number_client import TelephonyPhoneNumberConflictError
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
        self.updated: list[tuple[int, Dict[str, Any]]] = []

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

    async def update_phone_number(
        self, phone_number_id: int, configuration_id: int, **kwargs: Any
    ):
        self.updated.append((phone_number_id, kwargs))
        return _row("updated", row_id=phone_number_id)


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
        self.assertEqual(first["extra_metadata"], {"phone_number_id": "1"})
        self.assertIsNone(second["label"])

    async def test_refreshes_metadata_on_rows_it_already_has(self):
        """Merged, not replaced: whatever an operator stored on the row survives."""
        kept = _row("+15551234567", label="Boston", extra={"note": "ops"}, row_id=7)
        db = _FakeDB(rows=[kept])

        outcome = await _sync(db, [_number("+15551234567", "1")])

        self.assertEqual(db.created, [])
        self.assertEqual(len(outcome.updated), 1)
        row_id, kwargs = db.updated[0]
        self.assertEqual(row_id, 7)
        self.assertEqual(
            kwargs["extra_metadata"], {"note": "ops", "phone_number_id": "1"}
        )
        # The operator's label is theirs.
        self.assertIsNone(kwargs["label"])

    async def test_fills_in_a_label_only_when_the_row_has_none(self):
        unlabeled = _row("+15551234567", extra={"phone_number_id": "1"}, row_id=7)
        db = _FakeDB(rows=[unlabeled])

        await _sync(db, [_number("+15551234567", "1", label="Example")])

        self.assertEqual(db.updated[0][1]["label"], "Example")

    async def test_leaves_an_up_to_date_row_alone(self):
        current = _row(
            "+15551234567", label="Boston", extra={"phone_number_id": "1"}, row_id=7
        )
        db = _FakeDB(rows=[current])

        outcome = await _sync(db, [_number("+15551234567", "1")])

        self.assertEqual(db.updated, [])
        self.assertEqual(outcome.updated, [])

    async def test_reports_numbers_the_provider_no_longer_lists_without_touching_them(
        self,
    ):
        gone = _row("+15559999999", row_id=3)
        db = _FakeDB(rows=[gone])

        outcome = await _sync(db, [_number("+15551234567", "1")])

        self.assertEqual(outcome.missing_at_provider, [gone])
        self.assertEqual(db.updated, [])
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
