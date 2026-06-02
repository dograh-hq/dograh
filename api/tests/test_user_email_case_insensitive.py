import pytest


@pytest.mark.asyncio
async def test_user_email_writes_lowercase_and_looks_up_case_insensitively(
    db_session,
):
    user = await db_session.create_user_with_email(
        email="User@Example.COM",
        password_hash="hashed-password",
    )

    assert user.email == "user@example.com"

    fetched = await db_session.get_user_by_email("USER@example.com")

    assert fetched is not None
    assert fetched.id == user.id
    assert fetched.email == "user@example.com"
