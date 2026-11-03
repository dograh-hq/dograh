import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from api.services.integrations.paygent import client as paygent_client
from api.services.integrations.paygent.collector import PaygentCollector
from api.services.integrations.paygent.completion import _build_snapshot
from api.services.pipecat.realtime.aws_nova_sonic import DograhAWSNovaSonicLLMService

NOVA_MODEL = "amazon.nova-2-sonic-v1:0"


@pytest.fixture
def nova_collector():
    return PaygentCollector(
        workflow_run_id=1,
        is_realtime=True,
        sts_provider="aws_nova_sonic",
        sts_model=NOVA_MODEL,
    )


async def _report(collector, usage, *, processor="DograhAWSNovaSonicLLMService#0"):
    frame = MetricsFrame(
        data=[LLMUsageMetricsData(processor=processor, model=NOVA_MODEL, value=usage)]
    )
    event = FramePushed(
        source=SimpleNamespace(),
        destination=SimpleNamespace(),
        frame=frame,
        direction=FrameDirection.DOWNSTREAM,
        timestamp=0,
    )
    await collector.on_push_frame(event)
    # Observers see the same frame as it moves through the pipeline.
    await collector.on_push_frame(event)


@pytest.mark.asyncio
async def test_nova_usage_reaches_paygent_with_text_and_audio_split(
    nova_collector, monkeypatch
):
    service = DograhAWSNovaSonicLLMService(
        secret_access_key="test-secret", access_key_id="test-key", region="us-east-1"
    )

    async def report_usage(usage):
        await _report(nova_collector, usage, processor=service.name)

    monkeypatch.setattr(service, "start_llm_usage_metrics", report_usage)
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

    raw_snapshot = json.loads(json.dumps(nova_collector.build_snapshot()))
    assert raw_snapshot["llm_prompt_tokens"] == 0
    assert raw_snapshot["llm_completion_tokens"] == 0
    snapshot = _build_snapshot(raw_snapshot, workflow_run_id=1)
    post = AsyncMock()
    monkeypatch.setattr(paygent_client, "_post", post)

    result = await paygent_client.deliver(
        paygent_client.PaygentDeliveryConfig(
            api_key="test-key", agent_id="test-agent", customer_id="test-customer"
        ),
        snapshot,
    )

    assert result["status"] == "ok"
    sts_calls = [
        call for call in post.await_args_list if call.kwargs["label"] == "track_sts"
    ]
    assert len(sts_calls) == 1
    assert sts_calls[0].args[3] == {
        "sessionId": "1",
        "provider": "aws_nova_sonic",
        "model": NOVA_MODEL,
        "plan": "",
        "usageMetadata": {
            "schemaVersion": 1,
            "input": {"text": {"tokens": 500}, "audio": {"tokens": 160}},
            "output": {"text": {"tokens": 30}, "audio": {"tokens": 90}},
        },
    }
    assert "track_llm" not in result["delivered_steps"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "audio_tokens, expected",
    [
        (
            100,
            {
                "input": {"audio": {"tokens": 100}},
                "output": {"audio": {"tokens": 100}},
            },
        ),
        (
            None,
            {
                "input": {"text": {"tokens": 100}},
                "output": {"text": {"tokens": 100}},
            },
        ),
        (
            0,
            {
                "input": {"text": {"tokens": 100}},
                "output": {"text": {"tokens": 100}},
            },
        ),
    ],
    ids=["audio-only", "missing-audio-counts", "zero-audio-counts"],
)
async def test_nova_usage_with_single_modality(nova_collector, audio_tokens, expected):
    await _report(
        nova_collector,
        LLMTokenUsage(
            prompt_tokens=100,
            completion_tokens=100,
            total_tokens=200,
            input_audio_tokens=audio_tokens,
            output_audio_tokens=audio_tokens,
        ),
    )

    assert nova_collector.build_snapshot()["sts_usage_metadata"] == {
        "schemaVersion": 1,
        **expected,
    }
