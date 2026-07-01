"""aiohttp client for the VoiceLink reseller KYC API.

Mirrors the ``_login`` / ``_api_request`` seams of
``api.services.telephony.providers.voicelink.provider.VoiceLinkProvider``:
bearer-token auth obtained via ``POST /v1/auth/login`` with a single
re-login retry on 401. Credentials come from the environment
(``VOICELINK_API_BASE``, ``VOICELINK_RESELLER_USERNAME``,
``VOICELINK_RESELLER_PASSWORD``) — the password is never logged.

VoiceLink responses wrap as ``{"status": bool, "message": str, "data": {...}}``;
``kyc_request`` unwraps that envelope and raises :class:`VoiceLinkKycError`
on any HTTP or envelope-level failure so routes can map it to a 502.
"""

import json
import os
from typing import Any, Dict, Optional, Tuple

import aiohttp
from loguru import logger

DEFAULT_VOICELINK_API_BASE = "https://app.voicelink.co.in/api"


class VoiceLinkKycError(Exception):
    """Raised when a VoiceLink KYC API call fails (HTTP or envelope level)."""


class VoiceLinkKycClient:
    """Reseller-scoped VoiceLink KYC API client."""

    def __init__(
        self,
        api_base: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.api_base = (
            api_base
            or os.getenv("VOICELINK_API_BASE")
            or DEFAULT_VOICELINK_API_BASE
        ).rstrip("/")
        self.username = (
            username
            if username is not None
            else os.getenv("VOICELINK_RESELLER_USERNAME")
        )
        self.password = (
            password
            if password is not None
            else os.getenv("VOICELINK_RESELLER_PASSWORD")
        )
        self._access_token: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        """True when reseller credentials are present in the environment."""
        return bool(self.username and self.password)

    # ======== AUTH / HTTP HELPERS ========

    async def _login(self) -> str:
        """Obtain a bearer token via /v1/auth/login. Never logs the password."""
        if not self.is_configured:
            raise VoiceLinkKycError(
                "VoiceLink reseller credentials are not configured"
            )

        endpoint = f"{self.api_base}/v1/auth/login"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint,
                    json={"username": self.username, "password": self.password},
                ) as response:
                    body = await response.text()
                    if response.status not in (200, 201):
                        logger.error(
                            f"VoiceLink KYC login failed for user {self.username}: "
                            f"HTTP {response.status}"
                        )
                        raise VoiceLinkKycError(
                            f"VoiceLink login failed: HTTP {response.status}"
                        )
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        raise VoiceLinkKycError(
                            "VoiceLink login returned a non-JSON response"
                        )
        except aiohttp.ClientError as e:
            raise VoiceLinkKycError(f"VoiceLink login request failed: {e}")

        token = (data.get("data") or {}).get("access_token")
        if not token:
            raise VoiceLinkKycError(
                "VoiceLink login response missing data.access_token"
            )

        logger.info(f"VoiceLink KYC login succeeded for user {self.username}")
        self._access_token = token
        return token

    async def _send_request(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]],
        token: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Any]:
        """Single HTTP exchange against the VoiceLink API."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method, url, json=payload, params=params, headers=headers
                ) as response:
                    body = await response.text()
                    try:
                        data = json.loads(body)
                    except json.JSONDecodeError:
                        data = {"raw": body}
                    return response.status, data
        except aiohttp.ClientError as e:
            raise VoiceLinkKycError(f"VoiceLink request failed: {e}")

    async def _api_request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Any]:
        """Authenticated VoiceLink API request with one 401 re-login retry."""
        token = self._access_token or await self._login()
        url = f"{self.api_base}{path}"

        status, data = await self._send_request(method, url, payload, token, params)
        if status == 401:
            logger.info(
                "VoiceLink KYC token rejected (401); re-logging in and retrying"
            )
            token = await self._login()
            status, data = await self._send_request(
                method, url, payload, token, params
            )
        return status, data

    async def kyc_request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make a KYC API call and unwrap the {status, message, data} envelope.

        Returns the full envelope dict on success. Raises VoiceLinkKycError on
        HTTP errors or when the envelope reports ``status: false``.
        """
        status, data = await self._api_request(method, path, payload, params)
        if (
            status not in (200, 201)
            or not isinstance(data, dict)
            or not data.get("status")
        ):
            message = (
                data.get("message") if isinstance(data, dict) else None
            ) or f"HTTP {status}"
            logger.error(f"VoiceLink KYC request {method} {path} failed: {message}")
            raise VoiceLinkKycError(f"VoiceLink KYC request failed: {message}")
        return data

    # ======== KYC ENDPOINTS ========

    @staticmethod
    def _with_client_id(
        payload: Dict[str, Any], client_id: Optional[str]
    ) -> Dict[str, Any]:
        """Attach client_id when scoped to a client; omit it for reseller-own KYC."""
        if client_id:
            return {**payload, "client_id": client_id}
        return payload

    async def get_status(self, client_id: Optional[str] = None) -> Dict[str, Any]:
        params = {"client_id": client_id} if client_id else None
        return await self.kyc_request(
            "GET", "/v1/reseller/kyc/status", params=params
        )

    async def step1_register_details(
        self, details: Dict[str, Any], client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self.kyc_request(
            "POST",
            "/v1/reseller/kyc/step-1-register-details",
            payload=self._with_client_id(details, client_id),
        )

    async def step2_pan_verify(
        self, details: Dict[str, Any], client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self.kyc_request(
            "POST",
            "/v1/reseller/kyc/step-2-pan-verify",
            payload=self._with_client_id(details, client_id),
        )

    async def step3_aadhaar_init(
        self,
        redirect_url: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if redirect_url:
            payload["redirect_url"] = redirect_url
        return await self.kyc_request(
            "POST",
            "/v1/reseller/kyc/step-3-aadhaar-init",
            payload=self._with_client_id(payload, client_id),
        )

    async def step4_gst_verify(
        self, details: Dict[str, Any], client_id: Optional[str] = None
    ) -> Dict[str, Any]:
        return await self.kyc_request(
            "POST",
            "/v1/reseller/kyc/step-4-gst-verify",
            payload=self._with_client_id(details, client_id),
        )

    async def final_submit(self, client_id: Optional[str] = None) -> Dict[str, Any]:
        return await self.kyc_request(
            "POST",
            "/v1/reseller/kyc/final-submit",
            payload=self._with_client_id({}, client_id),
        )


_kyc_client: Optional[VoiceLinkKycClient] = None


def get_kyc_client() -> VoiceLinkKycClient:
    """Process-wide singleton so the bearer token survives across requests."""
    global _kyc_client
    if _kyc_client is None:
        _kyc_client = VoiceLinkKycClient()
    return _kyc_client


async def resolve_org_voicelink_client_id(
    organization_id: int,
) -> Tuple[Optional[str], bool]:
    """Resolve the VoiceLink ``client_id`` for an organization.

    Prefers the ``client_id`` credential stored on the org's VoiceLink
    telephony configuration(s) (JSONB credentials, default outbound first).
    Falls back to ``OrganizationModel.voicelink_client_id`` — the id stored at
    provisioning time — so client-side KYC scopes to the client's VoiceLink
    account as soon as the client is provisioned, before any DID/telephony
    configuration is assigned.

    Returns:
        (client_id, has_voicelink_config) — ``has_voicelink_config`` reflects
        whether a VoiceLink telephony configuration exists. ``client_id`` is
        None only when neither a config nor the org carries one (KYC calls then
        act on the reseller's own KYC).
    """
    from api.db import db_client

    configs = await db_client.list_telephony_configurations_by_provider(
        organization_id, "voicelink"
    )
    has_voicelink_config = bool(configs)

    if configs:
        ordered = sorted(configs, key=lambda c: not c.is_default_outbound)
        for config in ordered:
            client_id = (config.credentials or {}).get("client_id")
            if client_id:
                return str(client_id), has_voicelink_config

    organization = await db_client.get_organization_by_id(organization_id)
    org_client_id = getattr(organization, "voicelink_client_id", None)
    if org_client_id:
        return str(org_client_id), has_voicelink_config

    return None, has_voicelink_config
