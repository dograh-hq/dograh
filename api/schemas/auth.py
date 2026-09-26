import re

from pydantic import BaseModel, EmailStr, TypeAdapter, field_validator

from api.constants import AUTH_PROVIDER

_EMAIL_ADAPTER = TypeAdapter(EmailStr)
_LOCAL_TEST_EMAIL_RE = re.compile(r"^[^@\s]+@(?:[A-Za-z0-9-]+\.)*test$", re.IGNORECASE)


def _validate_auth_email(value: str) -> str:
    """Validate auth emails while allowing reserved .test addresses locally.

    ``email-validator`` intentionally rejects reserved domains such as
    ``example.test``.  Those domains are required for repeatable local smoke
    users, but must not change validation behaviour for hosted deployments.
    """
    if not isinstance(value, str):
        raise TypeError("Email must be a string")
    normalized = value.strip().lower()
    if AUTH_PROVIDER == "local" and _LOCAL_TEST_EMAIL_RE.fullmatch(normalized):
        local_part = normalized.rsplit("@", 1)[0]
        if local_part and ".." not in local_part:
            return normalized
    return str(_EMAIL_ADAPTER.validate_python(normalized))


class AuthEmailRequest(BaseModel):
    email: str

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _validate_auth_email(value)


class SignupRequest(AuthEmailRequest):
    password: str
    name: str | None = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(AuthEmailRequest):
    password: str


class UserResponse(BaseModel):
    id: int
    email: str | None
    name: str | None = None
    organization_id: int | None = None
    provider_id: str | None = None


class AuthResponse(BaseModel):
    token: str
    user: UserResponse
