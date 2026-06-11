"""Tests for ADMIN_EMAILS-driven superuser promotion (local/OSS auth)."""

import pytest

from api.services.auth.admin_emails import (
    is_admin_email,
    parse_admin_emails,
    promote_if_admin_email,
)


class TestParseAdminEmails:
    def test_empty_and_none(self):
        assert parse_admin_emails(None) == frozenset()
        assert parse_admin_emails("") == frozenset()
        assert parse_admin_emails(" , ,") == frozenset()

    def test_comma_separated_with_whitespace_and_case(self):
        assert parse_admin_emails(" Owner@Example.com , ops@example.com") == frozenset(
            {"owner@example.com", "ops@example.com"}
        )


class TestIsAdminEmail:
    def test_match_is_case_insensitive(self):
        admins = frozenset({"owner@example.com"})
        assert is_admin_email("OWNER@example.COM", admins) is True
        assert is_admin_email("  owner@example.com ", admins) is True

    def test_non_admin_and_missing_email(self):
        admins = frozenset({"owner@example.com"})
        assert is_admin_email("client@example.com", admins) is False
        assert is_admin_email(None, admins) is False
        assert is_admin_email("", admins) is False


class TestPromoteIfAdminEmail:
    @pytest.mark.asyncio
    async def test_promotes_admin_email_user(self, db_session):
        user = await db_session.create_user_with_email(
            email="owner@example.com", password_hash="x"
        )
        assert not user.is_superuser

        user = await promote_if_admin_email(
            user, admin_emails=frozenset({"owner@example.com"})
        )

        assert user.is_superuser is True
        refetched = await db_session.get_user_by_id(user.id)
        assert refetched.is_superuser is True

    @pytest.mark.asyncio
    async def test_does_not_promote_non_admin_email(self, db_session):
        user = await db_session.create_user_with_email(
            email="client@example.com", password_hash="x"
        )

        user = await promote_if_admin_email(
            user, admin_emails=frozenset({"owner@example.com"})
        )

        assert not user.is_superuser
        refetched = await db_session.get_user_by_id(user.id)
        assert not refetched.is_superuser

    @pytest.mark.asyncio
    async def test_already_superuser_is_noop(self, db_session):
        user = await db_session.create_user_with_email(
            email="owner@example.com", password_hash="x"
        )
        await db_session.update_user_superuser(user.id, True)
        user.is_superuser = True

        user = await promote_if_admin_email(
            user, admin_emails=frozenset({"owner@example.com"})
        )

        assert user.is_superuser is True
