"""Trusted gateway authentication.

Lets Dograh run behind an authenticating reverse proxy (an OIDC gateway such as oauth2-proxy, an identity-aware
proxy, ...). The gateway authenticates the person and forwards a short-lived signed JWT (the *assertion*) in one
header; Dograh trusts only that signed assertion, on every request, and never an unsigned header.

Enable with ``AUTH_PROVIDER=local`` and ``TRUSTED_GATEWAY_AUTH=true``. Anything else leaves behaviour unchanged.

Trust model
-----------
The assertion is verified against a JWKS read from a file, with ``iss``, ``aud`` and the signature algorithm
pinned by configuration. Unsigned tokens, ``alg=none``, symmetric algorithms, a wrong ``iss``/``aud``, a missing
``exp``/``iat`` or a lifetime above 300 s are rejected. Because a fresh assertion is required on each request,
access ends within ``TRUSTED_GATEWAY_MAX_LIFETIME_SECONDS`` (at most 300 s) of the gatewayway no longer issuing
one, even though the stateless JWTs Dograh issues itself cannot be revoked. Users are linked by the assertion's
``sub`` only (never by email), into one configured organization, and never become superusers. Email/password
signup and login are disabled, and ``X-API-Key`` is limited to one optional service key.

Everything fails closed: enabled with a missing or unsafe setting, startup raises ``TrustedGatewayConfigError``;
a JWKS file that later becomes unreadable or invalid makes every request 401.

Environment
-----------
``TRUSTED_GATEWAY_AUTH``                    ``true`` to enable (default off)
``TRUSTED_GATEWAY_ISSUER``                  required, exact ``iss``
``TRUSTED_GATEWAY_AUDIENCE``                pinned ``aud`` (default ``dograh-api``)
``TRUSTED_GATEWAY_ALGS``                    comma list, asymmetric only (default ``RS256``)
``TRUSTED_GATEWAY_JWKS_FILE``               required, path of a JWKS JSON file (re-read when it changes)
``TRUSTED_GATEWAY_HEADER``                  header carrying the assertion (default ``X-Gateway-Assertion``)
``TRUSTED_GATEWAY_MAX_LIFETIME_SECONDS``    ``exp - iat`` ceiling, default and maximum 300
``TRUSTED_GATEWAY_LEEWAY_SECONDS``          clock skew, default 5, maximum 30
``TRUSTED_GATEWAY_ORG_PROVIDER_ID``         required, the single organization every user belongs to
``TRUSTED_GATEWAY_REQUIRED_PERMISSION``     optional; value that must appear in ``roles``/``permissions``/``scp``
``TRUSTED_GATEWAY_SERVICE_API_KEY_SHA256_FILE``  optional; file with the sha256 hex of the ONE API key accepted
                                            (service-to-service). Unset = no API key is accepted at all.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import HTTPException

GATEWAY_PROVIDER_PREFIX = "gateway:"
DEFAULT_HEADER = "X-Gateway-Assertion"
MAX_LIFETIME_CEILING = 300
MAX_LEEWAY = 30
_ASYMMETRIC_ALGS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512", "EdDSA"}
_FORBIDDEN_HEADERS = {"authorization", "x-api-key", "cookie", "host", "content-type", "content-length"}
_HEADER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_SUB_MAX = 200


class TrustedGatewayConfigError(RuntimeError):
    """The gateway option is enabled but its configuration is missing or unsafe: refuse to start."""


@dataclass(frozen=True)
class GatewayConfig:
    issuer: str
    audience: str
    algs: tuple[str, ...]
    jwks_file: str
    header_name: str
    max_lifetime: int
    leeway: int
    org_provider_id: str
    required_permission: str
    service_key_sha256_file: str


@dataclass(frozen=True)
class GatewayIdentity:
    sub: str
    claims: dict


def _now() -> float:  # indirection so tests can move the clock
    return time.time()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _readable(path: str, what: str) -> None:
    try:
        with open(path, "rb") as fh:
            fh.read(1)
    except OSError as exc:
        raise TrustedGatewayConfigError(f"{what} is not readable: {path}") from exc


def _int(env: dict, name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name)
    if raw in (None, ""):
        return default
    try:
        val = int(raw)
    except ValueError as exc:
        raise TrustedGatewayConfigError(f"{name} must be an integer") from exc
    if not lo <= val <= hi:
        raise TrustedGatewayConfigError(f"{name} must be between {lo} and {hi}")
    return val


def load_config(env: dict | None = None, *, auth_provider: str = "local") -> GatewayConfig | None:
    """Return the gateway configuration, ``None`` when disabled; raise when enabled and unsafe."""
    env = os.environ if env is None else env
    if not _truthy(env.get("TRUSTED_GATEWAY_AUTH")):
        return None
    if auth_provider != "local":
        raise TrustedGatewayConfigError("TRUSTED_GATEWAY_AUTH requires AUTH_PROVIDER=local")

    def need(name: str) -> str:
        val = (env.get(name) or "").strip()
        if not val:
            raise TrustedGatewayConfigError(f"{name} is required when TRUSTED_GATEWAY_AUTH=true")
        return val

    issuer = need("TRUSTED_GATEWAY_ISSUER")
    jwks_file = need("TRUSTED_GATEWAY_JWKS_FILE")
    org = need("TRUSTED_GATEWAY_ORG_PROVIDER_ID")
    audience = (env.get("TRUSTED_GATEWAY_AUDIENCE") or "dograh-api").strip()
    algs = tuple(a.strip() for a in (env.get("TRUSTED_GATEWAY_ALGS") or "RS256").split(",") if a.strip())
    if not algs or any(a not in _ASYMMETRIC_ALGS for a in algs):
        raise TrustedGatewayConfigError("TRUSTED_GATEWAY_ALGS must list asymmetric algorithms only")
    header = (env.get("TRUSTED_GATEWAY_HEADER") or DEFAULT_HEADER).strip()
    if not _HEADER_RE.match(header) or header.lower() in _FORBIDDEN_HEADERS:
        raise TrustedGatewayConfigError("TRUSTED_GATEWAY_HEADER is not a safe dedicated header name")
    max_life = _int(env, "TRUSTED_GATEWAY_MAX_LIFETIME_SECONDS", MAX_LIFETIME_CEILING, 1, MAX_LIFETIME_CEILING)
    leeway = _int(env, "TRUSTED_GATEWAY_LEEWAY_SECONDS", 5, 0, MAX_LEEWAY)
    service_key_file = (env.get("TRUSTED_GATEWAY_SERVICE_API_KEY_SHA256_FILE") or "").strip()
    _readable(jwks_file, "TRUSTED_GATEWAY_JWKS_FILE")
    if service_key_file:
        _readable(service_key_file, "TRUSTED_GATEWAY_SERVICE_API_KEY_SHA256_FILE")
    cfg = GatewayConfig(
        issuer=issuer,
        audience=audience,
        algs=algs,
        jwks_file=jwks_file,
        header_name=header,
        max_lifetime=max_life,
        leeway=leeway,
        org_provider_id=org,
        required_permission=(env.get("TRUSTED_GATEWAY_REQUIRED_PERMISSION") or "").strip(),
        service_key_sha256_file=service_key_file,
    )
    try:
        _load_keys(cfg)  # the file must parse and hold at least one usable key right now
    except GatewayAuthError as exc:
        raise TrustedGatewayConfigError(f"JWKS file unusable: {exc}") from exc
    return cfg


class GatewayAuthError(Exception):
    """Authentication failed. The message is for logs only; clients get a generic 401."""


def unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="Unauthorized")


# --------------------------------------------------------------------------- JWKS (file, mtime cache)
_keys_lock = threading.Lock()
_keys_cache: dict[str, tuple[int, dict]] = {}


def _load_keys(cfg: GatewayConfig) -> dict[str, Any]:
    try:
        st = os.stat(cfg.jwks_file)
    except OSError as exc:
        raise GatewayAuthError("jwks file unreadable") from exc
    stamp = st.st_mtime_ns ^ (st.st_size << 1)
    with _keys_lock:
        hit = _keys_cache.get(cfg.jwks_file)
        if hit and hit[0] == stamp:
            return hit[1]
    try:
        with open(cfg.jwks_file, "rb") as fh:
            data = json.loads(fh.read())
        raw_keys = data["keys"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        with _keys_lock:
            _keys_cache.pop(cfg.jwks_file, None)
        raise GatewayAuthError("jwks file invalid") from exc
    keys: dict[str, Any] = {}
    for idx, jwk in enumerate(raw_keys if isinstance(raw_keys, list) else []):
        try:
            pyjwk = jwt.PyJWK(jwk)
        except Exception:  # noqa: BLE001 - skip unusable keys, fail only if none is usable
            continue
        kty = jwk.get("kty")
        if kty not in {"RSA", "EC", "OKP"}:
            continue
        keys[str(jwk.get("kid") or f"#{idx}")] = pyjwk.key
    if not keys:
        with _keys_lock:
            _keys_cache.pop(cfg.jwks_file, None)
        raise GatewayAuthError("jwks holds no usable key")
    with _keys_lock:
        _keys_cache[cfg.jwks_file] = (stamp, keys)
    return keys


def _select_key(token: str, keys: dict[str, Any], cfg: GatewayConfig) -> Any:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise GatewayAuthError("malformed token") from exc
    if header.get("alg") not in cfg.algs:
        raise GatewayAuthError("algorithm not allowed")
    kid = header.get("kid")
    if kid is not None:
        if str(kid) not in keys:
            raise GatewayAuthError("unknown kid")
        return keys[str(kid)]
    if len(keys) == 1:  # token without kid and a single-key JWKS
        return next(iter(keys.values()))
    raise GatewayAuthError("kid required")


# --------------------------------------------------------------------------- verification
def _has_permission(claims: dict, wanted: str) -> bool:
    for name in ("roles", "permissions", "scp", "scope"):
        val = claims.get(name)
        if isinstance(val, str):
            val = val.split()
        if isinstance(val, list) and wanted in [str(v) for v in val]:
            return True
    return False


def verify_assertion(token: str | None, cfg: GatewayConfig, now: float | None = None) -> GatewayIdentity:
    """Verify the gatewayway assertion or raise ``GatewayAuthError``."""
    if not token or not isinstance(token, str) or len(token) > 8192:
        raise GatewayAuthError("assertion missing")
    key = _select_key(token, _load_keys(cfg), cfg)
    clock = _now() if now is None else now
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(cfg.algs),
            audience=cfg.audience,
            issuer=cfg.issuer,
            leeway=cfg.leeway,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
                "verify_exp": False,  # checked below against the (injectable) clock
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except jwt.PyJWTError as exc:
        raise GatewayAuthError(f"invalid assertion: {type(exc).__name__}") from exc
    aud_claim = claims.get("aud")  # PyJWT accepts a list that merely CONTAINS the audience: require exactly it
    if not (aud_claim == cfg.audience or (isinstance(aud_claim, list) and aud_claim == [cfg.audience])):
        raise GatewayAuthError("aud must be exactly the pinned audience")
    exp, iat = claims.get("exp"), claims.get("iat")
    if isinstance(exp, bool) or isinstance(iat, bool) or not isinstance(exp, (int, float)) or not isinstance(iat, (int, float)):
        raise GatewayAuthError("exp/iat must be numbers")
    if exp - iat > cfg.max_lifetime or exp <= iat:
        raise GatewayAuthError("assertion lifetime exceeds the maximum")
    if clock > exp + cfg.leeway:
        raise GatewayAuthError("assertion expired")
    if iat > clock + cfg.leeway:
        raise GatewayAuthError("assertion issued in the future")
    nbf = claims.get("nbf")
    if isinstance(nbf, (int, float)) and nbf > clock + cfg.leeway:
        raise GatewayAuthError("assertion not yet valid")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.strip() or len(sub) > _SUB_MAX:
        raise GatewayAuthError("invalid sub")
    if cfg.required_permission and not _has_permission(claims, cfg.required_permission):
        raise GatewayAuthError("required permission missing")
    return GatewayIdentity(sub=sub, claims=claims)


# --- service API key
def service_key_matches(raw_key: str | None, cfg: GatewayConfig) -> bool:
    """True only for the ONE API key whose sha256 hex is in the configured file (constant time)."""
    if not raw_key or not cfg.service_key_sha256_file:
        return False
    try:
        with open(cfg.service_key_sha256_file, "r", encoding="ascii") as fh:
            expected = fh.read().strip().lower()
    except (OSError, UnicodeDecodeError):
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        return False
    got = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    return hmac.compare_digest(got, expected)


def install_api_key_guard(db: Any, cfg: GatewayConfig) -> None:
    """Make ``db.validate_api_key`` accept only the service key (fail-closed for EVERY caller).

    ``db_client.validate_api_key`` is also called outside ``depends.py`` (e.g. ``routes/public_agent.py``); wrapping
    the single DB entry point closes all of them. Idempotent.
    """
    original = db.validate_api_key
    if getattr(original, "_trusted_gateway_guard", False):
        return

    async def guarded(raw_api_key: str, *args: Any, **kwargs: Any) -> Any:
        if not service_key_matches(raw_api_key, cfg):
            return None  # callers treat "no such key" as 401
        return await original(raw_api_key, *args, **kwargs)

    guarded._trusted_gateway_guard = True  # type: ignore[attr-defined]
    db.validate_api_key = guarded


# --------------------------------------------------------------------------- linking (DB)
async def authenticate(
    cfg: GatewayConfig,
    *,
    assertion: str | None,
    oss_user_id: int | None,
    db: Any,
    bootstrap: Any,
) -> Any:
    """Return the Dograh user for a verified assertion.

    ``oss_user_id`` is the user id carried by the UI's own (already validated) Dograh JWT, if any: it must
    belong to the same user the assertion links to. Link key is ``gateway:<sub>`` in ``users.provider_id``;
    never the email. The user always lands in the single configured organization and is never a superuser.
    """
    try:
        identity = verify_assertion(assertion, cfg)
    except GatewayAuthError:
        raise unauthorized() from None
    provider_id = GATEWAY_PROVIDER_PREFIX + identity.sub
    if oss_user_id is not None:
        # Cross-check BEFORE creating or linking anything: the UI's own JWT must already belong to this sub.
        existing = await db.get_user_by_id(oss_user_id)
        if existing is None or existing.provider_id != provider_id:
            raise unauthorized()
    user, _ = await db.get_or_create_user_by_provider_id(provider_id)
    organization, _ = await db.get_or_create_organization_by_provider_id(
        org_provider_id=cfg.org_provider_id, user_id=user.id
    )
    if user.selected_organization_id != organization.id:
        await db.add_user_to_organization(user.id, organization.id)
        await db.update_user_selected_organization(user.id, organization.id)
        user.selected_organization_id = organization.id
    await bootstrap(organization.id, created_by=user.provider_id)
    # Defence in depth: the gateway never grants superuser. Not persisted, only removed from this request.
    user.is_superuser = False
    return user
