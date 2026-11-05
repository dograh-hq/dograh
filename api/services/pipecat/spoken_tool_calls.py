"""Shared execution policy for provider-specific spoken-tool stream adapters."""

import asyncio
import json
import uuid

from loguru import logger

from api.services.pipecat.spoken_tool_call_guard import SpokenToolCallGuard
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallFromLLM, FunctionCallRunnerItem


class SpokenToolCallMixin:
    """Validate recovery against the current response and preserve tool timing."""

    # Replies are synthesized to speech unless the owner says they are read.
    spoken_output = True

    def _new_spoken_tool_guard(self, advertised):
        schemas = (
            {
                tool.name: tool.to_default_dict()["parameters"]
                for tool in advertised.standard_tools
                if tool.name in self._functions
            }
            if isinstance(advertised, ToolsSchema)
            else {}
        )
        return SpokenToolCallGuard(schemas, spoken_output=self.spoken_output)

    def _recover_spoken_tool_calls(
        self,
        guard,
        *,
        context,
        advertised,
        tool_choice,
        generation,
        native_names,
        completed,
    ):
        can_recover = (
            completed
            and not self._tool_response_interrupted
            and generation == self._tool_response_generation
            and context.tools is advertised
        )
        by_name = {}
        for call in guard.calls:
            by_name.setdefault(call.name, {})[
                json.dumps(call.arguments, sort_keys=True)
            ] = call
        native_transition = any(
            self._function_is_node_transition(name) for name in native_names
        )
        recovered_transitions = {
            name
            for name in by_name
            if name not in native_names and self._function_is_node_transition(name)
        }
        recovered = []
        for name, alternatives in by_name.items():
            conflicting_transition = name in recovered_transitions and (
                native_transition or len(recovered_transitions) > 1
            )
            if (
                not can_recover
                or name in native_names
                or name not in self._functions
                or not self._spoken_tool_choice_allows(name, tool_choice)
                or not self._spoken_tool_choice_allows(name, context.tool_choice)
                or len(alternatives) != 1
                or conflicting_transition
            ):
                logger.info(
                    "Spoken tool fragment suppressed: tool={} recovery=not_executed",
                    name,
                )
                continue
            call = next(iter(alternatives.values()))
            call_id = f"text-{uuid.uuid4().hex}"
            recovered.append(
                FunctionCallFromLLM(
                    context=context,
                    tool_call_id=call_id,
                    function_name=name,
                    arguments=call.arguments,
                )
            )
            logger.info(
                "Spoken tool call recovered: tool={} tool_call_id={}", name, call_id
            )
        return recovered

    @staticmethod
    def _spoken_tool_choice_allows(name, tool_choice):
        if isinstance(tool_choice, dict):
            return (
                tool_choice.get("type") == "function"
                and tool_choice.get("function", {}).get("name") == name
            )
        return tool_choice != "none"

    async def _run_or_defer_function_calls(
        self, function_calls, *, text_generated, response_generation=None
    ):
        transitions = [
            call
            for call in function_calls
            if self._function_is_node_transition(call.function_name)
        ]
        if (
            any(
                (call.tool_call_id or "").startswith("text-") for call in function_calls
            )
            and len(function_calls) > 1
            and len(transitions) == 1
            and text_generated
            and not self._skip_tts
            and not self._tool_response_playback_stopped
            and not self._tool_response_interrupted
            and response_generation == self._tool_response_generation
            and not self._speculation_gate.is_speculating
        ):
            # Keep one tool group: dispatching saves separately would let their
            # results trigger another inference before the transition is accepted.
            ordinary = [call for call in function_calls if call not in transitions]
            self._pending_node_transition_function_calls = ordinary + transitions
            # Accept ordinary side effects now through the workflow's independent
            # owner, so interruption/worker cleanup cannot discard a requested
            # save. Delivery still uses the normal grouped dispatch after TTS;
            # the owner deduplicates that second preparation by tool_call_id.
            runner_items = []
            for call in ordinary:
                registry_item = self._functions.get(call.function_name)
                if registry_item is None:
                    registry_item = self._functions.get(None)
                if registry_item is not None:
                    runner_items.append(
                        FunctionCallRunnerItem(
                            registry_item=registry_item,
                            function_name=call.function_name,
                            tool_call_id=call.tool_call_id,
                            arguments=call.arguments,
                            context=call.context,
                        )
                    )
            try:
                await self._call_event_handler(
                    "on_function_calls_prepared", runner_items
                )
            except asyncio.CancelledError:
                # The fallback owner rejects work from a losing/stale attempt.
                self._discard_pending_node_transition_calls("not_accepted")
                raise
            logger.debug("Deferring recovered tool batch until after TTS")
            return
        await super()._run_or_defer_function_calls(
            function_calls,
            text_generated=text_generated,
            response_generation=response_generation,
        )
