import asyncio
import base64
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from api.services.configuration.openai_subscription_auth import (
    SubscriptionAuthError,
    SubscriptionAuthService,
    SubscriptionAuthSettings,
)

_NOW = 2_000_000_000
_ACCOUNT = "synthetic-account"


def _jwt(expiry=_NOW + 3600, account=_ACCOUNT, **extra):
    payload = {"exp": expiry, **extra}
    if account is not None:
        payload["https://api.openai.com/auth"] = {"chatgpt_account_id": account}
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    )
    return f"header.{encoded}.signature"


def _login(expiry=_NOW + 3600, account=_ACCOUNT):
    return {
        "auth_mode": "chatgpt",
        "future_top_level": {"preserve": True},
        "tokens": {
            "access_token": _jwt(expiry, account),
            "refresh_token": "synthetic-refresh-secret",
            "id_token": _jwt(expiry, account),
            "account_id": account,
            "future_token_field": "keep-me",
        },
    }


class _Redis:
    def __init__(self):
        self.now = _NOW
        self.values = {}
        self.expiry = {}
        self.closed = 0
        self.unavailable = False

    def _purge(self):
        if self.unavailable:
            raise ConnectionError("must-never-escape-in-status")
        for key in list(self.values):
            if self.expiry[key] <= self.now:
                del self.values[key]
                del self.expiry[key]

    async def set(self, key, value, *, nx, ex):
        self._purge()
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expiry[key] = self.now + ex
        return True

    async def exists(self, key):
        self._purge()
        return int(key in self.values)

    async def eval(self, script, numkeys, key, owner, *args):
        self._purge()
        assert numkeys == 1
        if self.values.get(key) != owner:
            return 0
        if "EXPIRE" in script:
            self.expiry[key] = self.now + args[0]
        elif "DEL" in script:
            del self.values[key]
            del self.expiry[key]
        else:
            raise AssertionError("Unexpected Redis script")
        return 1

    async def aclose(self):
        self.closed += 1


class SubscriptionAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dograh-synthetic-auth-")
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.path = self.home / "auth.json"
        self.settings = SubscriptionAuthSettings(
            enabled=True, codex_home=self.home, organization_id="42"
        )
        self.redis = _Redis()
        self.requests = []
        self.handler = lambda request: httpx.Response(
            200,
            json={
                "access_token": _jwt(_NOW + 7200),
                "refresh_token": "synthetic-rotated-secret",
                "expires_in": 7200,
            },
        )

        async def handle(request):
            self.requests.append(request)
            result = self.handler(request)
            if asyncio.iscoroutine(result):
                return await result
            return result

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        self.addAsyncCleanup(self.http.aclose)
        self.service = SubscriptionAuthService(
            self.settings, self.redis, self.http, clock=lambda: self.redis.now
        )

    def write(self, data=None):
        self.path.write_text(json.dumps(data or _login()), encoding="utf-8")

    async def assert_error(self, code, operation):
        with self.assertRaises(SubscriptionAuthError) as caught:
            await operation
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("synthetic-refresh", str(caught.exception))
        self.assertNotIn(str(self.home), str(caught.exception))

    def test_settings_are_disabled_without_explicit_subscription_configuration(self):
        settings = SubscriptionAuthSettings.from_env(
            {"CODEX_HOME": str(self.home), "OPENAI_API_KEY": "synthetic-api-key"}
        )
        self.assertFalse(settings.enabled)
        self.assertIsNone(settings.codex_home)
        self.assertIsNone(settings.organization_id)
        explicit = SubscriptionAuthSettings.from_env(
            {
                "DOGRAH_OPENAI_SUBSCRIPTION_ENABLED": "true",
                "DOGRAH_OPENAI_SUBSCRIPTION_CODEX_HOME": str(self.home),
                "DOGRAH_OPENAI_SUBSCRIPTION_ORGANIZATION_ID": "42",
                "DOGRAH_OPENAI_SUBSCRIPTION_ACCOUNT_ID": _ACCOUNT,
            }
        )
        self.assertTrue(explicit.enabled)
        self.assertEqual(explicit.expected_account_id, _ACCOUNT)

    async def test_disabled_and_wrong_org_never_read_credentials(self):
        with patch.object(self.service, "_read_credentials") as read:
            await self.assert_error(
                "organization_mismatch", self.service.acquire_session(43)
            )
            await self.assert_error("organization_mismatch", self.service.status(43))
            read.assert_not_called()
        disabled = SubscriptionAuthService(SubscriptionAuthSettings(), self.redis)
        with patch.object(disabled, "_read_credentials") as read:
            self.assertEqual((await disabled.status(42))["status"], "disabled")
            await self.assert_error("disabled", disabled.acquire_session(42))
            read.assert_not_called()
        self.assertFalse(self.requests)
        self.assertFalse(self.redis.values)

    async def test_cloud_missing_binding_and_relative_directory_fail_before_read(self):
        for changed, expected in [
            ({"deployment_mode": "cloud"}, "self_hosted_only"),
            ({"organization_id": None}, "configuration_required"),
            ({"codex_home": None}, "configuration_required"),
            ({"codex_home": Path("relative")}, "configuration_required"),
        ]:
            with self.subTest(changed=changed):
                service = SubscriptionAuthService(
                    replace(self.settings, **changed), self.redis
                )
                with patch.object(service, "_read_credentials") as read:
                    await self.assert_error(expected, service.acquire_session(42))
                    read.assert_not_called()

    async def test_default_desktop_directory_is_never_read(self):
        service = SubscriptionAuthService(
            replace(self.settings, codex_home=Path.home() / ".codex"), self.redis
        )
        with patch.object(Path, "read_text") as read:
            await self.assert_error(
                "configuration_required", service.acquire_session(42)
            )
            read.assert_not_called()

    async def test_nested_desktop_directory_and_junction_are_rejected_before_read(self):
        for directory in [Path.home() / ".codex" / "nested", self.home]:
            service = SubscriptionAuthService(
                replace(self.settings, codex_home=directory), self.redis
            )
            with patch.object(Path, "read_text") as read:
                if directory == self.home:
                    with patch.object(Path, "is_junction", return_value=True):
                        await self.assert_error(
                            "configuration_required", service.acquire_session(42)
                        )
                else:
                    await self.assert_error(
                        "configuration_required", service.acquire_session(42)
                    )
                read.assert_not_called()

    async def test_status_is_read_only_and_redacted(self):
        self.write()
        before = self.path.read_bytes()
        state = await self.service.status(42)
        self.assertEqual(set(state), {"status", "message"})
        self.assertEqual(state["status"], "ready")
        self.assertNotIn(_ACCOUNT, json.dumps(state))
        self.assertNotIn(str(self.home), json.dumps(state))
        self.assertNotIn("synthetic-refresh", json.dumps(state))
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.requests)
        self.write(_login(_NOW - 1))
        before = self.path.read_bytes()
        self.assertEqual((await self.service.status(42))["status"], "refresh_required")
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.requests)

    async def test_missing_and_malformed_login_status(self):
        self.assertEqual((await self.service.status(42))["status"], "login_required")
        for content in [
            "not-json",
            "[]",
            '{"auth_mode":"apikey","OPENAI_API_KEY":"key"}',
        ]:
            with self.subTest(content=content):
                self.path.write_text(content)
                self.assertEqual(
                    (await self.service.status(42))["status"],
                    "reauthentication_required",
                )
        self.assertFalse(self.requests)

    async def test_invalid_expiry_and_missing_identity_fail_closed(self):
        for expiry in [None, True, "future", float("nan"), -1]:
            with self.subTest(expiry=expiry):
                self.write(_login(expiry))
                await self.assert_error(
                    "reauthentication_required", self.service.acquire_session(42)
                )
        for token in ["opaque-token", "header.!invalid.signature", _jwt(account=None)]:
            with self.subTest(token=token):
                data = _login()
                data["tokens"]["access_token"] = token
                data["tokens"].pop("account_id")
                self.write(data)
                await self.assert_error(
                    "reauthentication_required", self.service.acquire_session(42)
                )
        self.assertFalse(self.requests)
        self.assertFalse(self.redis.values)

    async def test_account_claim_and_configured_pin_must_match(self):
        self.write()
        service = SubscriptionAuthService(
            replace(self.settings, expected_account_id="other-account"),
            self.redis,
            self.http,
        )
        await self.assert_error("account_mismatch", service.acquire_session(42))
        for source in ["access_token", "id_token"]:
            data = _login()
            data["tokens"][source] = _jwt(account="other-account")
            self.write(data)
            await self.assert_error(
                "account_mismatch", self.service.acquire_session(42)
            )
        self.assertFalse(self.requests)
        self.assertFalse(self.redis.values)

    async def test_session_is_busy_until_owner_releases(self):
        self.write()
        session = await self.service.acquire_session(42)
        self.assertEqual(session.credentials.account_id, _ACCOUNT)
        self.assertNotIn(_ACCOUNT, repr(session))
        self.assertNotIn("synthetic", repr(session.credentials))
        self.assertNotIn(_ACCOUNT, " ".join(self.redis.values))
        self.assertEqual((await self.service.status(42))["status"], "busy")
        await self.assert_error("busy", self.service.acquire_session(42))
        await session.release()
        await session.release()
        self.assertFalse(self.redis.values)
        second = await self.service.acquire_session(42)
        await second.release()
        self.assertFalse(self.requests)

    async def test_process_loss_expires_lease_and_old_owner_cannot_release_new_lease(
        self,
    ):
        self.write()
        first = await self.service.acquire_session(42)
        self.redis.now += first.lease_ttl_seconds + 1
        second = await self.service.acquire_session(42)
        await self.assert_error("lease_lost", first.renew())
        await first.release()
        self.assertEqual((await self.service.status(42))["status"], "busy")
        await second.release()

    async def test_renew_extends_only_owned_lease(self):
        self.write()
        session = await self.service.acquire_session(42)
        self.redis.now += 70
        await session.renew()
        self.redis.now += 70
        await self.assert_error("busy", self.service.acquire_session(42))
        await session.release()
        await self.assert_error("lease_lost", session.renew())

    async def test_redis_failure_never_starts_provider_or_exposes_error(self):
        self.write(_login(_NOW - 1))
        self.redis.unavailable = True
        state = await self.service.status(42)
        self.assertEqual(state["status"], "unavailable")
        self.assertNotIn("must-never", state["message"])
        await self.assert_error("unavailable", self.service.acquire_session(42))
        self.assertFalse(self.requests)

    async def test_refresh_fixed_endpoint_rotates_atomically_preserving_unknown_fields(
        self,
    ):
        self.write(_login(_NOW - 1))
        session = await self.service.acquire_session(42)
        updated = json.loads(self.path.read_text())
        self.assertEqual(updated["future_top_level"], {"preserve": True})
        self.assertEqual(updated["tokens"]["future_token_field"], "keep-me")
        self.assertEqual(updated["tokens"]["refresh_token"], "synthetic-rotated-secret")
        self.assertEqual(
            updated["tokens"]["access_token"], session.credentials.access_token
        )
        self.assertIn("last_refresh", updated)
        self.assertEqual(len(list(self.home.iterdir())), 1)
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(str(request.url), "https://auth.openai.com/oauth/token")
        self.assertEqual(
            request.headers["content-type"], "application/x-www-form-urlencoded"
        )
        self.assertNotIn("authorization", request.headers)
        self.assertIn(b"grant_type=refresh_token", request.content)
        self.assertIn(b"synthetic-refresh-secret", request.content)
        await session.release()

    async def test_refresh_failures_leave_original_credentials_and_release_lease(self):
        for status, error in [
            (400, "reauthentication_required"),
            (401, "reauthentication_required"),
            (403, "reauthentication_required"),
            (429, "rate_limited"),
            (500, "refresh_failed"),
            (302, "refresh_failed"),
        ]:
            with self.subTest(status=status):
                self.write(_login(_NOW - 1))
                before = self.path.read_bytes()
                self.handler = lambda request, code=status: httpx.Response(
                    code,
                    text="secret-must-not-escape",
                    headers={"location": "https://attacker.invalid"},
                )
                await self.assert_error(error, self.service.acquire_session(42))
                self.assertEqual(before, self.path.read_bytes())
                self.assertFalse(self.redis.values)
        self.assertEqual(len(self.requests), 6)

    async def test_refresh_timeout_is_redacted(self):
        self.write(_login(_NOW - 1))

        def timeout(request):
            raise httpx.ReadTimeout("synthetic-refresh-secret")

        self.handler = timeout
        await self.assert_error("refresh_failed", self.service.acquire_session(42))
        self.assertFalse(self.redis.values)

    async def test_refresh_total_deadline_bounds_client_that_ignores_phase_timeout(
        self,
    ):
        self.write(_login(_NOW - 1))
        before = self.path.read_bytes()
        completed = asyncio.Event()

        async def slow_response(request):
            await asyncio.sleep(1)
            completed.set()
            return httpx.Response(
                200,
                json={
                    "access_token": _jwt(),
                    "refresh_token": "rotated",
                    "expires_in": 3600,
                },
            )

        self.handler = slow_response
        with patch(
            "api.services.configuration.openai_subscription_auth._REFRESH_TIMEOUT_SECONDS",
            0.01,
        ):
            await self.assert_error("refresh_failed", self.service.acquire_session(42))
        self.assertFalse(completed.is_set())
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.redis.values)

    async def test_invalid_refresh_payload_cannot_replace_credentials(self):
        for payload in [
            {},
            {"access_token": _jwt(), "refresh_token": "new", "expires_in": -1},
            {"access_token": _jwt(), "refresh_token": "new", "expires_in": True},
            {
                "access_token": _jwt(account="other"),
                "refresh_token": "new",
                "expires_in": 3600,
            },
            {
                "access_token": _jwt(),
                "refresh_token": "new",
                "expires_in": 3600,
                "account_id": "other",
            },
        ]:
            with self.subTest(payload=payload):
                self.write(_login(_NOW - 1))
                before = self.path.read_bytes()
                self.handler = lambda request, value=payload: httpx.Response(
                    200, json=value
                )
                with self.assertRaises(SubscriptionAuthError):
                    await self.service.acquire_session(42)
                self.assertEqual(before, self.path.read_bytes())
                self.assertFalse(self.redis.values)

    async def test_persistence_failure_is_not_silently_accepted(self):
        self.write(_login(_NOW - 1))
        before = self.path.read_bytes()
        with patch(
            "api.services.configuration.openai_subscription_auth.os.replace",
            side_effect=PermissionError("sensitive-path"),
        ):
            await self.assert_error(
                "persistence_failed", self.service.acquire_session(42)
            )
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.home.iterdir()), [self.path])
        self.assertFalse(self.redis.values)

    async def test_concurrent_workers_reread_after_refresh_lock(self):
        self.write(_login(_NOW - 1))
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def delayed(request):
            started.set()
            await proceed.wait()
            return httpx.Response(
                200,
                json={
                    "access_token": _jwt(_NOW + 7200),
                    "refresh_token": "rotated",
                    "expires_in": 7200,
                },
            )

        self.handler = delayed
        other = SubscriptionAuthService(
            self.settings, self.redis, self.http, clock=lambda: self.redis.now
        )
        first = asyncio.create_task(self.service._resolve_credentials(_ACCOUNT))
        await started.wait()
        second = asyncio.create_task(other._resolve_credentials(_ACCOUNT))
        await asyncio.sleep(0)
        proceed.set()
        results = await asyncio.gather(first, second)
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.redis.values)

    async def test_codex_cli_refresh_race_accepts_new_valid_file_without_overwrite(
        self,
    ):
        for status in [200, 400]:
            with self.subTest(status=status):
                self.write(_login(_NOW - 1))

                def cli_race(request, status=status):
                    updated = _login(_NOW + 5400)
                    updated["tokens"]["refresh_token"] = "cli-rotation"
                    updated["future_top_level"] = "cli-preserved"
                    self.write(updated)
                    return httpx.Response(
                        status,
                        json={
                            "access_token": _jwt(_NOW + 7200),
                            "refresh_token": "our-rotation",
                            "expires_in": 7200,
                        },
                    )

                self.handler = cli_race
                session = await self.service.acquire_session(42)
                self.assertEqual(session.credentials.refresh_token, "cli-rotation")
                self.assertEqual(
                    json.loads(self.path.read_text())["future_top_level"],
                    "cli-preserved",
                )
                await session.release()

    async def test_lost_refresh_lock_does_not_overwrite_or_release_new_owner(self):
        self.write(_login(_NOW - 1))
        before = self.path.read_bytes()
        key = self.service._account_key(_ACCOUNT, "refresh")

        def lost_lock(request):
            self.redis.values[key] = "new-owner"
            return httpx.Response(
                200,
                json={
                    "access_token": _jwt(),
                    "refresh_token": "new",
                    "expires_in": 3600,
                },
            )

        self.handler = lost_lock
        await self.assert_error("refresh_in_progress", self.service.acquire_session(42))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.redis.values, {key: "new-owner"})

    async def test_cancellation_releases_session_and_refresh_locks(self):
        self.write(_login(_NOW - 1))
        started = asyncio.Event()

        async def pending(request):
            started.set()
            await asyncio.Event().wait()

        self.handler = pending
        task = asyncio.create_task(self.service.acquire_session(42))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.redis.values)

    async def test_cleanup_remains_idempotent_after_close_error(self):
        owned = SubscriptionAuthService(
            self.settings, self.redis, owns_redis_client=True
        )
        with patch.object(
            self.redis, "aclose", side_effect=ConnectionError("closed")
        ) as close:
            with self.assertRaises(ConnectionError):
                await owned.aclose()
            await owned.aclose()
            close.assert_awaited_once()

    async def test_cleanup_closes_only_owned_client_once(self):
        await self.service.aclose()
        self.assertEqual(self.redis.closed, 0)
        owned = SubscriptionAuthService(
            self.settings, self.redis, owns_redis_client=True
        )
        await owned.aclose()
        await owned.aclose()
        self.assertEqual(self.redis.closed, 1)


if __name__ == "__main__":
    unittest.main()
