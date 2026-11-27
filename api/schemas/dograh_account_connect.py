from typing import Literal

from pydantic import BaseModel, Field


class AccountConnectStartRequest(BaseModel):
    # Where people reach this install; shown on the approval page.
    install_url: str | None = Field(default=None, max_length=200)


class AccountConnectStart(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


class AccountConnectPollRequest(BaseModel):
    device_code: str = Field(min_length=1, max_length=128)


class AccountConnectPoll(BaseModel):
    status: Literal["pending", "connected", "denied", "expired", "collected"]
    team_name: str | None = None
    key_prefix: str | None = None
    made_default: bool = False


class AccountConnectRequest(BaseModel):
    user_code: str
    install_name: str
    install_url: str | None = None
    # MPS answers 404 for an expired code, so it never reports one here.
    status: Literal["pending", "approved", "denied", "issued"]
    expires_in: int


class AccountConnectDecision(BaseModel):
    status: Literal["approved", "denied"]


class DograhAccountStatus(BaseModel):
    # connected: a Dograh key MPS accepts; missing: no Dograh key at all;
    # invalid: MPS rejects the key (expired, archived or revoked).
    status: Literal["connected", "missing", "invalid"]
