"""Dograh-specific subclasses of pipecat realtime LLM services.

RealtimeConversationMixin owns the opening TTSSpeakFrame trigger and replaces
muted caller audio with silence. The engine skips configured edge/tool text
before arming mute or waiting for playback; recorded audio still uses the
transport. Idle prompts continue to use LLMMessagesAppendFrame.
End-node shutdown uses the engine's shared playback wait before sending an
EndFrame, so sessions stay open while the closing response is being spoken.

Node updates retain each provider's protocol:

- Gemini (including Vertex) and Nova reconnect with updated context.
- OpenAI Realtime, Azure, and Grok update the existing session.
- Ultravox returns a native new-stage tool result on the existing call.
- OpenAI Live updates the delegation backend's instructions and tools while
  keeping its voice session running.

Provider adapters own readiness, context replay, and tool-result timing.
Reconnecting a node must not reset the conversation's opening-greeting guard.

The pipecat fork's services stay close to upstream — Dograh behavior lives
here.
"""
