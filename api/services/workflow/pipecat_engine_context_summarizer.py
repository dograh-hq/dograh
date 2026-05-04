from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Optional

from loguru import logger
from opentelemetry import trace
from pipecat.frames.frames import LLMContextSummaryRequestFrame
from pipecat.utils.context.llm_context_summarization import (
    LLMContextSummarizationUtil,
    LLMContextSummaryConfig,
)
from pipecat.utils.tracing.service_attributes import add_llm_span_attributes

from api.services.pipecat.tracing_config import ensure_tracing

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine import PipecatEngine


class ContextSummarizationManager:
    """Manages background context summarization on node transitions.

    Replaces old messages (including orphaned tool calls from previous nodes)
    with a concise summary to keep the context window manageable.
    """

    def __init__(self, engine: "PipecatEngine") -> None:
        self._engine = engine
        self._summarization_task: Optional[asyncio.Task] = None
        self._config = LLMContextSummaryConfig(
            target_context_tokens=4000,
            min_messages_after_summary=2,
            summarization_timeout=30.0,
        )

    @property
    def config(self) -> LLMContextSummaryConfig:
        return self._config

    def start(self) -> None:
        """Kick off background context summarization, cancelling any in-flight one."""
        if self._summarization_task and not self._summarization_task.done():
            self._summarization_task.cancel()

        current_node = self._engine._current_node
        self._summarization_task = asyncio.create_task(
            self._summarize_context_in_background(),
            name=f"ctx-summarize:{current_node.name}",
        )

    async def cleanup(self) -> None:
        """Cancel any in-flight background summarization."""
        if self._summarization_task and not self._summarization_task.done():
            self._summarization_task.cancel()

    async def _summarize_context_in_background(self) -> None:
        """Summarize conversation context after a node transition.

        Runs as a fire-and-forget background task so it doesn't block
        the new node from speaking. Replaces old messages (including
        orphaned tool calls from previous nodes) with a concise summary.
        """
        context = self._engine.context
        llm = self._engine.inference_llm
        current_node = self._engine._current_node

        try:
            messages = context.messages
            # Not worth summarizing if context is small
            if len(messages) <= 6:
                return

            config = self._config
            request_frame = LLMContextSummaryRequestFrame(
                request_id=f"node-transition-{current_node.id}",
                context=context,
                min_messages_to_keep=config.min_messages_after_summary,
                target_context_tokens=config.target_context_tokens,
                summarization_prompt=config.summary_prompt,
                summarization_timeout=config.summarization_timeout,
            )

            # Capture parent OTel context before the await
            parent_ctx = self._engine._get_otel_context()

            summary_text, last_index = await asyncio.wait_for(
                llm._generate_summary(request_frame),
                timeout=config.summarization_timeout,
            )

            if not summary_text or last_index < 0:
                logger.warning(
                    "Context summarization returned empty result, keeping full context"
                )
                return

            # Trace the LLM call — mirror what _generate_summary sends to
            # run_inference: system prompt + formatted transcript as user msg.
            model_name = getattr(llm, "model_name", "unknown")
            if ensure_tracing():
                summarize_result = (
                    LLMContextSummarizationUtil.get_messages_to_summarize(
                        context, config.min_messages_after_summary
                    )
                )
                transcript = LLMContextSummarizationUtil.format_messages_for_summary(
                    summarize_result.messages
                )
                tracer = trace.get_tracer("pipecat")
                with tracer.start_as_current_span(
                    "llm-context-summarization", context=parent_ctx
                ) as span:
                    tracing_messages = [
                        {"role": "system", "content": config.summary_prompt},
                        {
                            "role": "user",
                            "content": f"Conversation history:\n{transcript}",
                        },
                    ]
                    add_llm_span_attributes(
                        span,
                        service_name=llm.__class__.__name__,
                        model=model_name,
                        operation_name="llm-context-summarization",
                        messages=tracing_messages,
                        output=json.dumps({"content": summary_text}),
                        stream=False,
                        parameters={
                            "target_context_tokens": config.target_context_tokens,
                        },
                    )

            # Snapshot current messages at apply-time (not request-time)
            # to preserve anything added while the summary was generating
            current_messages = context.messages
            recent_messages = current_messages[last_index + 1 :]

            summary_message = {
                "role": "user",
                "content": config.summary_message_template.format(summary=summary_text),
            }

            # Preserve the current system message (already set by the new node)
            first_system_msg = next(
                (
                    m
                    for m in current_messages
                    if isinstance(m, dict) and m.get("role") == "system"
                ),
                None,
            )

            new_messages = []
            if first_system_msg:
                new_messages.append(first_system_msg)
            new_messages.append(summary_message)
            new_messages.extend(recent_messages)

            context.set_messages(new_messages)
            logger.info(
                f"Background context summarization applied: "
                f"{len(current_messages)} -> {len(new_messages)} messages"
            )
        except asyncio.CancelledError:
            logger.debug("Context summarization cancelled (new transition started)")
        except asyncio.TimeoutError:
            logger.warning(
                f"Context summarization timed out after {self._config.summarization_timeout}s"
            )
        except Exception as e:
            logger.error(f"Background context summarization failed: {e}")
