"""Superuser promotion driven by the ADMIN_EMAILS environment variable.

ADMIN_EMAILS is a comma-separated list of email addresses. Any local-auth
user who signs up or logs in with one of these emails is promoted to
superuser (UserModel.is_superuser) if they aren't one already.
"""

from loguru import logger

from api.constants import ADMIN_EMAILS
from api.db import db_client
from api.db.models import UserModel


def parse_admin_emails(raw: str | None) -> frozenset[str]:
    """Parse a comma-separated email list into a normalized set."""
    if not raw:
        return frozenset()
    return frozenset(
        email.strip().lower() for email in raw.split(",") if email.strip()
    )


def is_admin_email(email: str | None, admin_emails: frozenset[str] | None = None) -> bool:
    """Return True if the email is configured as an admin email."""
    if not email:
        return False
    if admin_emails is None:
        admin_emails = parse_admin_emails(ADMIN_EMAILS)
    return email.strip().lower() in admin_emails


async def promote_if_admin_email(
    user: UserModel, admin_emails: frozenset[str] | None = None
) -> UserModel:
    """Promote the user to superuser if their email is in ADMIN_EMAILS.

    No-op when the email isn't configured or the user is already a superuser.
    Mutates and returns the passed user model so callers see the new flag.
    """
    if user.is_superuser or not is_admin_email(user.email, admin_emails):
        return user

    await db_client.update_user_superuser(user.id, True)
    user.is_superuser = True
    logger.info(f"Promoted user {user.id} ({user.email}) to superuser via ADMIN_EMAILS")
    return user
