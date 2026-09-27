import json

import pytest

from pipecat.services.openai.realtime import events
from pipecat.services.openai.realtime.events import ConversationItemAdded, SessionCreatedEvent
from pipecat.services.yandex.realtime.llm import (
    _RateNormalizingWebSocket,
    _normalize_event_type,
    _normalize_pcm_rate,
)

# Reproduces the conversation.item.created payload Yandex Realtime sends when
# announcing a new conversation item; pipecat only recognizes the GA rename
# "conversation.item.added" for the same payload shape.
YANDEX_CONVERSATION_ITEM_CREATED = json.dumps(
    {
        "type": "conversation.item.created",
        "event_id": "3052de6f-e175-420a-b8db-fb2efea878b8",
        "previous_item_id": None,
        "item": {
            "type": "message",
            "id": "27c0844b-425e-4fd3-9fc9-cd58f4edfcc4",
            "object": "realtime.item",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        },
    }
)

# Reproduces the exact session.created payload Yandex Realtime sends, which
# omits the PCM sample rate (`rate: null`) instead of echoing OpenAI's fixed
# 24000 Hz, breaking pipecat's `PCMAudioFormat.rate: Literal[24000]` schema.
YANDEX_SESSION_CREATED = json.dumps(
    {
        "type": "session.created",
        "event_id": "e5d32323-cddb-4b32-aa45-d3143b6fa76b",
        "session": {
            "id": "27a51a0fa793",
            "object": "realtime.session",
            "expires_at": None,
            "type": "realtime",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": None},
                    "turn_detection": {
                        "type": "server_vad",
                        "silence_duration_ms": 800,
                        "threshold": None,
                        "idle_timeout_ms": None,
                        "yc_idle_llm_message": None,
                    },
                    "languages": None,
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": None},
                    "speed": None,
                    "voice": "kirill",
                    "role": None,
                },
            },
            "instructions": None,
            "max_output_tokens": "inf",
            "output_modalities": ["text", "audio"],
            "prompt": None,
            "reasoning": None,
            "tools": [],
        },
    }
)


def test_normalize_pcm_rate_fills_in_null_rate_for_both_directions():
    patched = json.loads(_normalize_pcm_rate(YANDEX_SESSION_CREATED))

    assert patched["session"]["audio"]["input"]["format"]["rate"] == 24000
    assert patched["session"]["audio"]["output"]["format"]["rate"] == 24000
    # Untouched fields survive the round trip.
    assert patched["session"]["audio"]["output"]["voice"] == "kirill"


def test_normalize_pcm_rate_leaves_other_messages_untouched():
    message = json.dumps({"type": "response.done", "response": {"id": "r1"}})

    assert _normalize_pcm_rate(message) == message


def test_yandex_session_created_fails_to_parse_without_the_patch():
    with pytest.raises(Exception):
        events.parse_server_event(YANDEX_SESSION_CREATED)


def test_yandex_session_created_parses_after_the_patch():
    evt = events.parse_server_event(_normalize_pcm_rate(YANDEX_SESSION_CREATED))

    assert isinstance(evt, SessionCreatedEvent)
    assert evt.session.audio.input.format.rate == 24000
    assert evt.session.audio.output.format.rate == 24000


def test_normalize_event_type_renames_conversation_item_created():
    patched = json.loads(_normalize_event_type(YANDEX_CONVERSATION_ITEM_CREATED))

    assert patched["type"] == "conversation.item.added"
    # Untouched fields survive the round trip.
    assert patched["item"]["id"] == "27c0844b-425e-4fd3-9fc9-cd58f4edfcc4"


def test_normalize_event_type_leaves_other_messages_untouched():
    message = json.dumps({"type": "response.done", "response": {"id": "r1"}})

    assert _normalize_event_type(message) == message


def test_yandex_conversation_item_created_fails_to_parse_without_the_patch():
    with pytest.raises(Exception):
        events.parse_server_event(YANDEX_CONVERSATION_ITEM_CREATED)


def test_yandex_conversation_item_created_parses_after_the_patch():
    evt = events.parse_server_event(_normalize_event_type(YANDEX_CONVERSATION_ITEM_CREATED))

    assert isinstance(evt, ConversationItemAdded)
    assert evt.item.id == "27c0844b-425e-4fd3-9fc9-cd58f4edfcc4"


class _FakeConnection:
    def __init__(self, messages):
        self._messages = messages
        self.sent = []
        self.closed = False

    async def __aiter__(self):
        for message in self._messages:
            yield message

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_rate_normalizing_websocket_patches_iterated_messages():
    fake = _FakeConnection([YANDEX_SESSION_CREATED])
    wrapped = _RateNormalizingWebSocket(fake)

    messages = [message async for message in wrapped]

    assert json.loads(messages[0])["session"]["audio"]["input"]["format"]["rate"] == 24000


@pytest.mark.asyncio
async def test_rate_normalizing_websocket_renames_conversation_item_created():
    fake = _FakeConnection([YANDEX_CONVERSATION_ITEM_CREATED])
    wrapped = _RateNormalizingWebSocket(fake)

    messages = [message async for message in wrapped]

    assert json.loads(messages[0])["type"] == "conversation.item.added"


@pytest.mark.asyncio
async def test_rate_normalizing_websocket_forwards_send_and_close():
    fake = _FakeConnection([])
    wrapped = _RateNormalizingWebSocket(fake)

    await wrapped.send("outgoing")
    await wrapped.close()

    assert fake.sent == ["outgoing"]
    assert fake.closed is True
