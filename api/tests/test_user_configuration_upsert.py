import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from api.db.models import UserConfigurationModel
from api.db.user_client import UserClient
from api.enums import UserConfigurationKey


class _FakeResult:
    def __init__(self, value: dict):
        self._value = value

    def scalar_one(self) -> dict:
        return self._value


class _FakeSession:
    def __init__(self, result_value: dict):
        self.result_value = result_value
        self.statements = []
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self.result_value)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_upsert_user_configuration_value_uses_atomic_conflict_update():
    result_value = {"completed_actions": ["web_call_started"]}
    fake_session = _FakeSession(result_value)
    client = UserClient.__new__(UserClient)
    client.async_session = lambda: fake_session

    value = await client.upsert_user_configuration_value(
        86,
        UserConfigurationKey.ONBOARDING.value,
        result_value,
    )

    assert value == result_value
    assert fake_session.committed is True
    assert fake_session.rolled_back is False
    assert len(fake_session.statements) == 1

    compiled = str(fake_session.statements[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT ON CONSTRAINT _user_configuration_key_uc DO UPDATE" in compiled
    assert "configuration = excluded.configuration" in compiled
    assert "last_validated_at" not in compiled


@pytest.mark.asyncio
async def test_upsert_user_configuration_value_updates_existing_row(
    db_session,
    async_session,
):
    user, _ = await db_session.get_or_create_user_by_provider_id(
        "user-config-upsert-test"
    )

    first = await db_session.upsert_user_configuration_value(
        user.id,
        UserConfigurationKey.ONBOARDING.value,
        {"skipped": False},
    )
    second = await db_session.upsert_user_configuration_value(
        user.id,
        UserConfigurationKey.ONBOARDING.value,
        {"skipped": True},
    )

    assert first == {"skipped": False}
    assert second == {"skipped": True}

    result = await async_session.execute(
        select(UserConfigurationModel).where(
            UserConfigurationModel.user_id == user.id,
            UserConfigurationModel.key == UserConfigurationKey.ONBOARDING.value,
        )
    )
    rows = result.scalars().all()

    assert len(rows) == 1
    assert rows[0].configuration == {"skipped": True}
