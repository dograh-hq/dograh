from pydantic import BaseModel, EmailStr, field_validator


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None
    # Cloudflare Turnstile token (cf-turnstile-response). Required only when
    # TURNSTILE_SECRET_KEY is configured on the server.
    turnstile_token: str | None = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    turnstile_token: str | None = None


class UserResponse(BaseModel):
    id: int
    email: str | None
    name: str | None = None
    organization_id: int | None = None
    provider_id: str | None = None
    is_superuser: bool = False


class AuthResponse(BaseModel):
    token: str
    user: UserResponse
