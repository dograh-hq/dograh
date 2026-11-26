"""Thin async client for the Dial REST API.

Only the endpoints this integration uses are wrapped. Reference:
https://docs.getdial.ai/api-reference/rest-api
"""

from typing import Any, Dict, List, Optional

import aiohttp

DIAL_API_BASE_URL = "https://api.getdial.ai/api/v1"

_TIMEOUT = aiohttp.ClientTimeout(total=15)


class DialAPIError(Exception):
    """A Dial REST call failed. ``status_code`` is None when no response arrived."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _error_text(payload: Any) -> str:
    """Pull Dial's ``error`` field out of a response body, whatever its shape."""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
    return str(payload)[:300]


class DialClient:
    """Authenticated wrapper around the handful of Dial endpoints we call."""

    def __init__(self, api_key: str, *, base_url: str = DIAL_API_BASE_URL) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        request_headers = {"Authorization": f"Bearer {self._api_key}"}
        if headers:
            request_headers.update(headers)
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.request(
                    method,
                    f"{self._base_url}{path}",
                    json=json,
                    params=params,
                    headers=request_headers,
                ) as response:
                    try:
                        payload = await response.json(content_type=None)
                    except ValueError:
                        payload = {"error": (await response.text())[:300]}
                    if response.status >= 400:
                        raise DialAPIError(
                            f"Dial API {method} {path} returned {response.status}: "
                            f"{_error_text(payload)}",
                            status_code=response.status,
                        )
                    return payload if isinstance(payload, dict) else {}
        except aiohttp.ClientError as e:
            raise DialAPIError(f"Could not reach the Dial API: {e}") from e
        except TimeoutError as e:
            raise DialAPIError("Timed out waiting for the Dial API") from e

    # ---- account ---------------------------------------------------------

    async def get_account(self) -> Dict[str, Any]:
        return await self._request("GET", "/account")

    # ---- numbers ---------------------------------------------------------

    async def list_numbers(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/numbers")
        numbers = data.get("numbers")
        return numbers if isinstance(numbers, list) else []

    # ---- calls -----------------------------------------------------------

    async def make_call(
        self,
        body: Dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        data = await self._request("POST", "/calls", json=body, headers=headers)
        return data.get("call") or {}

    async def get_call(self, call_id: str) -> Dict[str, Any]:
        data = await self._request("GET", f"/calls/{call_id}")
        return data.get("call") or {}

    # ---- self-hosted audio -----------------------------------------------

    async def get_self_hosted(self) -> Dict[str, Any]:
        return await self._request("GET", "/self-hosted")

    async def activate_self_hosted_audio(
        self, ws_url: str, audio_format: str
    ) -> Dict[str, Any]:
        return await self._request(
            "POST",
            "/self-hosted",
            json={
                "action": "activate",
                "config": {
                    "type": "audio",
                    "wsUrl": ws_url,
                    "audioInboundFormat": audio_format,
                    "audioOutboundFormat": audio_format,
                },
            },
        )

    async def request_self_hosted_access(
        self, use_case: str, company_url: Optional[str] = None
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"action": "request_access", "useCase": use_case}
        if company_url:
            body["companyUrl"] = company_url
        return await self._request("POST", "/self-hosted", json=body)

    async def get_self_hosted_secret(self, mode: str = "audio") -> str:
        data = await self._request("GET", "/self-hosted/secret", params={"mode": mode})
        return str(data.get("secret") or "")

    # ---- webhooks --------------------------------------------------------

    async def list_webhooks(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", "/webhooks")
        webhooks = data.get("webhooks")
        return webhooks if isinstance(webhooks, list) else []

    async def create_webhook(
        self, target_url: str, event_types: List[str]
    ) -> Dict[str, Any]:
        data = await self._request(
            "POST",
            "/webhooks",
            json={"targetUrl": target_url, "eventTypes": event_types},
        )
        return data.get("webhook") or {}

    async def get_webhook_secret(self, webhook_id: str) -> str:
        data = await self._request("GET", f"/webhooks/{webhook_id}/secret")
        return str(data.get("secret") or "")
