"""Voice session builder — realtime or STT+LLM+TTS fallback."""

import logging
from livekit import agents, rtc
from livekit.agents import AgentSession, room_io

logger = logging.getLogger(__name__)

REALTIME_LLM_PROVIDERS = {"google_realtime", "openai_realtime", "aws_realtime"}


def _room_options_for_channel(channel: str) -> room_io.RoomOptions:
    audio_output = room_io.AudioOutputOptions(
        track_publish_options=rtc.TrackPublishOptions(
            source=rtc.TrackSource.SOURCE_MICROPHONE, red=False,
        ),
    )
    return room_io.RoomOptions(audio_input=True, audio_output=audio_output)


async def voice_session(ctx: agents.JobContext, config: dict) -> AgentSession:
    """Build and start a voice AgentSession."""
    from app.tools.dispatcher import LuminaAgent

    llm_cfg = config.get("llm_config", {})
    llm_provider = llm_cfg.get("provider", "")
    llm_model = llm_cfg.get("model", "")

    if config.get("orchestrator_mode") == "agentos":
        from app.translator.workflow import translate_workflow
        workflow_graph_data = config.get("workflow_graph")
        if workflow_graph_data:
            from app.models import WorkflowGraph
            graph = WorkflowGraph(**workflow_graph_data) if isinstance(workflow_graph_data, dict) else workflow_graph_data
            agno_wf = translate_workflow(graph, config)
            agent = agno_wf
        else:
            agent = LuminaAgent(config=config)
    else:
        agent = LuminaAgent(config=config)

    is_realtime = llm_provider in REALTIME_LLM_PROVIDERS

    if is_realtime:
        session = await _build_realtime_session(ctx, agent, llm_provider, llm_model, config)
    else:
        session = await _build_fallback_session(ctx, agent, config)

    greeting = config.get("greeting_message", "").strip()
    if greeting and is_realtime:
        await session.generate_reply(
            instructions=f"Di' esattamente questa frase: \"{greeting}\""
        )

    return session


async def _build_realtime_session(
    ctx: agents.JobContext, agent, llm_provider: str, llm_model: str, config: dict
) -> AgentSession:
    llm_voice = config.get("tts_config", {}).get("voice_id", "Kore")
    llm_temperature = float(config.get("llm_config", {}).get("temperature", 0.8))
    room_options = _room_options_for_channel(config.get("channel", ""))

    if llm_provider == "google_realtime":
        from livekit.plugins import google
        instructions = config.get("system_prompt", "")
        llm_max_tokens = config.get("llm_config", {}).get("max_output_tokens") or None
        kwargs = dict(model=llm_model, temperature=llm_temperature, instructions=instructions, voice=llm_voice)
        if llm_max_tokens:
            kwargs["max_output_tokens"] = llm_max_tokens
        session = AgentSession(llm=google.realtime.RealtimeModel(**kwargs))
        await session.start(room=ctx.room, agent=agent, room_options=room_options)
    elif llm_provider == "openai_realtime":
        from livekit.plugins import openai
        session = AgentSession(
            llm=openai.realtime.RealtimeModel(
                model=llm_model or "gpt-4o-realtime-preview",
                temperature=llm_temperature,
                modalities=["audio"],
                voice=llm_voice,
            )
        )
        await session.start(room=ctx.room, agent=agent, room_options=room_options)
    else:
        raise ValueError(f"Unknown realtime provider: {llm_provider}")

    return session


async def _build_fallback_session(
    ctx: agents.JobContext, agent, config: dict
) -> AgentSession:
    from livekit.plugins import deepgram, google, silero, cartesia
    from livekit.agents import TurnHandlingOptions

    config = config or {}
    channel = config.get("channel", "")

    stt_provider = config.get("stt_config", {}).get("provider", "deepgram")
    stt_model = config.get("stt_config", {}).get("model_id", "nova-3")
    tts_provider = config.get("tts_config", {}).get("provider", "cartesia")
    tts_voice = config.get("tts_config", {}).get("voice_id", "Kore")

    if stt_provider == "google":
        stt = google.STT(model="latest_long", languages="it-IT")
    else:
        stt_model_id = stt_model.split("/")[-1] if stt_model else "nova-3"
        stt = deepgram.STT(model=stt_model_id, language="it")

    tts_model = config.get("tts_config", {}).get("model_id", "sonic-3")
    if tts_provider == "google_tts":
        tts = google.TTS(model_name="gemini-2.5-flash-preview-tts", voice_name=tts_voice or "Kore", language="it")
    elif tts_provider == "deepgram":
        tts = deepgram.TTS(model="aura-2", voice=tts_voice or "")
    else:
        model_id = tts_model.split("/")[-1] if tts_model else "sonic-3"
        tts = cartesia.TTS(model=model_id, voice=tts_voice or "")

    llm_model = config.get("llm_config", {}).get("model", "gemini-2.5-flash")
    llm = google.LLM(model=llm_model)

    session = AgentSession(
        stt=stt, llm=llm, tts=tts,
        vad=silero.VAD.load(),
        turn_handling=TurnHandlingOptions(turn_detection="stt"),
        preemptive_generation=True,
    )
    await session.start(room=ctx.room, agent=agent, room_options=_room_options_for_channel(channel))
    return session
