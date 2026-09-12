import json
from unittest.mock import AsyncMock

import pytest
from google.genai.types import LiveServerMessage, UsageMetadata
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
from pipecat.processors.frame_processor import FrameDirection

from api.services.pipecat.pipeline_metrics_aggregator import PipelineMetricsAggregator
from api.services.pipecat.realtime.aws_nova_sonic import DograhAWSNovaSonicLLMService
from api.services.pipecat.realtime.gemini_live import DograhGeminiLiveLLMService
from api.services.workflow.run_usage_response import format_public_usage_info

PROCESSOR = "DograhGeminiLiveLLMService#0"
MODEL = "gemini-3.1-flash-live-preview"
USAGE_KEY = f"{PROCESSOR}|||{MODEL}"
OPTIONAL_TOKEN_FIELDS = (
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "reasoning_tokens",
    "input_audio_tokens",
    "output_audio_tokens",
    "cache_read_input_audio_tokens",
)


@pytest.fixture
def aggregator(monkeypatch):
    instance = PipelineMetricsAggregator()
    monkeypatch.setattr(instance, "push_frame", AsyncMock())
    return instance


async def _report(aggregator, usage, *, processor=PROCESSOR, model=MODEL):
    frame = MetricsFrame(
        data=[LLMUsageMetricsData(processor=processor, model=model, value=usage)]
    )
    await aggregator.process_frame(frame, FrameDirection.DOWNSTREAM)
    aggregator.push_frame.assert_awaited_with(frame, FrameDirection.DOWNSTREAM)


@pytest.mark.asyncio
async def test_gemini_modality_counts_survive_aggregation_and_public_serialization(
    aggregator, monkeypatch
):
    service = DograhGeminiLiveLLMService(api_key="test-key")
    emit_usage = AsyncMock()
    monkeypatch.setattr(service, "start_llm_usage_metrics", emit_usage)
    message = LiveServerMessage(
        usage_metadata=UsageMetadata(
            prompt_token_count=1000,
            response_token_count=200,
            total_token_count=1220,
            cached_content_token_count=100,
            thoughts_token_count=20,
            prompt_tokens_details=[
                {"modality": "TEXT", "token_count": 400},
                {"modality": "AUDIO", "token_count": 600},
            ],
            response_tokens_details=[
                {"modality": "TEXT", "token_count": 50},
                {"modality": "AUDIO", "token_count": 150},
            ],
            cache_tokens_details=[
                {"modality": "TEXT", "token_count": 60},
                {"modality": "AUDIO", "token_count": 40},
            ],
        )
    )

    await service._handle_msg_usage_metadata(message)
    emit_usage.assert_awaited_once()
    emitted = emit_usage.await_args.args[0]
    await _report(aggregator, emitted)

    stored_usage = json.loads(json.dumps(aggregator.get_all_usage_metrics_serialized()))
    public_usage = format_public_usage_info(stored_usage)
    assert public_usage["llm"][USAGE_KEY] == {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "total_tokens": 1220,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": None,
        "reasoning_tokens": 20,
        "input_audio_tokens": 600,
        "output_audio_tokens": 150,
        "cache_read_input_audio_tokens": 40,
    }

    # Aggregation must own its snapshot if the emitting service reuses its object.
    emitted.input_audio_tokens = 9999
    assert aggregator.get_llm_usage_metrics()[USAGE_KEY].input_audio_tokens == 600


@pytest.mark.asyncio
async def test_nova_audio_counts_survive_aggregation_and_public_serialization(
    aggregator, monkeypatch
):
    service = DograhAWSNovaSonicLLMService(
        secret_access_key="test-secret", access_key_id="test-key", region="us-east-1"
    )
    emit_usage = AsyncMock()
    monkeypatch.setattr(service, "start_llm_usage_metrics", emit_usage)
    processor = "DograhAWSNovaSonicLLMService#0"
    model = "amazon.nova-2-sonic-v1:0"
    for delta, total in (
        (
            {
                "input": {"speechTokens": 100, "textTokens": 200},
                "output": {"speechTokens": 50, "textTokens": 10},
            },
            {
                "input": {"speechTokens": 100, "textTokens": 200},
                "output": {"speechTokens": 50, "textTokens": 10},
            },
        ),
        (
            {
                "input": {"speechTokens": 60, "textTokens": 300},
                "output": {"speechTokens": 40, "textTokens": 20},
            },
            {
                "input": {"speechTokens": 160, "textTokens": 500},
                "output": {"speechTokens": 90, "textTokens": 30},
            },
        ),
    ):
        await service._handle_usage_event(
            {"usageEvent": {"details": {"delta": delta, "total": total}}}
        )
        await _report(
            aggregator,
            emit_usage.await_args.args[0],
            processor=processor,
            model=model,
        )

    assert emit_usage.await_count == 2
    stored_usage = json.loads(json.dumps(aggregator.get_all_usage_metrics_serialized()))
    public_usage = format_public_usage_info(stored_usage)
    assert public_usage["llm"][f"{processor}|||{model}"] == {
        "prompt_tokens": 660,
        "completion_tokens": 120,
        "total_tokens": 780,
        "cache_read_input_tokens": None,
        "cache_creation_input_tokens": None,
        "reasoning_tokens": None,
        "input_audio_tokens": 160,
        "output_audio_tokens": 90,
        "cache_read_input_audio_tokens": None,
    }


@pytest.mark.asyncio
async def test_sums_all_token_counts_across_turns(aggregator):
    await _report(
        aggregator,
        LLMTokenUsage(
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1220,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=10,
            reasoning_tokens=20,
            input_audio_tokens=600,
            output_audio_tokens=150,
            cache_read_input_audio_tokens=40,
        ),
    )
    await _report(
        aggregator,
        LLMTokenUsage(
            prompt_tokens=1500,
            completion_tokens=300,
            total_tokens=1830,
            cache_read_input_tokens=200,
            cache_creation_input_tokens=30,
            reasoning_tokens=30,
            input_audio_tokens=900,
            output_audio_tokens=250,
            cache_read_input_audio_tokens=70,
        ),
    )

    assert aggregator.get_all_usage_metrics_serialized()["llm"][USAGE_KEY] == {
        "prompt_tokens": 2500,
        "completion_tokens": 500,
        "total_tokens": 3050,
        "cache_read_input_tokens": 300,
        "cache_creation_input_tokens": 40,
        "reasoning_tokens": 50,
        "input_audio_tokens": 1500,
        "output_audio_tokens": 400,
        "cache_read_input_audio_tokens": 110,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ([None], None),
        ([None, None], None),
        ([0], 0),
        ([0, 0], 0),
        ([None, 0], 0),
        ([0, None], 0),
        ([None, 7, None, 3], 10),
        ([7, None], 7),
    ],
)
async def test_distinguishes_unreported_counts_from_zero(aggregator, counts, expected):
    for count in counts:
        await _report(
            aggregator,
            LLMTokenUsage(
                prompt_tokens=100,
                completion_tokens=20,
                total_tokens=120,
                **dict.fromkeys(OPTIONAL_TOKEN_FIELDS, count),
            ),
        )

    serialized = aggregator.get_all_usage_metrics_serialized()["llm"][USAGE_KEY]
    for field in OPTIONAL_TOKEN_FIELDS:
        assert serialized[field] == expected


@pytest.mark.asyncio
async def test_keeps_processor_and_model_totals_separate_and_resets(aggregator):
    for processor, model, count in (
        (PROCESSOR, MODEL, 10),
        (PROCESSOR, "another-model", 20),
        ("another-processor", MODEL, 30),
    ):
        await _report(
            aggregator,
            LLMTokenUsage(
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                input_audio_tokens=count,
            ),
            processor=processor,
            model=model,
        )

    serialized = aggregator.get_all_usage_metrics_serialized()["llm"]
    assert serialized[USAGE_KEY]["input_audio_tokens"] == 10
    assert serialized[f"{PROCESSOR}|||another-model"]["input_audio_tokens"] == 20
    assert serialized[f"another-processor|||{MODEL}"]["input_audio_tokens"] == 30

    aggregator.reset_metrics()
    assert aggregator.get_all_usage_metrics_serialized()["llm"] == {}
