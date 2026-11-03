"""Tests for RecordingRouterProcessor mixed-marker handling.

When the LLM generates a response containing both a TTS marker (▸) and a
recording marker (●), only the *first* marker should be honoured. Everything
from the second marker onward must be silently dropped so it never reaches
downstream TTS or triggers a second recording playback.

Uses pipecat's ``run_test`` helper to send frames through a real pipeline
and inspect what arrives downstream.
"""

from typing import Optional
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import (
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.recording_audio_cache import RecordingAudio
from api.services.pipecat.recording_router_processor import (
    RecordingRouterProcessor,
)
from api.services.workflow.pipecat_engine_context_composer import (
    RECORDING_MARKER,
    TTS_MARKER,
)
from pipecat.tests import run_test

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_AUDIO = b"\x00\x01" * 8000  # 1 second of 16-bit mono @ 16 kHz


async def _fake_fetch(recording_id: str) -> Optional[RecordingAudio]:
    """Stub that returns fake PCM audio for any recording_id."""
    return RecordingAudio(audio=FAKE_AUDIO)


def _make_processor(**kwargs) -> RecordingRouterProcessor:
    enabled = kwargs.pop("enabled", True)
    processor = RecordingRouterProcessor(
        audio_sample_rate=16_000,
        fetch_recording_audio=kwargs.pop("fetch", _fake_fetch),
        **kwargs,
    )
    processor.set_enabled(enabled)
    return processor


def _llm_tokens(tokens: list[str]) -> list[LLMTextFrame]:
    """Build a list of LLMTextFrame from raw strings."""
    return [LLMTextFrame(text=t) for t in tokens]


@pytest.mark.asyncio
async def test_disabled_router_streams_text_before_response_end():
    fetch = AsyncMock()
    processor = RecordingRouterProcessor(
        audio_sample_rate=16_000, fetch_recording_audio=fetch
    )
    processor.push_frame = AsyncMock()
    direction = FrameDirection.DOWNSTREAM
    await processor.process_frame(LLMFullResponseStartFrame(), direction)

    for text in ("Hello", " world", " ● leave this unchanged"):
        frame = LLMTextFrame(text)
        await processor.process_frame(frame, direction)
        processor.push_frame.assert_called_with(frame, direction)
        assert frame.text == text
        assert not frame.skip_tts

    fetch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", [LLMFullResponseEndFrame, InterruptionFrame])
async def test_node_change_applies_to_next_response(boundary):
    fetch = AsyncMock(return_value=RecordingAudio(audio=FAKE_AUDIO))
    processor = _make_processor(fetch=fetch, enable_direct_mode=True)
    processor.push_frame = AsyncMock()
    direction = FrameDirection.DOWNSTREAM

    await processor.process_frame(LLMFullResponseStartFrame(), direction)
    await processor.process_frame(LLMTextFrame(RECORDING_MARKER), direction)
    # Preparing the next node must not change the current generation's mode.
    processor.set_enabled(False)
    await processor.process_frame(LLMTextFrame(" rec123 transcript"), direction)
    fetch.assert_awaited_once_with(recording_id="rec123")
    await processor.process_frame(boundary(), direction)

    await processor.process_frame(LLMFullResponseStartFrame(), direction)
    frame = LLMTextFrame("The next node streams immediately.")
    await processor.process_frame(frame, direction)
    processor.push_frame.assert_called_with(frame, direction)


# ---------------------------------------------------------------------------
# Tests — single marker (baseline sanity)
# ---------------------------------------------------------------------------


class TestSingleMarker:
    """Verify basic TTS-only and recording-only paths still work."""

    @pytest.mark.asyncio
    async def test_tts_only(self):
        """▸ Hello — text should flow downstream as LLMTextFrames."""
        processor = _make_processor()

        frames_to_send = _llm_tokens(
            [
                TTS_MARKER,
                " Hello, how are you today?",
            ]
        ) + [LLMFullResponseEndFrame()]

        down, _ = await run_test(
            processor,
            frames_to_send=frames_to_send,
            expected_down_frames=None,  # don't assert types, inspect manually
        )

        tts_text = "".join(
            f.text for f in down if isinstance(f, LLMTextFrame) and not f.skip_tts
        )
        assert "Hello, how are you today?" in tts_text

        # No audio playback
        assert not any(isinstance(f, TTSAudioRawFrame) for f in down)

    @pytest.mark.asyncio
    async def test_recording_only(self):
        """● rec_id [transcript] — should play audio and push TTSTextFrame
        context."""
        processor = _make_processor()

        frames_to_send = _llm_tokens(
            [
                RECORDING_MARKER,
                " abc123",
                " [ This is the transcript. ]",
            ]
        ) + [LLMFullResponseEndFrame()]

        down, _ = await run_test(
            processor,
            frames_to_send=frames_to_send,
            expected_down_frames=None,
        )

        # Audio playback frames should be present
        assert any(isinstance(f, TTSStartedFrame) for f in down)
        assert any(isinstance(f, TTSAudioRawFrame) for f in down)
        assert any(isinstance(f, TTSStoppedFrame) for f in down)

        # Context TTSTextFrame with transcript
        ctx_frames = [f for f in down if isinstance(f, TTSTextFrame)]
        assert len(ctx_frames) == 1
        assert "abc123" in ctx_frames[0].text


# ---------------------------------------------------------------------------
# Tests — mixed markers (the bug)
# ---------------------------------------------------------------------------


class TestMixedMarkerSuppression:
    """The LLM sometimes generates both markers in one response.

    Only the first marker should be honoured; the second marker and
    everything after it must be dropped.
    """

    @pytest.mark.asyncio
    async def test_tts_then_recording_marker_ignores_recording(self):
        """▸ text... ● rec_id [transcript]

        Expected: only the TTS text reaches downstream; the recording
        marker, recording_id, and bracketed transcript are all suppressed.
        No audio playback frames should appear.
        """
        processor = _make_processor()

        frames_to_send = _llm_tokens(
            [
                TTS_MARKER,
                " Okay, so this is regarding government changes.",
                "\n",
                RECORDING_MARKER,
                " fetafnqb",
                " [ Okay, so it's Nancy here. ]",
            ]
        ) + [LLMFullResponseEndFrame()]

        down, _ = await run_test(
            processor,
            frames_to_send=frames_to_send,
            expected_down_frames=None,
        )

        # Collect all LLMTextFrame text that was NOT marked skip_tts
        tts_text = "".join(
            f.text for f in down if isinstance(f, LLMTextFrame) and not f.skip_tts
        )

        # The TTS text should contain the first sentence
        assert "government changes" in tts_text

        # Nothing from the recording section should leak into TTS
        assert RECORDING_MARKER not in tts_text
        assert "fetafnqb" not in tts_text
        assert "Nancy" not in tts_text

        # No audio playback frames
        assert not any(isinstance(f, TTSStartedFrame) for f in down)
        assert not any(isinstance(f, TTSAudioRawFrame) for f in down)
        assert not any(isinstance(f, TTSStoppedFrame) for f in down)

    @pytest.mark.asyncio
    async def test_recording_then_tts_marker_ignores_tts(self):
        """● rec_id [transcript] ▸ text...

        Expected: recording plays; the TTS marker and following text are
        suppressed — they must not appear in any downstream frame, including
        the TTSTextFrame context pushed at response end.
        """
        fetched_ids: list[str] = []

        async def tracking_fetch(recording_id: str):
            fetched_ids.append(recording_id)
            return RecordingAudio(audio=FAKE_AUDIO)

        processor = _make_processor(fetch=tracking_fetch)

        frames_to_send = _llm_tokens(
            [
                RECORDING_MARKER,
                " fetafnqb",
                " [ Okay, so it's Nancy here. ]",
                "\n",
                TTS_MARKER,
                " And this is the fallback TTS text.",
            ]
        ) + [LLMFullResponseEndFrame()]

        down, _ = await run_test(
            processor,
            frames_to_send=frames_to_send,
            expected_down_frames=None,
        )

        # Recording playback should have occurred
        assert any(isinstance(f, TTSAudioRawFrame) for f in down)

        # Only the correct recording_id should have been fetched
        assert fetched_ids == ["fetafnqb"]

        # The TTS text after the ▸ marker must NOT appear in any downstream frame
        all_text = "".join(
            f.text for f in down if isinstance(f, LLMTextFrame) and not f.skip_tts
        )
        assert "fallback TTS text" not in all_text

        # The TTSTextFrame context pushed at response end should only contain
        # the recording marker + recording_id + transcript, not the TTS part
        ctx_frames = [f for f in down if isinstance(f, TTSTextFrame)]
        assert len(ctx_frames) == 1
        ctx_text = ctx_frames[0].text
        assert "fetafnqb" in ctx_text
        assert TTS_MARKER not in ctx_text
        assert "fallback TTS text" not in ctx_text
