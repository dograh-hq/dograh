"""Refresh the offline visualization's source excerpts from the local checkout."""

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = "api/services/pipecat/"
PIPECAT = "pipecat/src/pipecat/"
EXCERPTS = {
    "standard_builder": (API + "pipeline_builder.py", 32, 115, "Standard pipeline", "Ordered processors in build_pipeline()."),
    "realtime_builder": (API + "pipeline_builder.py", 118, 179, "Realtime pipeline", "Ordered processors in build_realtime_pipeline(). Runtime disables its optional voicemail detector."),
    "services": (API + "run_pipeline.py", 684, 712, "Create services", "Branch on is_realtime; create either a realtime service or STT + TTS + text LLM."),
    "components": (API + "pipeline_builder.py", 15, 29, "Shared components", "AudioBufferProcessor and LLMContext are created together."),
    "construction": (API + "run_pipeline.py", 898, 960, "Create aggregators", "Configure analyzer, turn strategies, and aggregator parameters."),
    "realtime_config": (API + "run_pipeline.py", 204, 264, "Provider turn policy", "Select local VAD or external strategies and who owns interruption."),
    "standard_config": (API + "run_pipeline.py", 154, 201, "Standard turn policy", "Default, minimum-word, provisional VAD, external STT, and stop strategies."),
    "external_stt": (API + "service_factory.py", 222, 229, "STT-owned turns", "STT providers/models selecting external turn boundaries."),
    "aggregator": (PIPECAT + "processors/aggregators/llm_response_universal.py", 751, 764, "VAD ownership", "The user aggregator constructs VADController and registers its callbacks."),
    "aggregator_audio": (PIPECAT + "processors/aggregators/llm_response_universal.py", 857, 876, "Forward, then analyze", "Ordinary audio is forwarded before VAD and turn-controller processing."),
    "transport": (PIPECAT + "transports/base_input.py", 269, 299, "Transport input", "Audio queue, optional filter, and downstream passthrough."),
    "stt": (PIPECAT + "services/stt_service.py", 464, 486, "STT passthrough", "STT processes audio and forwards raw frames; upstream VAD frames also reach STT."),
    "vad": (PIPECAT + "audio/vad/vad_controller.py", 170, 200, "VAD callback", "Analyze the PCM bytes and invoke on_speech_started / on_speech_stopped."),
    "analyzer": (PIPECAT + "audio/vad/vad_analyzer.py", 179, 239, "Analyzer windows", "Run analysis in an executor, buffer PCM, and apply confidence, volume, and state thresholds."),
    "vad_broadcast": (PIPECAT + "processors/aggregators/llm_response_universal.py", 1270, 1306, "Queue VAD frames", "Queue a downstream instance on self and push another upstream."),
    "vad_strategy": (PIPECAT + "turns/user_start/vad_user_turn_start_strategy.py", 14, 35, "VAD start strategy", "A VADUserStartedSpeakingFrame triggers user-turn start."),
    "turn_start": (PIPECAT + "processors/aggregators/llm_response_universal.py", 1308, 1327, "Accept user turn", "Broadcast accepted speech state and optional interruption, then invoke the turn-start event."),
    "provider_events": (PIPECAT + "services/openai/realtime/llm.py", 1142, 1155, "OpenAI speech events", "Provider speech events become proposed user-turn frames. Azure uses the OpenAI-compatible path."),
    "grok_events": (PIPECAT + "services/xai/realtime/llm.py", 1041, 1061, "Grok speech events", "Provider speech events become proposed user-turn frames."),
    "realtime_audio": (PIPECAT + "services/openai/realtime/llm.py", 657, 696, "Realtime audio input", "OpenAI example: send audio to the provider and forward the input frame."),
    "external_strategy": (PIPECAT + "turns/user_start/external_user_turn_start_strategy.py", 71, 108, "Resolve a proposal", "External start strategy accepts proposals; already accepted turns are adopted without rebroadcast."),
    "external_stop": (PIPECAT + "turns/user_stop/external_user_turn_stop_strategy.py", 20, 130, "External stop strategy", "Follow proposed or accepted provider stops, with configurable transcript waiting."),
    "stop_strategy": (PIPECAT + "turns/user_stop/speech_timeout_user_turn_stop_strategy.py", 29, 99, "Speech-timeout stop", "User speech timeout and STT latency/finalization are distinct conditions."),
    "inference": (PIPECAT + "processors/aggregators/llm_response_universal.py", 1329, 1390, "Inference and turn stop", "Standard mode pushes context at inference; realtime mode defers local context bookkeeping."),
    "frame_routing": (PIPECAT + "processors/frame_processor.py", 1162, 1196, "Frame routing", "push_frame routes through next.queue_frame or previous.queue_frame."),
    "broadcast": (PIPECAT + "processors/frame_processor.py", 1040, 1056, "Broadcast instances", "A normal broadcast creates separate downstream and upstream frames."),
    "optional": (API + "run_pipeline.py", 1002, 1060, "Optional processors", "Runtime voicemail gating and standard-mode recording router creation."),
    "worker": (API + "pipeline_builder.py", 207, 245, "Create the worker", "PipelineWorker configures audio rates, observers/tracing, and runtime behavior."),
}


def revision(path: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "--short", "HEAD"], text=True
    ).strip()


def main() -> None:
    data = {"dograh": revision(ROOT), "pipecat": revision(ROOT / "pipecat"), "excerpts": {}}
    for key, (path, start, end, title, description) in EXCERPTS.items():
        lines = (ROOT / path).read_text().splitlines()
        data["excerpts"][key] = {
            "path": path,
            "start": start,
            "title": title,
            "description": description,
            "code": "\n".join(lines[start - 1 : end]),
        }
    target = Path(__file__).with_name("index.html")
    # Escape '<' so source comments cannot close the embedded JSON script tag.
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    content, count = re.subn(
        r'(<script id="source-data" type="application/json">).*?(</script>)',
        lambda match: match[1] + payload + match[2],
        target.read_text(),
        flags=re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("Expected one embedded source-data block")
    target.write_text(content)
    print(f"Embedded {len(EXCERPTS)} excerpts in {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
