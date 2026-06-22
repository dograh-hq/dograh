"""create_audio_config: realtime models must run the pipeline at >=16 kHz.

Regression test for the one-way/garbled-inbound bug on telephony: an 8 kHz
VoiceLink pipeline fed Gemini Live half-rate audio, so the caller's speech was
barely recognized. Realtime models must get 16 kHz; the serializer resamples
the 8 kHz wire.
"""

# Importing the provider package registers its ProviderSpec (transport_sample_rate=8000).
import api.services.telephony.providers.voicelink  # noqa: F401
from api.enums import WorkflowRunMode
from api.services.pipecat.audio_config import create_audio_config


def test_voicelink_cascaded_keeps_8k_wire_rate():
    cfg = create_audio_config("voicelink", is_realtime=False)
    assert cfg.pipeline_sample_rate == 8000
    assert cfg.transport_in_sample_rate == 8000
    assert cfg.transport_out_sample_rate == 8000


def test_voicelink_realtime_runs_pipeline_at_16k():
    cfg = create_audio_config("voicelink", is_realtime=True)
    assert cfg.pipeline_sample_rate == 16000
    assert cfg.transport_in_sample_rate == 16000
    assert cfg.transport_out_sample_rate == 16000
    assert cfg.vad_sample_rate == 16000


def test_webrtc_is_16k_regardless():
    cfg = create_audio_config(WorkflowRunMode.SMALLWEBRTC.value, is_realtime=False)
    assert cfg.pipeline_sample_rate == 16000
