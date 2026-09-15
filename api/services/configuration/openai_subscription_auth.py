"""Organization-bound Codex OAuth credentials and account session leases.

Credential parsing/refresh adapted from Hermes Talk's talk_auth.py at 8100046e.
See THIRD_PARTY_NOTICES.md for Hermes Talk and OpenClaw attribution.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import math
import os
import tempfile
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from redis.exceptions import RedisError

_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_REFRESH_MARGIN_SECONDS = 60
_REFRESH_TIMEOUT_SECONDS = 30
_REFRESH_LOCK_SECONDS = 45
_COMPARE_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_COMPARE_RENEW = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_MESSAGES = {
    "disabled": "Subscription voice is disabled on this server.",
    "self_hosted_only": "Subscription voice is available only on self-hosted Dograh deployments.",
    "configuration_required": "Configure the dedicated subscription login directory and organization binding on the server.",
    "organization_mismatch": "Subscription voice is not connected to this organization.",
    "login_required": "Sign in with Codex CLI using the dedicated Dograh credential directory.",
    "reauthentication_required": "The subscription login is invalid or expired. Sign in again in the dedicated Dograh credential directory.",
    "account_mismatch": "The subscription login does not match the connected account. Check the server account binding.",
    "busy": "The connected account already has an active subscription voice session.",
    "unavailable": "Subscription session coordination is unavailable. Try again after Redis is healthy.",
    "lease_lost": "The subscription session lease was lost. Start a new conversation.",
    "refresh_in_progress": "The subscription login is being refreshed. Try again shortly.",
    "refresh_failed": "Subscription sign-in could not be refreshed. Try again or sign in again with Codex CLI.",
    "rate_limited": "Subscription sign-in is rate limited. Try again later.",
    "persistence_failed": "The refreshed login could not be saved. Check write access to the dedicated credential directory and sign in again.",
    "credentials_changed": "The connected login changed during session setup. Start a new conversation.",
}


class SubscriptionAuthError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        self.safe_message = _MESSAGES[code]
        super().__init__(self.safe_message)


@dataclass(frozen=True)
class SubscriptionAuthSettings:
    enabled: bool = False
    deployment_mode: str = "oss"
    codex_home: Path | None = field(default=None, repr=False)
    organization_id: str | None = None
    expected_account_id: str | None = field(default=None, repr=False)
    lease_ttl_seconds: int = 90

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> SubscriptionAuthSettings:
        values = os.environ if env is None else env
        prefix = "DOGRAH_OPENAI_SUBSCRIPTION_"
        directory = values.get(prefix + "CODEX_HOME", "").strip()
        return cls(
            enabled=values.get(prefix + "ENABLED", "false").strip().lower() == "true",
            deployment_mode=values.get("DEPLOYMENT_MODE", "oss"),
            codex_home=Path(directory) if directory else None,
            organization_id=values.get(prefix + "ORGANIZATION_ID", "").strip() or None,
            expected_account_id=values.get(prefix + "ACCOUNT_ID", "").strip() or None,
        )


@dataclass(frozen=True)
class SubscriptionCredentials:
    access_token: str = field(repr=False)
    account_id: str = field(repr=False)
    expires_at: float
    refresh_token: str = field(repr=False)


@dataclass
class SubscriptionSession:
    credentials: SubscriptionCredentials = field(repr=False)
    _service: SubscriptionAuthService = field(repr=False)
    _key: str = field(repr=False)
    _owner: str = field(repr=False)
    _released: bool = field(default=False, repr=False)

    @property
    def lease_ttl_seconds(self) -> int:
        return self._service.settings.lease_ttl_seconds

    async def renew(self) -> None:
        if self._released:
            raise SubscriptionAuthError("lease_lost")
        renewed = await self._service._redis(
            "eval", _COMPARE_RENEW, 1, self._key, self._owner, self.lease_ttl_seconds
        )
        if not renewed:
            raise SubscriptionAuthError("lease_lost")

    async def release(self) -> None:
        if not self._released:
            await self._service._redis(
                "eval", _COMPARE_DELETE, 1, self._key, self._owner
            )
            self._released = True


def _token_text(value: Any) -> bool:
    return (
        isinstance(value, str) and bool(value) and not any(c.isspace() for c in value)
    )


def _jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError
        payload = json.loads(
            base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        )
        if not isinstance(payload, dict):
            raise TypeError
        return payload
    except (ValueError, TypeError, UnicodeError):
        raise SubscriptionAuthError("reauthentication_required") from None


def _account_claim(payload: dict) -> str | None:
    auth = payload.get("https://api.openai.com/auth")
    if isinstance(auth, dict):
        value = auth.get("chatgpt_account_id")
        if value is not None:
            if not _token_text(value):
                raise SubscriptionAuthError("reauthentication_required")
            return value
    return None


def _parse_credentials(
    data: dict, expected_account_id: str | None
) -> SubscriptionCredentials:
    mode = data.get("auth_mode")
    if mode is not None:
        if not isinstance(mode, str) or mode.strip().lower() not in {
            "chatgpt",
            "chatgptauthtokens",
        }:
            raise SubscriptionAuthError("reauthentication_required")
    elif isinstance(data.get("OPENAI_API_KEY"), str):
        raise SubscriptionAuthError("reauthentication_required")
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        raise SubscriptionAuthError("reauthentication_required")
    access, refresh = tokens.get("access_token"), tokens.get("refresh_token")
    if not _token_text(access) or not _token_text(refresh):
        raise SubscriptionAuthError("reauthentication_required")
    payload = _jwt_payload(access)
    expiry = payload.get("exp")
    if (
        isinstance(expiry, bool)
        or not isinstance(expiry, (int, float))
        or not math.isfinite(expiry)
        or expiry <= 0
    ):
        raise SubscriptionAuthError("reauthentication_required")
    account_id = tokens.get("account_id") or _account_claim(payload)
    if not _token_text(account_id):
        raise SubscriptionAuthError("reauthentication_required")
    claims = [_account_claim(payload)]
    id_token = tokens.get("id_token")
    if id_token is not None:
        if not _token_text(id_token):
            raise SubscriptionAuthError("reauthentication_required")
        claims.append(_account_claim(_jwt_payload(id_token)))
    if (expected_account_id and expected_account_id != account_id) or any(
        claim is not None and claim != account_id for claim in claims
    ):
        raise SubscriptionAuthError("account_mismatch")
    # Claims are consistency checks, not local signature/entitlement verification.
    return SubscriptionCredentials(access, account_id, float(expiry), refresh)


class SubscriptionAuthService:
    def __init__(
        self,
        settings: SubscriptionAuthSettings,
        redis_client: Any,
        http_client: httpx.AsyncClient | None = None,
        *,
        clock=time.time,
        owns_redis_client: bool = False,
    ):
        self.settings = settings
        self._redis_client = redis_client
        self._http_client = http_client
        self._clock = clock
        self._owns_redis_client = owns_redis_client

    def assert_organization(self, organization_id: int | str) -> None:
        if not self.settings.enabled:
            raise SubscriptionAuthError("disabled")
        if self.settings.deployment_mode != "oss":
            raise SubscriptionAuthError("self_hosted_only")
        if not self.settings.organization_id or self.settings.codex_home is None:
            raise SubscriptionAuthError("configuration_required")
        if str(organization_id) != str(self.settings.organization_id):
            raise SubscriptionAuthError("organization_mismatch")
        if (
            not self.settings.codex_home.is_absolute()
            or self.settings.lease_ttl_seconds < 60
        ):
            raise SubscriptionAuthError("configuration_required")

    def _auth_path(self) -> Path:
        directory = self.settings.codex_home
        assert directory is not None
        # No fallback to the server operator's desktop login or caller-supplied path.
        desktop_home = Path.home() / ".codex"
        ambient_home = os.environ.get("CODEX_HOME")
        forbidden = [desktop_home]
        if ambient_home:
            forbidden.append(Path(ambient_home))
        resolved_directory = directory.resolve()
        if (
            directory.is_symlink()
            or directory.is_junction()
            or any(
                resolved_directory.is_relative_to(path.resolve()) for path in forbidden
            )
        ):
            raise SubscriptionAuthError("configuration_required")
        path = directory / "auth.json"
        if path.is_symlink() or path.is_junction():
            raise SubscriptionAuthError("configuration_required")
        return path

    def _read_credentials(self) -> tuple[Path, dict, SubscriptionCredentials]:
        path = self._auth_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SubscriptionAuthError("login_required") from None
        except (OSError, ValueError, UnicodeError):
            raise SubscriptionAuthError("reauthentication_required") from None
        if not isinstance(data, dict):
            raise SubscriptionAuthError("reauthentication_required")
        return path, data, _parse_credentials(data, self.settings.expected_account_id)

    @staticmethod
    def _account_key(account_id: str, purpose: str) -> str:
        digest = hashlib.sha256(account_id.encode()).hexdigest()
        return f"dograh:openai-subscription:{digest}:{purpose}"

    async def _redis(self, method: str, *args, **kwargs):
        if self._redis_client is None:
            raise SubscriptionAuthError("unavailable")
        try:
            return await getattr(self._redis_client, method)(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except (RedisError, OSError):
            raise SubscriptionAuthError("unavailable") from None

    async def aclose(self) -> None:
        if self._owns_redis_client:
            self._owns_redis_client = False
            await self._redis_client.aclose()

    async def status(self, organization_id: int | str) -> dict[str, str]:
        if not self.settings.enabled:
            return {"status": "disabled", "message": _MESSAGES["disabled"]}
        try:
            self.assert_organization(organization_id)
            _, _, credential = self._read_credentials()
            if await self._redis(
                "exists", self._account_key(credential.account_id, "session")
            ):
                return {"status": "busy", "message": _MESSAGES["busy"]}
            if credential.expires_at <= self._clock() + _REFRESH_MARGIN_SECONDS:
                return {
                    "status": "refresh_required",
                    "message": "The login will be refreshed when a voice session starts.",
                }
            return {
                "status": "ready",
                "message": "Subscription login is present. Provider access is checked when a voice session starts.",
            }
        except SubscriptionAuthError as exc:
            if exc.code == "organization_mismatch":
                raise
            state = {
                "configuration_required": "login_required",
                "self_hosted_only": "disabled",
                "account_mismatch": "reauthentication_required",
            }.get(exc.code, exc.code)
            return {"status": state, "message": exc.safe_message}

    async def get_credentials(
        self,
        organization_id: int | str,
        *,
        expected_account_id: str | None = None,
    ) -> SubscriptionCredentials:
        """Resolve the same bound login for reasoning without a second voice lease."""
        self.assert_organization(organization_id)
        _, _, credential = self._read_credentials()
        if (
            expected_account_id is not None
            and credential.account_id != expected_account_id
        ):
            raise SubscriptionAuthError("account_mismatch")
        return await self._resolve_credentials(credential.account_id)

    async def acquire_session(self, organization_id: int | str) -> SubscriptionSession:
        self.assert_organization(organization_id)
        _, _, credential = self._read_credentials()
        key = self._account_key(credential.account_id, "session")
        owner = uuid.uuid4().hex
        if not await self._redis(
            "set", key, owner, nx=True, ex=self.settings.lease_ttl_seconds
        ):
            raise SubscriptionAuthError("busy")
        try:
            credential = await self._resolve_credentials(credential.account_id)
            session = SubscriptionSession(credential, self, key, owner)
            await session.renew()
            return session
        except BaseException:
            with contextlib.suppress(SubscriptionAuthError):
                await self._redis("eval", _COMPARE_DELETE, 1, key, owner)
            raise

    async def _resolve_credentials(self, account_id: str) -> SubscriptionCredentials:
        _, _, credential = self._read_credentials()
        if credential.account_id != account_id:
            raise SubscriptionAuthError("account_mismatch")
        if credential.expires_at > self._clock() + _REFRESH_MARGIN_SECONDS:
            return credential
        key = self._account_key(account_id, "refresh")
        owner = uuid.uuid4().hex
        for _ in range(50):
            if await self._redis("set", key, owner, nx=True, ex=_REFRESH_LOCK_SECONDS):
                break
            await asyncio.sleep(0.1)
        else:
            raise SubscriptionAuthError("refresh_in_progress")
        try:
            # A different API worker may have rotated the single-use refresh token.
            path, _, credential = self._read_credentials()
            if credential.account_id != account_id:
                raise SubscriptionAuthError("account_mismatch")
            if credential.expires_at > self._clock() + _REFRESH_MARGIN_SECONDS:
                return credential
            try:
                payload = await self._refresh_request(credential.refresh_token)
            except SubscriptionAuthError:
                # Codex CLI does not share the Redis mutex. Accept its fresh token only.
                _, _, updated = self._read_credentials()
                if (
                    updated.account_id == account_id
                    and updated.access_token != credential.access_token
                    and updated.expires_at > self._clock() + _REFRESH_MARGIN_SECONDS
                ):
                    return updated
                raise
            if not await self._redis(
                "eval", _COMPARE_RENEW, 1, key, owner, _REFRESH_LOCK_SECONDS
            ):
                raise SubscriptionAuthError("refresh_in_progress")
            return self._persist_refresh(path, credential, payload)
        finally:
            with contextlib.suppress(SubscriptionAuthError):
                await self._redis("eval", _COMPARE_DELETE, 1, key, owner)

    async def _refresh_request(self, refresh_token: str) -> dict:
        fields = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CODEX_CLIENT_ID,
        }
        try:
            async with asyncio.timeout(_REFRESH_TIMEOUT_SECONDS):
                if self._http_client is None:
                    async with httpx.AsyncClient(
                        follow_redirects=False, trust_env=False
                    ) as client:
                        response = await client.post(
                            _CODEX_TOKEN_URL,
                            data=fields,
                            timeout=_REFRESH_TIMEOUT_SECONDS,
                        )
                else:
                    response = await self._http_client.post(
                        _CODEX_TOKEN_URL,
                        data=fields,
                        timeout=_REFRESH_TIMEOUT_SECONDS,
                        follow_redirects=False,
                    )
                if response.status_code in {400, 401, 403}:
                    raise SubscriptionAuthError("reauthentication_required")
                if response.status_code == 429:
                    raise SubscriptionAuthError("rate_limited")
                if response.status_code != 200:
                    raise SubscriptionAuthError("refresh_failed")
                payload = response.json()
                if not isinstance(payload, dict):
                    raise SubscriptionAuthError("refresh_failed")
                return payload
        except SubscriptionAuthError:
            raise
        except (httpx.HTTPError, ValueError, TimeoutError):
            raise SubscriptionAuthError("refresh_failed") from None

    def _persist_refresh(
        self, path: Path, original: SubscriptionCredentials, payload: dict
    ) -> SubscriptionCredentials:
        expires_in = payload.get("expires_in")
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or not math.isfinite(expires_in)
            or expires_in <= _REFRESH_MARGIN_SECONDS
        ):
            raise SubscriptionAuthError("reauthentication_required")
        _, latest, current = self._read_credentials()
        if current.account_id != original.account_id:
            raise SubscriptionAuthError("account_mismatch")
        if (
            current.access_token != original.access_token
            or current.refresh_token != original.refresh_token
        ):
            if current.expires_at > self._clock() + _REFRESH_MARGIN_SECONDS:
                return current
            raise SubscriptionAuthError("credentials_changed")
        tokens = {
            **latest["tokens"],
            "access_token": payload.get("access_token"),
            "refresh_token": payload.get("refresh_token"),
        }
        if "id_token" in payload:
            tokens["id_token"] = payload["id_token"]
        if "account_id" in payload and payload["account_id"] != original.account_id:
            raise SubscriptionAuthError("account_mismatch")
        updated = {
            **latest,
            "tokens": tokens,
            "last_refresh": datetime.fromtimestamp(self._clock(), UTC).isoformat(),
        }
        refreshed = _parse_credentials(updated, original.account_id)
        if refreshed.expires_at <= self._clock() + _REFRESH_MARGIN_SECONDS:
            raise SubscriptionAuthError("reauthentication_required")
        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix="auth-", suffix=".json", dir=path.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(updated, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except OSError:
            raise SubscriptionAuthError("persistence_failed") from None
        finally:
            if tmp_name:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_name)
        return refreshed
