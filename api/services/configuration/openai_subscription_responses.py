"""Bounded Codex Responses SSE client using the organization's subscription login.

Request/identity and sparse-output handling adapted from Hermes Agent at 4eb66e3.
See THIRD_PARTY_NOTICES.md. This module does not load the Hermes runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any

import httpx
from openai.types.responses import Response, ResponseCompletedEvent, ResponseStreamEvent
from openai.types.responses.response_usage import (
    InputTokensDetails,
    OutputTokensDetails,
    ResponseUsage,
)
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from api.services.configuration.openai_subscription_auth import (
    SubscriptionAuthError,
    SubscriptionAuthService,
)

_CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
_EVENT_ADAPTER = TypeAdapter(
    Annotated[ResponseStreamEvent, Field(discriminator="type")]
)
_REQUEST_FIELDS = frozenset(
    {
        "model",
        "input",
        "instructions",
        "stream",
        "store",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "include",
        "text",
        "service_tier",
        "prompt_cache_key",
    }
)
# These public-API settings are rejected by the consumer Codex backend.
_OMITTED_FIELDS = frozenset(
    {"max_output_tokens", "temperature", "top_p", "prompt_cache_retention"}
)
_MESSAGES = {
    "invalid_request": "The subscription reasoning request contains unsupported or invalid settings.",
    "request_too_large": "The subscription reasoning context is too large for this integration.",
    "reauthentication_required": "Subscription reasoning access was rejected. Check the connected Codex login and account access.",
    "rate_limited": "Subscription reasoning is rate limited. Try again later.",
    "provider_error": "Subscription reasoning failed. Start a new request; the interrupted request was not retried.",
    "redirect_rejected": "Subscription reasoning returned an unexpected redirect. No credentials were forwarded.",
    "invalid_response": "Subscription reasoning returned an unsupported response. Start a new request.",
    "incomplete_response": "Subscription reasoning ended before a completed response. The request was not retried.",
    "response_too_large": "The subscription reasoning response exceeded this integration's size limit.",
    "timeout": "Subscription reasoning timed out. The request was not retried.",
    "closed": "The subscription reasoning client is closed.",
}


class SubscriptionResponsesError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        self.safe_message = _MESSAGES[code]
        super().__init__(self.safe_message)


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _request_body(params: Mapping[str, Any], max_bytes: int) -> tuple[dict, bytes]:
    if (
        not isinstance(params, Mapping)
        or set(params) - _REQUEST_FIELDS - _OMITTED_FIELDS
    ):
        raise SubscriptionResponsesError("invalid_request")
    body = {
        key: _json_value(value)
        for key, value in params.items()
        if key in _REQUEST_FIELDS
    }
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise SubscriptionResponsesError("invalid_request")
    instructions = body.get("instructions")
    if instructions is None or (
        isinstance(instructions, str) and not instructions.strip()
    ):
        instructions = "You are a helpful assistant."
    if not isinstance(instructions, str):
        raise SubscriptionResponsesError("invalid_request")
    body["instructions"] = instructions
    if not isinstance(body.get("input"), (list, str)):
        raise SubscriptionResponsesError("invalid_request")
    if not body["input"]:
        body["input"] = [{"role": "user", "content": ""}]
    tools = body.get("tools")
    if tools is None or tools == []:
        body.pop("tools", None)
    elif not isinstance(tools, list) or any(
        not isinstance(tool, dict) or tool.get("type") != "function" for tool in tools
    ):
        raise SubscriptionResponsesError("invalid_request")
    if "reasoning" in body:
        if not isinstance(body["reasoning"], dict):
            raise SubscriptionResponsesError("invalid_request")
        body["reasoning"] = {
            key: value for key, value in body["reasoning"].items() if value is not None
        }
        if not body["reasoning"]:
            body.pop("reasoning")
    body["stream"] = True
    body["store"] = False
    try:
        encoded = json.dumps(
            body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise SubscriptionResponsesError("invalid_request") from None
    if len(encoded) > max_bytes:
        raise SubscriptionResponsesError("request_too_large")
    return body, encoded


def _usage(payload: Any) -> ResponseUsage | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise SubscriptionResponsesError("invalid_response")

    def counts(data: dict, names: tuple[str, ...]) -> dict:
        result = {name: data.get(name) for name in names}
        if any(
            value is not None and (type(value) is not int or value < 0)
            for value in result.values()
        ):
            raise SubscriptionResponsesError("invalid_response")
        return result

    def details(name: str, model, names: tuple[str, ...]):
        data = payload.get(name)
        if data is None:
            return None
        if not isinstance(data, dict):
            raise SubscriptionResponsesError("invalid_response")
        return model.model_construct(**counts(data, names))

    # Match the SDK's lenient usage decoder: absent measurements stay unknown,
    # including fields added to the public API but absent on the consumer endpoint.
    return ResponseUsage.model_construct(
        **counts(payload, ("input_tokens", "output_tokens", "total_tokens")),
        input_tokens_details=details(
            "input_tokens_details",
            InputTokensDetails,
            ("cached_tokens", "cache_write_tokens"),
        ),
        output_tokens_details=details(
            "output_tokens_details", OutputTokensDetails, ("reasoning_tokens",)
        ),
    )


class SubscriptionResponseStream(AsyncIterator[ResponseStreamEvent]):
    def __init__(
        self,
        owner: SubscriptionResponsesClient,
        client: httpx.AsyncClient,
        response: httpx.Response,
        request_body: dict,
        deadline: float,
    ):
        self._owner = owner
        self._client = client
        self._response = response
        self._request_body = request_body
        self._deadline = deadline
        self._chunks = response.aiter_bytes().__aiter__()
        self._buffer = bytearray()
        self._data_lines: list[bytes] = []
        self._event_bytes = 0
        self._total_bytes = 0
        self._done_items: dict[int, dict] = {}
        self._response_id: str | None = None
        self._response_metadata: dict = {}
        self._closed = False
        self._completed: Response | None = None

    def __aiter__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()

    async def __anext__(self) -> ResponseStreamEvent:
        if self._closed:
            raise StopAsyncIteration
        try:
            async with asyncio.timeout_at(self._deadline):
                event = await self._next_event()
            if self._closed:
                raise StopAsyncIteration
            if isinstance(event, ResponseCompletedEvent):
                self._completed = event.response
                await self.aclose()
            return event
        except BaseException as exc:
            await self.aclose()
            if isinstance(exc, TimeoutError):
                raise SubscriptionResponsesError("timeout") from None
            if isinstance(exc, httpx.HTTPError):
                raise SubscriptionResponsesError("provider_error") from None
            raise

    async def _next_event(self) -> ResponseStreamEvent:
        while True:
            line = await self._next_line()
            self._event_bytes += len(line) + 1
            if self._event_bytes > self._owner.max_event_bytes:
                raise SubscriptionResponsesError("response_too_large")
            if not line:
                data_lines, self._data_lines = self._data_lines, []
                self._event_bytes = 0
                if not data_lines:
                    continue
                data = b"\n".join(data_lines)
                if data == b"[DONE]":
                    raise SubscriptionResponsesError("incomplete_response")
                try:
                    payload = json.loads(data)
                    if not isinstance(payload, dict):
                        raise TypeError
                except (ValueError, TypeError, UnicodeError):
                    raise SubscriptionResponsesError("invalid_response") from None
                return self._decode_event(payload)
            if line.startswith(b"data:"):
                value = line[5:]
                self._data_lines.append(value[1:] if value.startswith(b" ") else value)
            # SSE comments, event, id and retry fields are metadata, not request instructions.

    async def _next_line(self) -> bytes:
        while True:
            split = self._buffer.find(b"\n")
            if split >= 0:
                line = bytes(self._buffer[:split]).removesuffix(b"\r")
                del self._buffer[: split + 1]
                return line
            if len(self._buffer) > self._owner.max_event_bytes:
                raise SubscriptionResponsesError("response_too_large")
            try:
                chunk = await self._chunks.__anext__()
            except StopAsyncIteration:
                raise SubscriptionResponsesError("incomplete_response") from None
            self._total_bytes += len(chunk)
            if self._total_bytes > self._owner.max_response_bytes:
                raise SubscriptionResponsesError("response_too_large")
            self._buffer.extend(chunk)

    def _decode_event(self, payload: dict) -> ResponseStreamEvent:
        event_type = payload.get("type")
        if event_type in {"error", "response.failed"}:
            raise SubscriptionResponsesError("provider_error")
        if event_type == "response.incomplete":
            raise SubscriptionResponsesError("incomplete_response")
        if isinstance(payload.get("response"), dict):
            payload = {
                **payload,
                "response": self._normalize_response(payload["response"], event_type),
            }
        # The Codex wire may omit logprobs; this is empty rather than invented probability data.
        if event_type == "response.output_text.delta":
            payload = {"logprobs": [], **payload}
        try:
            event = _EVENT_ADAPTER.validate_python(payload)
        except (ValidationError, ValueError, TypeError):
            raise SubscriptionResponsesError("invalid_response") from None
        if event_type == "response.output_item.done":
            if event.output_index < 0:
                raise SubscriptionResponsesError("invalid_response")
            item = event.item.model_dump(mode="json", by_alias=True, exclude_none=True)
            previous = self._done_items.get(event.output_index)
            if previous is not None and previous != item:
                raise SubscriptionResponsesError("invalid_response")
            self._done_items[event.output_index] = item
        return event

    def _normalize_response(self, response: dict, event_type: str) -> dict:
        response_id = response.get("id")
        if not isinstance(response_id, str) or not response_id:
            raise SubscriptionResponsesError("invalid_response")
        if self._response_id is not None and response_id != self._response_id:
            raise SubscriptionResponsesError("invalid_response")
        self._response_id = response_id
        self._response_metadata = {**self._response_metadata, **response}
        merged = {
            "object": "response",
            "model": self._request_body["model"],
            "tools": self._request_body.get("tools", []),
            "tool_choice": self._request_body.get("tool_choice", "auto"),
            "parallel_tool_calls": self._request_body.get("parallel_tool_calls", False),
            **self._response_metadata,
        }
        if event_type == "response.completed":
            if (
                response.get("status") != "completed"
                or response.get("error") is not None
            ):
                raise SubscriptionResponsesError("incomplete_response")
            output = response.get("output")
            if output is None or (output == [] and self._done_items):
                output = [self._done_items[index] for index in sorted(self._done_items)]
            merged["output"] = output
        elif merged.get("output") is None:
            merged["output"] = []
        merged["usage"] = _usage(merged.get("usage"))
        return merged

    async def close(self) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._owner._streams.discard(self)
        try:
            async with asyncio.timeout(5):
                await self._response.aclose()
        except (httpx.HTTPError, OSError, TimeoutError):
            pass
        finally:
            with contextlib.suppress(httpx.HTTPError, OSError, TimeoutError):
                async with asyncio.timeout(5):
                    await self._client.aclose()


class SubscriptionResponsesClient:
    def __init__(
        self,
        auth_service: SubscriptionAuthService,
        organization_id: int | str,
        *,
        expected_account_id: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 120,
        max_response_bytes: int = 8 * 1024 * 1024,
        max_event_bytes: int = 1024 * 1024,
        max_request_bytes: int = 4 * 1024 * 1024,
    ):
        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or min(max_response_bytes, max_event_bytes, max_request_bytes) <= 0
        ):
            raise ValueError(
                "Subscription Responses limits must be positive and finite"
            )
        self.auth_service = auth_service
        self.organization_id = organization_id
        self._expected_account_id = expected_account_id
        self._transport = transport
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_event_bytes = max_event_bytes
        self.max_request_bytes = max_request_bytes
        self._streams: set[SubscriptionResponseStream] = set()
        self._opening_tasks: set[asyncio.Task] = set()
        self._closed = False

    def bind_account(self, account_id: str) -> None:
        if not isinstance(account_id, str) or not account_id:
            raise SubscriptionAuthError("account_mismatch")
        if (
            self._expected_account_id is not None
            and self._expected_account_id != account_id
        ):
            raise SubscriptionAuthError("account_mismatch")
        self._expected_account_id = account_id

    async def stream(self, params: Mapping[str, Any]) -> SubscriptionResponseStream:
        if self._closed:
            raise SubscriptionResponsesError("closed")
        self.auth_service.assert_organization(self.organization_id)
        body, encoded = _request_body(params, self.max_request_bytes)
        opening_task = asyncio.current_task()
        assert opening_task is not None
        self._opening_tasks.add(opening_task)
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        client: httpx.AsyncClient | None = None
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout_at(deadline):
                credentials = await self.auth_service.get_credentials(
                    self.organization_id, expected_account_id=self._expected_account_id
                )
                if self._closed:
                    raise SubscriptionResponsesError("closed")
                self.bind_account(credentials.account_id)
                client = httpx.AsyncClient(
                    transport=self._transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(30, connect=10),
                )
                request = client.build_request(
                    "POST",
                    _CODEX_RESPONSES_URL,
                    headers={
                        "Authorization": f"Bearer {credentials.access_token}",
                        "ChatGPT-Account-ID": credentials.account_id,
                        "User-Agent": "Dograh/experimental-subscription",
                        "originator": "dograh",
                        "Accept": "text/event-stream",
                        "Accept-Encoding": "identity",
                        "Content-Type": "application/json",
                    },
                    content=encoded,
                )
                response = await client.send(
                    request, stream=True, follow_redirects=False
                )
                if self._closed:
                    raise SubscriptionResponsesError("closed")
                if (
                    response.headers.get("content-encoding", "identity").lower()
                    != "identity"
                ):
                    raise SubscriptionResponsesError("invalid_response")
                if response.status_code in {401, 403}:
                    raise SubscriptionResponsesError("reauthentication_required")
                if response.status_code == 429:
                    raise SubscriptionResponsesError("rate_limited")
                if 300 <= response.status_code < 400:
                    raise SubscriptionResponsesError("redirect_rejected")
                if response.status_code != 200:
                    raise SubscriptionResponsesError("provider_error")
                if (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                    != "text/event-stream"
                ):
                    raise SubscriptionResponsesError("invalid_response")
                result = SubscriptionResponseStream(
                    self, client, response, body, deadline
                )
                self._streams.add(result)
                return result
        except BaseException as exc:
            if response is not None:
                with contextlib.suppress(httpx.HTTPError, OSError, TimeoutError):
                    async with asyncio.timeout(5):
                        await response.aclose()
            if client is not None:
                with contextlib.suppress(httpx.HTTPError, OSError, TimeoutError):
                    async with asyncio.timeout(5):
                        await client.aclose()
            if isinstance(exc, TimeoutError):
                raise SubscriptionResponsesError("timeout") from None
            if isinstance(exc, httpx.HTTPError):
                raise SubscriptionResponsesError("provider_error") from None
            raise
        finally:
            self._opening_tasks.discard(opening_task)

    async def complete(self, params: Mapping[str, Any]) -> Response:
        async with await self.stream(params) as stream:
            async for event in stream:
                if isinstance(event, ResponseCompletedEvent):
                    return event.response
        raise SubscriptionResponsesError("incomplete_response")

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        opening = self._opening_tasks - {asyncio.current_task()}
        for task in opening:
            task.cancel()
        if opening:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(5):
                    await asyncio.gather(*opening, return_exceptions=True)
        for stream in tuple(self._streams):
            await stream.aclose()
