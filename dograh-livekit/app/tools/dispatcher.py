"""LuminaAgent — default LiveKit Agent with dynamic tool loading."""

import logging
from livekit import agents
from app.tools.registry import build_tools
from app.session.flow import render_template

logger = logging.getLogger(__name__)


class LuminaAgent(agents.Agent):
    """Default agent for Dograh-LiveKit sessions."""

    def __init__(self, config: dict):
        self._config = config
        self._deploy_id = config.get("deploy_id", "")
        self._org_id = config.get("org_id", "")
        self._kb_refs = config.get("kb_refs", []) or []
        self._greeting_message = config.get("greeting_message", "").strip()
        self._channel = config.get("channel", "")

        llm_provider = config.get("llm_config", {}).get("provider", "")
        self._is_realtime = llm_provider in {
            "google_realtime", "openai_realtime", "aws_realtime",
        }

        instructions = config.get("system_prompt", "Sei un assistente.")
        if self._kb_refs:
            instructions += (
                "\n\nSe l'utente chiede dettagli specifici, chiama "
                "`search_knowledge` prima di rispondere."
            )
        instructions = render_template(instructions, config.get("flow_variables") or {})

        tools = build_tools(self)
        super().__init__(instructions=instructions, tools=tools)

    async def on_enter(self) -> None:
        if not self._is_realtime:
            return
        if self._greeting_message:
            await self.session.generate_reply(
                instructions=f"Di' esattamente questa frase: \"{self._greeting_message}\""
            )
