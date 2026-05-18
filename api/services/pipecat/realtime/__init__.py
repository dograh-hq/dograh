"""Dograh-specific subclasses of pipecat realtime LLM services.

Each subclass wires Dograh engine integration quirks (user-mute gating,
TTSSpeakFrame greeting trigger, node-transition handling, function-call
deferral, etc.) onto the corresponding pipecat realtime service.

The pipecat fork's services stay close to upstream — Dograh behavior lives
here.
"""
