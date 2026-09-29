"""Unit tests for the VoiceLink transport factory."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.telephony.providers.voicelink import transport as transport_module

# This directory deliberately has no ``__init__.py``: as a package it would be
# importable as ``voicelink`` and shadow the SDK the provider imports. That
# rules out sharing fixtures via relative imports, so the frame lives here too.
START_FRAME = json.dumps(
    {
        "event": "start",
        "stream_sid": "stream-1",
        "start": {
            "stream_sid": "stream-1",
            "call_sid": "call-1",
            "account_sid": "123",
            "media_format": {"encoding": "audio/alaw", "sample_rate": "8000"},
        },
    }
)


@pytest.mark.asyncio
async def test_create_transport_primes_serializer_from_start_frame():
    audio_config = SimpleNamespace(
        pipeline_sample_rate=16000,
        transport_in_sample_rate=8000,
        transport_out_sample_rate=8000,
    )
    with (
        patch.object(
            transport_module,
            "load_credentials_for_transport",
            new_callable=AsyncMock,
            return_value={"provider": "voicelink"},
        ) as load_credentials,
        patch.object(
            transport_module,
            "build_audio_out_mixer",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        transport = await transport_module.create_transport(
            MagicMock(),
            42,
            audio_config,
            9,
            telephony_configuration_id=5,
            start_message=START_FRAME,
        )

    load_credentials.assert_awaited_once_with(9, 5, expected_provider="voicelink")
    serializer = transport._params.serializer
    # Learned from the replayed start frame, before any media arrives.
    assert serializer.stream_sid == "stream-1"
    assert serializer.call_sid == "call-1"
    assert serializer.audio_spec.sample_rate == 8000
