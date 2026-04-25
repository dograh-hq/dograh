"""GENERATED — do not edit by hand.

Regenerate with `python -m dograh_sdk.codegen` against the target
Dograh backend. Source of truth: each node's NodeSpec in the backend's
`api/services/workflow/node_specs/` directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

from dograh_sdk.typed._base import TypedNode


@dataclass(kw_only=True)
class Qa(TypedNode):
    """
    Run LLM quality analysis on the call transcript.  LLM hint: Runs an LLM
    quality review on the call transcript after completion. Per-node
    analysis splits the conversation by node and evaluates each segment
    against the configured system prompt. Sampling, minimum duration, and
    voicemail filters are supported.
    """

    type: ClassVar[str] = 'qa'

    name: str = 'QA Analysis'
    """
    Short identifier for this QA configuration.
    """

    qa_enabled: bool = True
    """
    When false, the QA run is skipped.
    """

    qa_system_prompt: str = 'You are a QA analyst evaluating a specific segment of a voice AI conversation.\n\n## Node Purpose\n{{node_summary}}\n\n## Previous Conversation Context (For start of conversation, previous conversation summary can be empty.)\n{{previous_conversation_summary}}\n\n## Tags to evaluate\n\nExamine the conversation carefully and identify which of the following tags apply:\n\n- UNCLEAR_CONVERSATION - The conversation is not coherent or clear, messages don\'t connect logically\n- ASSISTANT_IN_LOOP - The assistant asks the same question multiple times or gets stuck repeating itself\n- ASSISTANT_REPLY_IMPROPER - The assistant did not reply properly to the user\'s question/query or seems confused by what the user said\n- USER_FRUSTRATED - The user seems angry, frustrated, or is complaining about something in the call\n- USER_NOT_UNDERSTANDING - The user explicitly says they don\'t understand or repeatedly asks for clarification\n- HEARING_ISSUES - Either party can\'t hear the other ("hello?", "are you there?", "can you hear me?")\n- DEAD_AIR - Unusually long silences in the conversation (use the timestamps to judge)\n- USER_REQUESTING_FEATURE - The user asks for something the assistant can\'t fulfill\n- ASSISTANT_LACKS_EMPATHY - The assistant ignores the user\'s personal situation or emotional state and continues pitching or pushing the agenda.\n- USER_DETECTS_AI - The user suspects or identifies that they are talking to an AI/robot/bot rather than a real human.\n\n## Call metrics (pre-computed)\n\nUse these alongside the transcript for your analysis:\n{{metrics}}\n\n## Output format\n\nReturn ONLY a valid JSON object (no markdown):\n{\n    "tags": [\n        {\n            "tag": "TAG_NAME",\n            "reason": "Short reason with evidence from the transcript"\n        }\n    ],\n    "overall_sentiment": "positive|neutral|negative",\n    "call_quality_score": <1-10>,\n    "summary": "1-2 sentence summary of this segment"\n}\n\nIf no tags apply, return an empty tags list. Always provide sentiment, score, and summary.'
    """
    Instructions to the QA reviewer LLM. Supports placeholders:
    `{node_summary}`, `{previous_conversation_summary}`, `{transcript}`,
    `{metrics}`.
    """

    qa_min_call_duration: float = 15
    """
    Calls shorter than this are skipped.
    """

    qa_voicemail_calls: bool = False
    """
    When false, calls flagged as voicemail are skipped.
    """

    qa_sample_rate: float = 100
    """
    Percent of eligible calls QA'd. 100 means every call; lower values use
    random sampling.
    """

    qa_use_workflow_llm: bool = True
    """
    When true, the QA pass uses the same LLM the workflow runs with. Set
    false to specify a separate provider/model.
    """

    qa_provider: Optional[Literal['openai', 'azure', 'openrouter', 'anthropic']] = None
    """
    LLM provider used for the QA pass.
    """

    qa_model: str = 'default'
    """
    Model identifier (e.g., 'gpt-4o', 'claude-sonnet-4-6'). Provider-
    specific.
    """

    qa_api_key: Optional[str] = None
    """
    API key for the chosen provider.
    """

    qa_endpoint: Optional[str] = None
    """
    Required for the Azure provider.
    """

