import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemDoneEvent,
    ResponseTextDeltaEvent,
)

from api.services.configuration.openai_subscription_auth import (
    SubscriptionAuthError,
    SubscriptionCredentials,
)
from api.services.configuration.openai_subscription_responses import (
    SubscriptionResponsesClient,
    SubscriptionResponsesError,
)

_ACCOUNT = "synthetic-account"
_TOKEN = "synthetic-oauth-only"
_PARAMS = {
    "model": "gpt-5.6-luna",
    "instructions": "Run only the provided Dograh workflow tools.",
    "input": [{"role": "user", "content": "Look up a synthetic order."}],
}


def _message(text="Synthetic order is ready."):
    return {
        "type": "message",
        "id": "msg_synthetic",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _response(output=None, status="completed"):
    return {
        "id": "resp_synthetic",
        "object": "response",
        "created_at": 2_000_000_000,
        "model": "gpt-5.6-luna",
        "output": output,
        "status": status,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 8,
            "total_tokens": 20,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }


def _completed(output=None):
    return {
        "type": "response.completed",
        "sequence_number": 3,
        "response": _response(output),
    }


def _done_item(item=None, index=0, sequence=2):
    return {
        "type": "response.output_item.done",
        "sequence_number": sequence,
        "output_index": index,
        "item": item or _message(),
    }


def _sse(*events):
    return b"".join(
        b"data: " + json.dumps(event).encode() + b"\n\n" for event in events
    )


class _ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0, error=None):
        self.chunks = chunks
        self.delay = delay
        self.error = error
        self.closed = False
        self.entered = asyncio.Event()

    async def __aiter__(self):
        self.entered.set()
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
        if self.error:
            raise self.error

    async def aclose(self):
        self.closed = True


class SubscriptionResponsesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.auth = Mock()
        self.auth.assert_organization = Mock()
        self.auth.get_credentials = AsyncMock(
            return_value=SubscriptionCredentials(
                _TOKEN, _ACCOUNT, 2_000_003_600, "synthetic-refresh"
            )
        )
        self.requests = []
        self.byte_stream = _ByteStream([_sse(_done_item(), _completed())])
        self.response = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=self.byte_stream
        )
        self.open_delay = 0

        async def handler(request):
            self.requests.append(request)
            if self.open_delay:
                await asyncio.sleep(self.open_delay)
            return self.response

        self.transport = httpx.MockTransport(handler)
        self.client = SubscriptionResponsesClient(
            self.auth, 42, transport=self.transport
        )
        self.addAsyncCleanup(self.client.aclose)

    def set_events(self, *events, **kwargs):
        self.byte_stream = _ByteStream([_sse(*events)], **kwargs)
        self.response = httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            stream=self.byte_stream,
        )

    async def assert_error(self, code, operation):
        with self.assertRaises(SubscriptionResponsesError) as caught:
            await operation
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(_TOKEN, str(caught.exception))
        self.assertNotIn("raw-private", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    async def test_fixed_request_contract_ignores_all_ambient_api_settings(self):
        with patch.dict(
            "os.environ",
            {
                "OPENAI_API_KEY": "paid-key-must-not-be-used",
                "OPENAI_BASE_URL": "https://attacker.invalid/v1",
                "OPENAI_ORG_ID": "ambient-organization",
                "OPENAI_PROJECT_ID": "ambient-project",
                "HTTP_PROXY": "http://attacker.invalid:3128",
                "HTTPS_PROXY": "http://attacker.invalid:3128",
            },
        ):
            result = await self.client.complete(
                {
                    **_PARAMS,
                    "stream": False,
                    "store": True,
                    "temperature": 0.2,
                    "top_p": 0.9,
                    "max_output_tokens": 256,
                    "prompt_cache_retention": "24h",
                }
            )
        self.assertIsInstance(result, Response)
        self.assertEqual(result.output_text, "Synthetic order is ready.")
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual(
            str(request.url), "https://chatgpt.com/backend-api/codex/responses"
        )
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["authorization"], f"Bearer {_TOKEN}")
        self.assertEqual(request.headers["chatgpt-account-id"], _ACCOUNT)
        self.assertEqual(request.headers["originator"], "dograh")
        self.assertEqual(
            request.headers["user-agent"], "Dograh/experimental-subscription"
        )
        self.assertNotIn("openai-organization", request.headers)
        self.assertNotIn("openai-project", request.headers)
        self.assertNotIn("cookie", request.headers)
        self.assertNotIn("paid-key", str(request.headers))
        body = json.loads(request.content)
        self.assertIs(body["stream"], True)
        self.assertIs(body["store"], False)
        self.assertEqual(body["model"], _PARAMS["model"])
        self.assertEqual(body["instructions"], _PARAMS["instructions"])
        for omitted in [
            "temperature",
            "top_p",
            "max_output_tokens",
            "prompt_cache_retention",
        ]:
            self.assertNotIn(omitted, body)
        self.auth.assert_organization.assert_called_once_with(42)
        self.auth.get_credentials.assert_awaited_once_with(42, expected_account_id=None)
        self.assertTrue(self.byte_stream.closed)

    async def test_native_function_history_and_reasoning_are_preserved_without_mutation(
        self,
    ):
        params = {
            **_PARAMS,
            "tools": [
                {
                    "type": "function",
                    "name": "lookup_order",
                    "description": "Synthetic lookup",
                    "parameters": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                }
            ],
            "input": [
                {
                    "type": "function_call",
                    "call_id": "call_synthetic",
                    "name": "lookup_order",
                    "arguments": '{"id":"1"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_synthetic",
                    "output": '{"ready":true}',
                },
            ],
            "reasoning": {"effort": "low", "summary": None},
            "include": ["reasoning.encrypted_content"],
        }
        original = json.dumps(params)
        await self.client.complete(params)
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["input"], params["input"])
        self.assertEqual(body["tools"], params["tools"])
        self.assertEqual(body["reasoning"], {"effort": "low"})
        self.assertEqual(body["include"], ["reasoning.encrypted_content"])
        self.assertEqual(json.dumps(params), original)

    async def test_missing_instructions_receive_explicit_default_and_empty_tools_are_omitted(
        self,
    ):
        await self.client.complete(
            {"model": _PARAMS["model"], "input": [], "tools": []}
        )
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["instructions"], "You are a helpful assistant.")
        self.assertEqual(body["input"], [{"role": "user", "content": ""}])
        self.assertNotIn("tools", body)

    async def test_unknown_override_fields_and_builtin_tools_fail_before_auth(self):
        for field in [
            "extra_headers",
            "extra_body",
            "base_url",
            "api_key",
            "previous_response_id",
            "organization",
            "project",
            "max_retries",
        ]:
            with self.subTest(field=field):
                await self.assert_error(
                    "invalid_request",
                    self.client.complete({**_PARAMS, field: "raw-private"}),
                )
        for tool in [{"type": "web_search"}, {"type": "computer_use_preview"}]:
            await self.assert_error(
                "invalid_request", self.client.complete({**_PARAMS, "tools": [tool]})
            )
        self.auth.get_credentials.assert_not_called()
        self.assertFalse(self.requests)

    async def test_model_required_and_invalid_json_rejected_without_fallback(self):
        for model in [None, "", " ", 1]:
            await self.assert_error(
                "invalid_request", self.client.complete({**_PARAMS, "model": model})
            )
        await self.assert_error(
            "invalid_request",
            self.client.complete({**_PARAMS, "input": [{"not_json": object()}]}),
        )
        self.auth.get_credentials.assert_not_called()
        self.assertFalse(self.requests)

    async def test_wrong_org_disabled_or_bad_auth_never_starts_http(self):
        self.auth.assert_organization.side_effect = SubscriptionAuthError(
            "organization_mismatch"
        )
        with self.assertRaises(SubscriptionAuthError):
            await self.client.stream(_PARAMS)
        self.auth.get_credentials.assert_not_called()
        self.auth.assert_organization.side_effect = None
        self.auth.get_credentials.side_effect = SubscriptionAuthError("login_required")
        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "paid-key"}),
            self.assertRaises(SubscriptionAuthError),
        ):
            await self.client.complete(_PARAMS)
        self.assertFalse(self.requests)

    async def test_account_binding_is_idempotent_and_blocks_switched_voice_account(
        self,
    ):
        self.client.bind_account(_ACCOUNT)
        self.client.bind_account(_ACCOUNT)
        with self.assertRaises(SubscriptionAuthError) as caught:
            self.client.bind_account("another-account")
        self.assertEqual(caught.exception.code, "account_mismatch")
        await self.client.complete(_PARAMS)
        self.auth.get_credentials.assert_awaited_once_with(
            42, expected_account_id=_ACCOUNT
        )
        self.auth.get_credentials.return_value = SubscriptionCredentials(
            _TOKEN, "switched-account", 2_000_003_600, "refresh"
        )
        with self.assertRaises(SubscriptionAuthError):
            await self.client.complete(_PARAMS)
        self.assertEqual(len(self.requests), 1)

    async def test_stream_uses_sdk_event_types_and_reconstructs_null_final_output(self):
        delta = {
            "type": "response.output_text.delta",
            "sequence_number": 1,
            "item_id": "msg_synthetic",
            "output_index": 0,
            "content_index": 0,
            "delta": "Synthetic order is ready.",
        }
        self.set_events(delta, _done_item(), _completed(None))
        stream = await self.client.stream(_PARAMS)
        events = [event async for event in stream]
        self.assertIsInstance(events[0], ResponseTextDeltaEvent)
        self.assertIsInstance(events[1], ResponseOutputItemDoneEvent)
        self.assertIsInstance(events[2], ResponseCompletedEvent)
        self.assertEqual(events[2].response.output_text, "Synthetic order is ready.")
        self.assertEqual(events[2].response.usage.total_tokens, 20)
        self.assertTrue(self.byte_stream.closed)
        await stream.close()

    async def test_sparse_usage_retains_unknown_fields_and_rejects_invalid_counts(self):
        event = _completed([_message()])
        event["response"]["usage"] = {
            "input_tokens": 12,
            "input_tokens_details": {"cached_tokens": 4},
        }
        self.set_events(event)
        result = await self.client.complete(_PARAMS)
        self.assertEqual(result.usage.input_tokens, 12)
        self.assertIsNone(result.usage.output_tokens)
        self.assertIsNone(result.usage.total_tokens)
        self.assertIsNone(result.usage.input_tokens_details.cache_write_tokens)
        self.assertIsNone(result.usage.output_tokens_details)
        for invalid in [True, -1, "12"]:
            event["response"]["usage"] = {"input_tokens": invalid}
            self.set_events(event)
            await self.assert_error("invalid_response", self.client.complete(_PARAMS))

    async def test_complete_uses_terminal_output_when_present(self):
        self.set_events(_completed([_message("Full terminal output.")]))
        result = await self.client.complete(_PARAMS)
        self.assertEqual(result.output_text, "Full terminal output.")

    async def test_done_items_assemble_in_output_index_order_with_function_calls(self):
        function = {
            "type": "function_call",
            "id": "fc_synthetic",
            "name": "lookup_order",
            "call_id": "call_synthetic",
            "arguments": '{"id":"1"}',
            "status": "completed",
        }
        self.set_events(
            _done_item(_message(), index=1, sequence=1),
            _done_item(function, index=0),
            _completed(None),
        )
        result = await self.client.complete(_PARAMS)
        self.assertIsInstance(result.output[0], ResponseFunctionToolCall)
        self.assertEqual(result.output[0].arguments, '{"id":"1"}')
        self.assertEqual(result.output[1].id, "msg_synthetic")

    async def test_created_response_metadata_survives_sparse_final_frame(self):
        created = {
            "type": "response.created",
            "sequence_number": 0,
            "response": _response(None, "in_progress"),
        }
        final = {
            "type": "response.completed",
            "sequence_number": 3,
            "response": {"id": "resp_synthetic", "status": "completed", "output": None},
        }
        self.set_events(created, _done_item(), final)
        result = await self.client.complete(_PARAMS)
        self.assertEqual(result.created_at, 2_000_000_000)
        self.assertEqual(result.output_text, "Synthetic order is ready.")

    async def test_sse_fragmentation_crlf_comments_and_multiline_json(self):
        message = _done_item(_message("Ready café."))
        encoded = (
            b": keepalive\r\nevent: response.output_item.done\r\ndata: "
            + json.dumps(message, ensure_ascii=False).encode()
            + b"\r\n\r\n"
        )
        final_json = json.dumps(_completed(None), indent=2)
        encoded += (
            b"\n".join(b"data: " + line.encode() for line in final_json.splitlines())
            + b"\n\n"
        )
        self.byte_stream = _ByteStream([bytes([byte]) for byte in encoded])
        self.response = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=self.byte_stream
        )
        result = await self.client.complete(_PARAMS)
        self.assertEqual(result.output_text, "Ready café.")

    async def test_failed_incomplete_and_error_events_are_redacted_and_not_retried(
        self,
    ):
        for event, code in [
            ({"type": "error", "message": f"raw-private {_TOKEN}"}, "provider_error"),
            (
                {
                    "type": "response.failed",
                    "response": {"error": {"message": f"raw-private {_TOKEN}"}},
                },
                "provider_error",
            ),
            (
                {
                    "type": "response.incomplete",
                    "response": {"incomplete_details": {"reason": "raw-private"}},
                },
                "incomplete_response",
            ),
        ]:
            with self.subTest(event=event["type"]):
                self.set_events(_done_item(), event)
                await self.assert_error(code, self.client.complete(_PARAMS))
                self.assertTrue(self.byte_stream.closed)
        self.assertEqual(len(self.requests), 3)

    async def test_disconnect_or_done_without_terminal_never_fabricates_success(self):
        for suffix in [b"", b"data: [DONE]\n\n"]:
            self.byte_stream = _ByteStream([_sse(_done_item()) + suffix])
            self.response = httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=self.byte_stream,
            )
            await self.assert_error(
                "incomplete_response", self.client.complete(_PARAMS)
            )
            self.assertTrue(self.byte_stream.closed)
        self.assertEqual(len(self.requests), 2)

    async def test_malformed_unknown_event_and_mixed_response_identity_fail_closed(
        self,
    ):
        cases = [
            b"data: not-json raw-private\n\n",
            _sse({"type": "unknown"}),
            _sse({"type": "response.output_text.delta", "delta": 42}),
            _sse(
                {
                    "type": "response.created",
                    "sequence_number": 0,
                    "response": _response([], "in_progress"),
                },
                {**_completed(), "response": {**_response([]), "id": "other-response"}},
            ),
        ]
        for content in cases:
            self.byte_stream = _ByteStream([content])
            self.response = httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=self.byte_stream,
            )
            await self.assert_error("invalid_response", self.client.complete(_PARAMS))
            self.assertTrue(self.byte_stream.closed)

    async def test_redirect_and_http_errors_do_not_forward_credentials_or_retry(self):
        for status, code in [
            (307, "redirect_rejected"),
            (302, "redirect_rejected"),
            (401, "reauthentication_required"),
            (403, "reauthentication_required"),
            (429, "rate_limited"),
            (400, "provider_error"),
            (500, "provider_error"),
        ]:
            with self.subTest(status=status):
                self.byte_stream = _ByteStream([b"raw-private-token-body"])
                self.response = httpx.Response(
                    status,
                    headers={"location": "https://attacker.invalid/steal"},
                    stream=self.byte_stream,
                )
                await self.assert_error(code, self.client.complete(_PARAMS))
                self.assertFalse(self.byte_stream.entered.is_set())
                self.assertTrue(self.byte_stream.closed)
        self.assertEqual(len(self.requests), 7)
        self.assertTrue(
            all(request.url.host == "chatgpt.com" for request in self.requests)
        )

    async def test_missing_content_type_accepts_validated_sse_completion(self):
        content = _sse(_done_item(), _completed())
        self.byte_stream = _ByteStream([content[:17], content[17:]])
        self.response = httpx.Response(200, stream=self.byte_stream)
        result = await self.client.complete(_PARAMS)
        self.assertEqual(result.output_text, "Synthetic order is ready.")
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.byte_stream.closed)

    async def test_missing_content_type_still_requires_valid_complete_sse(self):
        for content, code in [
            (b'{"message":"raw-private"}', "incomplete_response"),
            (b"<html>raw-private</html>", "incomplete_response"),
            (b"data: not-json raw-private\n\n", "invalid_response"),
            (_sse({"type": "unknown"}), "invalid_response"),
            (_sse(_done_item()), "incomplete_response"),
        ]:
            with self.subTest(code=code):
                self.byte_stream = _ByteStream([content])
                self.response = httpx.Response(200, stream=self.byte_stream)
                await self.assert_error(code, self.client.complete(_PARAMS))
                self.assertTrue(self.byte_stream.closed)

    async def test_declared_non_sse_response_is_rejected_without_reading_body(self):
        for content_type in ("application/json", "text/html", ""):
            with self.subTest(content_type=content_type):
                self.byte_stream = _ByteStream([b'{"secret":"raw-private"}'])
                self.response = httpx.Response(
                    200, headers={"content-type": content_type}, stream=self.byte_stream
                )
                await self.assert_error(
                    "invalid_response", self.client.complete(_PARAMS)
                )
                self.assertFalse(self.byte_stream.entered.is_set())
                self.assertTrue(self.byte_stream.closed)

    async def test_request_event_and_total_response_limits(self):
        small_request = SubscriptionResponsesClient(
            self.auth, 42, transport=self.transport, max_request_bytes=20
        )
        await self.assert_error("request_too_large", small_request.complete(_PARAMS))
        self.assertFalse(self.requests)
        small_event = SubscriptionResponsesClient(
            self.auth, 42, transport=self.transport, max_event_bytes=20
        )
        self.byte_stream = _ByteStream([b"data: " + b"x" * 100])
        self.response = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=self.byte_stream
        )
        await self.assert_error("response_too_large", small_event.complete(_PARAMS))
        self.assertTrue(self.byte_stream.closed)
        small_total = SubscriptionResponsesClient(
            self.auth, 42, transport=self.transport, max_response_bytes=100
        )
        self.byte_stream = _ByteStream([b": comment\n\n"] * 100)
        self.response = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=self.byte_stream
        )
        await self.assert_error("response_too_large", small_total.complete(_PARAMS))
        self.assertTrue(self.byte_stream.closed)

    async def test_total_deadline_covers_opening_and_stream_read(self):
        client = SubscriptionResponsesClient(
            self.auth, 42, transport=self.transport, timeout_seconds=0.01
        )
        self.open_delay = 1
        await self.assert_error("timeout", client.complete(_PARAMS))
        self.open_delay = 0
        self.set_events(_completed([]), delay=1)
        await self.assert_error("timeout", client.complete(_PARAMS))
        self.assertTrue(self.byte_stream.closed)
        self.assertEqual(len(self.requests), 2)

    async def test_transport_failure_is_redacted_and_has_one_attempt(self):
        self.set_events(error=httpx.ReadError(f"raw-private {_TOKEN}"))
        await self.assert_error("provider_error", self.client.complete(_PARAMS))
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.byte_stream.closed)

    async def test_cancellation_closes_active_stream_without_replay(self):
        self.set_events(_completed([]), delay=10)
        task = asyncio.create_task(self.client.complete(_PARAMS))
        await asyncio.wait_for(self.byte_stream.entered.wait(), timeout=2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.byte_stream.closed)
        self.assertFalse(self.client._streams)
        self.assertEqual(len(self.requests), 1)

    async def test_client_close_cancels_inflight_auth_and_http_open(self):
        started = asyncio.Event()

        async def blocked_auth(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        self.auth.get_credentials.side_effect = blocked_auth
        task = asyncio.create_task(self.client.stream(_PARAMS))
        await asyncio.wait_for(started.wait(), timeout=2)
        await self.client.aclose()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.requests)
        self.assertFalse(self.client._opening_tasks)
        self.auth.get_credentials.side_effect = None
        another = SubscriptionResponsesClient(self.auth, 42, transport=self.transport)
        self.open_delay = 10
        task = asyncio.create_task(another.stream(_PARAMS))
        while not self.requests:
            await asyncio.sleep(0)
        await another.aclose()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(another._opening_tasks)

    async def test_stream_closed_during_read_cannot_deliver_late_events(self):
        self.set_events(_done_item(), delay=0.02)
        stream = await self.client.stream(_PARAMS)
        task = asyncio.create_task(stream.__anext__())
        await asyncio.wait_for(self.byte_stream.entered.wait(), timeout=2)
        await stream.aclose()
        with self.assertRaises(StopAsyncIteration):
            await task
        self.assertTrue(self.byte_stream.closed)

    async def test_compressed_response_rejected_before_decompression(self):
        self.response = httpx.Response(
            200,
            headers={"content-type": "text/event-stream", "content-encoding": "gzip"},
            stream=self.byte_stream,
        )
        await self.assert_error("invalid_response", self.client.complete(_PARAMS))
        self.assertEqual(self.requests[0].headers["accept-encoding"], "identity")
        self.assertFalse(self.byte_stream.entered.is_set())
        self.assertTrue(self.byte_stream.closed)

    async def test_early_consumer_break_and_client_cleanup_release_responses(self):
        self.set_events(_done_item(), _completed())
        async with await self.client.stream(_PARAMS) as stream:
            async for event in stream:
                self.assertIsInstance(event, ResponseOutputItemDoneEvent)
                break
        self.assertTrue(self.byte_stream.closed)
        self.set_events(_completed())
        stream = await self.client.stream(_PARAMS)
        await self.client.aclose()
        await self.client.aclose()
        self.assertTrue(self.byte_stream.closed)
        with self.assertRaises(StopAsyncIteration):
            await stream.__anext__()
        await self.assert_error("closed", self.client.complete(_PARAMS))


if __name__ == "__main__":
    unittest.main()
