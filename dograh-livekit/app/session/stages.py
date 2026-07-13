"""Stage agents — reusable conversation stage types for Agno-powered Dograh workflows."""

from __future__ import annotations

import json
import logging
from typing import Any

from livekit.agents import Agent, RunContext, function_tool

from app.session.flow import render_template

logger = logging.getLogger(__name__)


class LuminaStageAgent(Agent):
    """Base class for all Dograh-LiveKit stage agents."""

    def __init__(
        self,
        stage_config: dict,
        agent_config: dict,
        all_stages: list[dict] | None = None,
    ):
        self.stage_id: str = stage_config.get("id", "")
        self.stage_type: str = stage_config.get("type", "custom")
        self.stage_label: str = (
            stage_config.get("label") or self.stage_type.replace("_", " ").title()
        )
        self._stage_config = stage_config
        self._agent_config = agent_config
        self._routes: dict[str, str] = stage_config.get("routes") or {}
        self._all_stages: list[dict] = all_stages or []

        instructions = self._build_instructions(agent_config)
        super().__init__(instructions=instructions)

    def _build_instructions(self, agent_config: dict) -> str:
        agent_name = str(agent_config.get("agent_name") or "").strip()
        system_prompt = str(agent_config.get("system_prompt") or "").strip()

        identity_block = ""
        if system_prompt:
            identity_block = (
                "═══ CONTESTO AGENTE ═══\n"
                f"{system_prompt}\n"
                "═══════════════════════\n\n"
            )
        elif agent_name:
            identity_block = f"Sei {agent_name}.\n\n"

        base = self._base_instructions()
        custom = self._stage_config.get("instructions", "").strip()

        parts = [identity_block, base, custom]
        instructions = "\n\n".join(part for part in parts if part)
        return render_template(instructions, agent_config.get("flow_variables") or {})

    def _base_instructions(self) -> str:
        return "Esegui questa fase della conversazione."

    async def _complete_and_handoff(
        self, result: dict, route_key: str | None = None
    ) -> Agent | None:
        logger.info(
            "Stage '%s' completed: route_key=%s data=%s",
            self.stage_id, route_key, list(result.keys()),
        )
        return None

    async def on_enter(self) -> None:
        pass


class CustomStage(LuminaStageAgent):
    """Generic stage following node instructions."""

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Inizia questa fase seguendo le tue istruzioni."
        )

    @function_tool
    async def complete_custom_stage(
        self, result: str, ctx: RunContext, route_key: str | None = None
    ) -> Agent | None:
        try:
            data = json.loads(result)
        except Exception:
            data = {"result": result}
        return await self._complete_and_handoff(data, route_key=route_key)


class IdentifyIntentStage(LuminaStageAgent):
    """Classify caller intent and route to the appropriate stage."""

    def _base_instructions(self) -> str:
        routes = self._stage_config.get("routes") or {}
        stages_by_id = {s.get("id"): s for s in self._all_stages if s.get("id")}
        route_lines = []
        for route_key, target_id in routes.items():
            rk = str(route_key).strip()
            if not rk or rk == "*":
                continue
            target = stages_by_id.get(target_id)
            target_label = str((target or {}).get("label") or "").strip()
            desc = f"  - '{rk}'"
            if target_label:
                desc += f": {target_label}"
            route_lines.append(desc)

        cats_block = ""
        if route_lines:
            first_key = next((str(k).strip() for k in routes if str(k).strip() != "*"), "categoria")
            cats_block = (
                "Le categorie disponibili:\n"
                + "\n".join(route_lines)
                + f"\n\nUsa come valore di 'intent' esattamente una delle chiavi (es. '{first_key}'). "
            )

        return (
            "Il tuo obiettivo è capire il motivo della richiesta e instradare "
            "correttamente usando 'record_intent'.\n\n"
            f"{cats_block}"
            "REGOLE:\n"
            "1. Solo saluto → NON chiamare record_intent, rispondi e chiedi come aiutare.\n"
            "2. Qualsiasi altra cosa → classifica SUBITO con record_intent.\n"
            "3. Nel dubbio, scegli la categoria informativa/generica.\n"
        )

    async def on_enter(self) -> None:
        greeting = str(self._agent_config.get("greeting_message") or "").strip()
        if greeting:
            await self.session.generate_reply(
                instructions=f"Di' esattamente questa frase: \"{greeting}\""
            )
        else:
            await self.session.generate_reply(
                instructions="Chiedi all'utente come puoi aiutarlo oggi."
            )

    @function_tool
    async def record_intent(
        self, intent: str, description: str, urgency: str, ctx: RunContext
    ) -> Agent | None:
        route_key = str(intent or "").strip().lower()
        return await self._complete_and_handoff(
            {"intent": route_key or intent, "intent_description": description, "urgency": urgency},
            route_key=route_key,
        )


class CloseStage(LuminaStageAgent):
    """Close the conversation professionally."""

    def _base_instructions(self) -> str:
        return (
            "Il tuo obiettivo è chiudere la conversazione in modo professionale. "
            "Riepiloga quanto concordato, conferma i prossimi passi e saluta. "
            "Usa il tool 'close_call' quando hai finito."
        )

    async def on_enter(self) -> None:
        await self.session.generate_reply(
            instructions="Riepiloga brevemente quanto discusso e saluta il cliente."
        )

    @function_tool
    async def close_call(self, summary: str, outcome: str, ctx: RunContext) -> None:
        await self._complete_and_handoff({"call_summary": summary, "call_outcome": outcome})
