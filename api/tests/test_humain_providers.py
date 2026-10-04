from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter, ValidationError

from api.services.configuration.registry import LLMConfig, STTConfig, TTSConfig
from api.services.pipecat import humain
from pipecat.frames.frames import TTSAudioRawFrame, ErrorFrame


def test_humain_configuration_discriminators():
    assert TypeAdapter(TTSConfig).validate_python({'provider': 'humain', 'api_key': 'test', 'voice': 'profile-id'}).model == 'nebula'
    assert TypeAdapter(STTConfig).validate_python({'provider': 'humain', 'api_key': 'test'}).language == 'codeswitch'
    iq = TypeAdapter(LLMConfig).validate_python({'provider': 'humain_iq', 'api_key': 'test', 'base_url': 'https://issued.example/v1', 'model': 'issued-allam-34b-id'})
    assert iq.model == 'issued-allam-34b-id'
    with pytest.raises(ValidationError):
        TypeAdapter(LLMConfig).validate_python({'provider': 'humain_iq', 'api_key': 'test'})


@pytest.mark.asyncio
async def test_voice_discovery_and_tts_use_real_profile_and_pcm(monkeypatch):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): closed.append(True)
        async def list_voices(self, **kwargs):
            assert kwargs['timeout_seconds'] == 5
            return [{'id': 'profile-id', 'label': 'Speaker', 'profile': {'speaker': {'gender': 'female'}, 'languages': ['ar', 'en']}}]
        async def synthesize_stream(self, text, **kwargs):
            assert kwargs['voice_id'] == 'profile-id'
            assert kwargs['model'] == 'nebula'
            yield SimpleNamespace(audio=b'\x00\x00' * 240, is_last=True)
    closed = []
    monkeypatch.setattr(humain, 'voice_client', lambda _: Client())
    config = TypeAdapter(TTSConfig).validate_python({'provider': 'humain', 'api_key': 'test', 'voice': 'profile-id'})
    assert (await humain.list_humain_voices(config))[0]['voice_id'] == 'profile-id'
    service = humain.HumainTTSService(config=config)
    frames = [frame async for frame in service.run_tts('Hello', 'context')]
    audio = next(frame for frame in frames if isinstance(frame, TTSAudioRawFrame))
    assert audio.sample_rate == 24000 and audio.num_channels == 1
    assert len(closed) == 2


@pytest.mark.asyncio
async def test_stt_resamples_telephone_pcm_before_sending():
    config = TypeAdapter(STTConfig).validate_python({'provider': 'humain', 'api_key': 'test'})
    service = humain.HumainSTTService(config=config)
    service._input_rate = 8000
    service._stream = SimpleNamespace(send=AsyncMock())
    service._resampler = SimpleNamespace(resample=AsyncMock(return_value=b'converted'))
    frames = [frame async for frame in service.run_stt(b'pcm')]
    service._resampler.resample.assert_awaited_once_with(b'pcm', 8000, 16000)
    service._stream.send.assert_awaited_once_with(b'converted')
    assert frames == [None]


@pytest.mark.asyncio
async def test_stt_missing_connection_reports_failure():
    config = TypeAdapter(STTConfig).validate_python({'provider': 'humain', 'api_key': 'test'})
    frames = [frame async for frame in humain.HumainSTTService(config=config).run_stt(b'pcm')]
    assert isinstance(frames[0], ErrorFrame)


def test_factory_passes_iq_endpoint_and_model_without_openai_fallback(monkeypatch):
    from fastapi import HTTPException
    from api.services.pipecat import service_factory
    from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
    captured = {}
    def llm(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)
    monkeypatch.setattr(service_factory, 'OpenAILLMService', llm)
    iq = TypeAdapter(LLMConfig).validate_python({'provider': 'humain_iq', 'api_key': 'test', 'base_url': 'https://issued.example/v1', 'model': 'issued-allam-34b-id'})
    service_factory.create_llm_service(EffectiveAIModelConfiguration(llm=iq))
    assert captured['base_url'] == 'https://issued.example/v1'
    assert captured['settings'].model == 'issued-allam-34b-id'
    with pytest.raises(HTTPException):
        service_factory.create_llm_service_from_provider('humain_iq', 'issued-id', 'test')


def test_pipeline_factories_select_humain_voice_services():
    from api.services.pipecat.service_factory import create_stt_service, create_tts_service
    from api.services.pipecat.audio_config import AudioConfig
    config = SimpleNamespace(
        stt=TypeAdapter(STTConfig).validate_python({'provider': 'humain', 'api_key': 'test'}),
        tts=TypeAdapter(TTSConfig).validate_python({'provider': 'humain', 'api_key': 'test', 'voice': 'profile-id'}),
    )
    audio = AudioConfig(transport_in_sample_rate=16000, transport_out_sample_rate=16000)
    assert isinstance(create_stt_service(config, audio), humain.HumainSTTService)
    assert isinstance(create_tts_service(config, audio), humain.HumainTTSService)


@pytest.mark.asyncio
async def test_voice_catalog_uses_current_organization_credentials(monkeypatch):
    from api.routes import user as route
    config = TypeAdapter(TTSConfig).validate_python({'provider': 'humain', 'api_key': 'tenant-key', 'voice': 'profile-id'})
    resolver = AsyncMock(return_value=SimpleNamespace(effective=SimpleNamespace(tts=config)))
    catalog = AsyncMock(return_value=[{'voice_id': 'profile-id', 'name': 'Speaker'}])
    monkeypatch.setattr(route, 'get_resolved_ai_model_configuration', resolver)
    monkeypatch.setattr(humain, 'list_humain_voices', catalog)
    response = await route.get_voices('humain', user=SimpleNamespace(selected_organization_id=17))
    resolver.assert_awaited_once_with(organization_id=17)
    catalog.assert_awaited_once_with(config)
    assert response.voices[0].voice_id == 'profile-id'
