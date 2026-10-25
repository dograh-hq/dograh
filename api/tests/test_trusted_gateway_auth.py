"""Trusted gateway authentication (AUTH_PROVIDER=local + TRUSTED_GATEWAY_AUTH=true).

Real RSA keys are generated at test time; nothing here touches a database.
"""

import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.routes import auth as auth_routes
from api.services.auth import depends as auth_depends
from api.services.auth import trusted_gateway as tg

ISS = "https://gateway.example.test"
AUD = "dograh-api"
ORG = "org_single"
SERVICE_KEY = "svc_" + "b" * 40
OSS_SECRET = "unit-test-secret-0123456789abcdef0123"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    d.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return d


class FakeDB:
    def __init__(self):
        self.users = {}
        self.orgs = {}
        self.org_calls = []
        self.api_key_calls = []

    async def get_or_create_user_by_provider_id(self, provider_id):
        if provider_id in self.users:
            return self.users[provider_id], False
        user = SimpleNamespace(
            id=len(self.users) + 1,
            provider_id=provider_id,
            email=None,
            is_superuser=False,
            selected_organization_id=None,
        )
        self.users[provider_id] = user
        return user, True

    async def get_or_create_organization_by_provider_id(self, org_provider_id, user_id):
        self.org_calls.append(org_provider_id)
        org = self.orgs.setdefault(
            org_provider_id,
            SimpleNamespace(id=900 + len(self.orgs), provider_id=org_provider_id),
        )
        return org, False

    async def add_user_to_organization(self, user_id, organization_id):
        return None

    async def update_user_selected_organization(self, user_id, organization_id):
        return None

    async def validate_api_key(self, raw):
        self.api_key_calls.append(raw)
        return SimpleNamespace(created_by=1, organization_id=900, key_prefix=raw[:8])

    async def get_user_by_id(self, user_id):
        return next((u for u in self.users.values() if u.id == int(user_id)), None)


@pytest.fixture
def gw(monkeypatch, tmp_path):
    jwks = tmp_path / "jwks.json"
    jwks.write_text(json.dumps({"keys": [_jwk(KEY, "k1")]}))
    keyfile = tmp_path / "service-key.sha256"
    keyfile.write_text(hashlib.sha256(SERVICE_KEY.encode()).hexdigest())
    env = {
        "TRUSTED_GATEWAY_AUTH": "true",
        "TRUSTED_GATEWAY_ISSUER": ISS,
        "TRUSTED_GATEWAY_JWKS_FILE": str(jwks),
        "TRUSTED_GATEWAY_ORG_PROVIDER_ID": ORG,
        "TRUSTED_GATEWAY_SERVICE_API_KEY_SHA256_FILE": str(keyfile),
    }
    cfg = tg.load_config(env, auth_provider="local")
    db = FakeDB()
    boot = AsyncMock(return_value=False)
    monkeypatch.setattr(auth_depends, "_GATEWAY", cfg)
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "local")
    monkeypatch.setattr(auth_depends, "db_client", db)
    monkeypatch.setattr(auth_depends, "ensure_organization_bootstrapped", boot)
    monkeypatch.setattr(auth_routes, "_GATEWAY", cfg)
    return SimpleNamespace(cfg=cfg, db=db, env=env, jwks=jwks, boot=boot)


def mint(key=KEY, kid="k1", **claims):
    now = int(time.time())
    payload = {"iss": ISS, "aud": AUD, "sub": "sub-1", "iat": now, "exp": now + 120}
    payload.update({k: v for k, v in claims.items() if v is not None})
    for k in [k for k, v in claims.items() if v is None]:
        payload.pop(k, None)
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


async def get_user(assertion=None, authorization=None, api_key=None):
    return await auth_depends.get_user(
        authorization=authorization,
        x_api_key=api_key,
        x_gateway_assertion=assertion,
    )


async def expect_401(coro):
    with pytest.raises(HTTPException) as exc:
        await coro
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_assertion_links_user_by_sub_in_single_org(gw):
    user = await get_user(mint(sub="abc", email="x@example.com", org="other"))
    assert user.provider_id == "gateway:abc"
    assert user.is_superuser is False
    assert user.email is None
    assert set(gw.db.org_calls) == {ORG}
    gw.boot.assert_awaited()


@pytest.mark.asyncio
async def test_rejects_unsigned_expired_wrong_signer_audience_issuer(gw):
    now = int(time.time())
    await expect_401(get_user(None))
    await expect_401(get_user("garbage"))
    unsigned = jwt.encode(
        {"iss": ISS, "aud": AUD, "sub": "x", "iat": now, "exp": now + 60},
        None,
        algorithm="none",
    )
    await expect_401(get_user(unsigned))
    await expect_401(get_user(mint(key=OTHER_KEY)))
    await expect_401(get_user(mint(kid="unknown")))
    await expect_401(get_user(mint(iat=now - 1000, exp=now - 800)))
    await expect_401(get_user(mint(aud="someone-else")))
    await expect_401(get_user(mint(iss="https://other.example.test")))
    await expect_401(get_user(mint(exp=None)))
    await expect_401(get_user(mint(iat=None)))
    await expect_401(get_user(mint(sub=None)))


@pytest.mark.asyncio
async def test_assertion_lifetime_is_capped_at_five_minutes(gw, monkeypatch):
    now = int(time.time())
    await expect_401(get_user(mint(iat=now, exp=now + 301)))
    tok = mint(iat=now, exp=now + 300)
    assert (await get_user(tok)).provider_id == "gateway:sub-1"
    monkeypatch.setattr(tg, "_now", lambda: now + 306)
    await expect_401(get_user(tok))


@pytest.mark.asyncio
async def test_dograh_jwt_alone_never_authenticates(gw):
    user, _ = await gw.db.get_or_create_user_by_provider_id("gateway:sub-1")
    oss = jwt.encode(
        {"sub": str(user.id), "exp": int(time.time()) + 600},
        OSS_SECRET,
        algorithm="HS256",
    )
    monkeypatched = pytest.MonkeyPatch()
    monkeypatched.setattr(auth_depends, "decode_jwt_token", lambda t: jwt.decode(t, OSS_SECRET, algorithms=["HS256"]))
    try:
        await expect_401(get_user(None, authorization=f"Bearer {oss}"))
        # with a valid assertion for the same user it is accepted; for another user it is not
        assert (await get_user(mint(), authorization=f"Bearer {oss}")).id == user.id
        await expect_401(get_user(mint(sub="someone-else"), authorization=f"Bearer {oss}"))
    finally:
        monkeypatched.undo()


@pytest.mark.asyncio
async def test_api_key_only_accepts_the_configured_service_key(gw):
    await expect_401(get_user(None, api_key="dgr_user_key"))
    await expect_401(get_user(mint(), api_key="dgr_user_key"))
    await expect_401(auth_depends._handle_api_key_auth("dgr_user_key"))
    assert gw.db.api_key_calls == []
    await gw.db.get_or_create_user_by_provider_id("gateway:owner")
    assert (await get_user(None, api_key=SERVICE_KEY)).selected_organization_id == 900


@pytest.mark.asyncio
async def test_websocket_requires_assertion(gw):
    class WS:
        def __init__(self, headers=None):
            from starlette.datastructures import Headers

            self.headers = Headers(headers or {})
            self.closed = None

        async def close(self, code=1000, reason=None):
            self.closed = code

    ws = WS()
    with pytest.raises(HTTPException):
        await auth_depends.get_user_ws(ws, token="anything", api_key=None)
    assert ws.closed == 1008
    ws = WS({tg.DEFAULT_HEADER: mint(sub="ws-user")})
    user = await auth_depends.get_user_ws(ws, token=None, api_key=None)
    assert user.provider_id == "gateway:ws-user" and ws.closed is None


@pytest.mark.asyncio
async def test_superuser_flag_is_never_granted(gw):
    user, _ = await gw.db.get_or_create_user_by_provider_id("gateway:su")
    user.is_superuser = True
    with pytest.raises(HTTPException) as exc:
        await auth_depends.get_superuser(
            authorization=None,
            x_api_key=None,
            x_gateway_assertion=mint(sub="su"),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_required_permission_is_enforced_when_configured(gw):
    cfg = tg.load_config(
        {**gw.env, "TRUSTED_GATEWAY_REQUIRED_PERMISSION": "voice"}, auth_provider="local"
    )
    auth_depends._GATEWAY = cfg
    await expect_401(get_user(mint()))
    assert (await get_user(mint(roles=["voice"]))).id


@pytest.mark.asyncio
async def test_signup_and_login_are_404_and_session_route_works(gw, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await auth_depends.require_local_auth()
    assert exc.value.status_code == 404
    monkeypatch.setattr(auth_routes, "create_jwt_token", lambda uid, email: f"ui-token-{uid}")
    app = FastAPI()
    app.include_router(auth_routes.router)
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": "a@example.com", "password": "x"}).status_code == 404
    assert client.get("/auth/gateway-session").status_code == 401
    ok = client.get("/auth/gateway-session", headers={tg.DEFAULT_HEADER: mint(sub="ui")})
    assert ok.status_code == 200 and ok.json()["user"]["provider_id"] == "gateway:ui"


@pytest.mark.parametrize(
    "override",
    [
        {"TRUSTED_GATEWAY_JWKS_FILE": "/nonexistent/jwks.json"},
        {"TRUSTED_GATEWAY_ISSUER": ""},
        {"TRUSTED_GATEWAY_ORG_PROVIDER_ID": ""},
        {"TRUSTED_GATEWAY_ALGS": "HS256"},
        {"TRUSTED_GATEWAY_ALGS": "RS256,none"},
        {"TRUSTED_GATEWAY_MAX_LIFETIME_SECONDS": "301"},
        {"TRUSTED_GATEWAY_HEADER": "Authorization"},
    ],
)
def test_unsafe_configuration_refuses_to_start(gw, override):
    with pytest.raises(tg.TrustedGatewayConfigError):
        tg.load_config({**gw.env, **override}, auth_provider="local")


def test_requires_local_provider_and_is_off_by_default(gw):
    with pytest.raises(tg.TrustedGatewayConfigError):
        tg.load_config(gw.env, auth_provider="stack")
    assert tg.load_config({}, auth_provider="local") is None
    assert tg.load_config({"TRUSTED_GATEWAY_AUTH": "false"}, auth_provider="local") is None


@pytest.mark.asyncio
async def test_unreadable_jwks_after_start_fails_closed(gw):
    assert (await get_user(mint())).id
    gw.jwks.write_text("{broken")
    await expect_401(get_user(mint()))


@pytest.mark.asyncio
async def test_disabled_keeps_existing_behaviour(monkeypatch):
    monkeypatch.setattr(auth_depends, "_GATEWAY", None)
    monkeypatch.setattr(auth_depends, "AUTH_PROVIDER", "local")
    db = FakeDB()
    await db.get_or_create_user_by_provider_id("owner")
    monkeypatch.setattr(auth_depends, "db_client", db)
    await auth_depends.get_user(authorization=None, x_api_key="dgr_user_key")
    assert db.api_key_calls == ["dgr_user_key"]
    assert await auth_depends.require_local_auth() is None
    await expect_401(get_user(mint()))


@pytest.mark.asyncio
async def test_aud_list_must_be_exactly_the_pinned_audience(gw):
    await expect_401(get_user(mint(aud=["dograh-api", "other-api"])))
    await expect_401(get_user(mint(aud=["other-api", "dograh-api"])))
    await expect_401(get_user(mint(aud=[])))
    assert (await get_user(mint(aud=["dograh-api"], sub="s-list"))).provider_id == "gateway:s-list"


@pytest.mark.asyncio
async def test_mismatched_ui_jwt_creates_and_links_nothing(gw, monkeypatch):
    other, _ = await gw.db.get_or_create_user_by_provider_id("gateway:someone-else")
    monkeypatch.setattr(
        auth_depends, "decode_jwt_token", lambda t: jwt.decode(t, OSS_SECRET, algorithms=["HS256"])
    )
    oss = jwt.encode({"sub": str(other.id), "exp": int(time.time()) + 600}, OSS_SECRET, algorithm="HS256")
    before = set(gw.db.users)
    await expect_401(get_user(mint(sub="brand-new"), authorization=f"Bearer {oss}"))
    assert set(gw.db.users) == before and gw.db.org_calls == []
    gw.boot.assert_not_awaited()


@pytest.mark.asyncio
async def test_validate_api_key_is_guarded_for_every_caller(gw):
    """routes/public_agent.py calls db_client.validate_api_key directly."""
    tg.install_api_key_guard(gw.db, gw.cfg)
    assert await gw.db.validate_api_key("dgr_org_key") is None
    assert gw.db.api_key_calls == []
    assert await gw.db.validate_api_key(SERVICE_KEY) is not None


@pytest.mark.asyncio
async def test_service_key_never_returns_superuser(gw):
    owner, _ = await gw.db.get_or_create_user_by_provider_id("gateway:owner")
    owner.is_superuser = True
    assert (await get_user(None, api_key=SERVICE_KEY)).is_superuser is False


def test_gateway_session_requires_assertion_and_refuses_api_key(gw, monkeypatch):
    gw.db.users["gateway:owner"] = SimpleNamespace(
        id=1, provider_id="gateway:owner", email=None, is_superuser=False, selected_organization_id=900
    )
    monkeypatch.setattr(auth_routes, "create_jwt_token", lambda uid, email: "ui-token")
    app = FastAPI()
    app.include_router(auth_routes.router)
    client = TestClient(app)
    assert client.get("/auth/gateway-session", headers={"X-API-Key": SERVICE_KEY}).status_code == 401
    both = {"X-API-Key": SERVICE_KEY, tg.DEFAULT_HEADER: mint(sub="x")}
    assert client.get("/auth/gateway-session", headers=both).status_code == 401
