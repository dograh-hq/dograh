"""aiohttp client for the VoiceLink reseller client-management API.

Extends :class:`api.services.voicelink_kyc.client.VoiceLinkKycClient` so the
bearer-token auth (``/v1/auth/login`` with a single 401 re-login retry) and
the reseller credentials from the environment are shared with the KYC
client family. The create payload carries the client's plaintext password —
it is forwarded to VoiceLink and NEVER logged.
"""

from typing import Any, Dict, Optional

from loguru import logger

from api.services.voicelink_kyc.client import VoiceLinkKycClient, VoiceLinkKycError


class VoiceLinkClientError(Exception):
    """Raised when a VoiceLink client-management call fails.

    Carries the upstream HTTP status code when one was received (e.g. 422
    when the reseller has no channels available to allocate).
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class VoiceLinkClientsClient(VoiceLinkKycClient):
    """Reseller-scoped VoiceLink client management API client."""

    async def create_client(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """``POST /v1/reseller/client/create``.

        Returns the full ``{status, message, data}`` envelope on success.
        Raises :class:`VoiceLinkClientError` on HTTP, transport, or
        envelope-level failure. Never logs the password in ``payload``.
        """
        try:
            status, data = await self._api_request(
                "POST", "/v1/reseller/client/create", payload=payload
            )
        except VoiceLinkKycError as e:
            raise VoiceLinkClientError(str(e))

        # _api_request may hand back either the raw envelope ({status, message,
        # data}) or an already-unwrapped payload ({client_id: …}). Only treat an
        # EXPLICIT status:false as an envelope failure — a 2xx without a status
        # key is a success (live API returns 201 {status:true, data:{client_id}}).
        if (
            status in (200, 201)
            and isinstance(data, dict)
            and data.get("status", True) is not False
        ):
            logger.info(
                f"VoiceLink client created for username {payload.get('username')}"
            )
            return data

        message = (
            data.get("message") if isinstance(data, dict) else None
        ) or f"HTTP {status}"
        logger.error(
            f"VoiceLink client create failed for username "
            f"{payload.get('username')}: {message} (HTTP {status})"
        )
        raise VoiceLinkClientError(message, status_code=status)

    async def available_dids(self) -> list[Dict[str, Any]]:
        """``GET /v1/reseller/client/available-dids`` → DID records.

        Each carries ``did_id``/``did_number``/``type_label``/``country_code`` and
        ``user_status`` (1=Available 2=Assigned 3=Expired 4=Inactive).
        """
        try:
            status, data = await self._api_request(
                "GET", "/v1/reseller/client/available-dids"
            )
        except VoiceLinkKycError as e:
            raise VoiceLinkClientError(str(e))
        if (
            status in (200, 201)
            and isinstance(data, dict)
            and data.get("status", True) is not False
        ):
            dids = data.get("data")
            return dids if isinstance(dids, list) else []
        message = (
            data.get("message") if isinstance(data, dict) else None
        ) or f"HTTP {status}"
        raise VoiceLinkClientError(message, status_code=status)

    async def map_did(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """``POST /v1/reseller/client/map-did`` → assign a DID to a client.

        payload: client_id, did_id, call_recording 0|1, user_status,
        user_expiry_date, client_auto_renew. Returns the success envelope.
        """
        try:
            status, data = await self._api_request(
                "POST", "/v1/reseller/client/map-did", payload=payload
            )
        except VoiceLinkKycError as e:
            raise VoiceLinkClientError(str(e))
        if (
            status in (200, 201)
            and isinstance(data, dict)
            and data.get("status", True) is not False
        ):
            return data
        message = (
            data.get("message") if isinstance(data, dict) else None
        ) or f"HTTP {status}"
        raise VoiceLinkClientError(message, status_code=status)

    async def list_clients(self) -> list[Dict[str, Any]]:
        """``GET /v1/reseller/clients`` → the list of reseller client records.

        Each record carries at least ``id`` and ``username`` (plus name/email/
        wallet/channels). Raises :class:`VoiceLinkClientError` on HTTP or
        envelope-level failure.
        """
        try:
            status, data = await self._api_request("GET", "/v1/reseller/clients")
        except VoiceLinkKycError as e:
            raise VoiceLinkClientError(str(e))

        if (
            status in (200, 201)
            and isinstance(data, dict)
            and data.get("status", True) is not False
        ):
            clients = data.get("data")
            return clients if isinstance(clients, list) else []

        message = (
            data.get("message") if isinstance(data, dict) else None
        ) or f"HTTP {status}"
        raise VoiceLinkClientError(message, status_code=status)


_clients_client: Optional[VoiceLinkClientsClient] = None


def get_voicelink_clients_client() -> VoiceLinkClientsClient:
    """Process-wide singleton so the bearer token survives across requests."""
    global _clients_client
    if _clients_client is None:
        _clients_client = VoiceLinkClientsClient()
    return _clients_client
