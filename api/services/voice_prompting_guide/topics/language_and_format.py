"""Topic: phone-call output format and language handling."""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="language_and_format",
    title="Phone-call output: no markdown, explicit language, English alphabet",
    severity="medium",
    applies_to_node_types=("globalNode",),
    stages={
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Remind the model in the global prompt that this is a phone call: "
                "plain spoken sentences only, no markdown/lists/bold. State which "
                "language to respond in, and to render it in English alphabet so the "
                "TTS pronounces it correctly."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Confirm the prompt says it's a phone call (no formatting) and names "
                "the response language. Note: section headers like '## Success "
                "Criteria' in the PROMPT are fine and recommended — this rule is "
                "about the agent's spoken OUTPUT, not the prompt text."
            ),
        ),
    },
    content="""\
Voice has no formatting. No bullet points, no bold, no headers, no markdown the
caller can scan. Everything has to flow when spoken aloud.

Put these in the global prompt:
- Tell the model explicitly that this is a phone call and responses must be
  simple, unformatted sentences — no lists, markdown, bullets, bold, or italic.
- State which language the agent should respond in, and that it should try to
  match the language the user speaks. But always generate the response in the
  English alphabet — e.g. "Respond in French but use English letters, like
  'comment allez-vous aujourd'hui'." Native script in the LLM output causes
  weird failures in most TTS providers.

Important caveat — do NOT lint this against the prompt's own text. The prompt
itself SHOULD use section headers like "## Success Criteria" and numbered call
flows; the guide recommends them. This rule constrains the agent's spoken
OUTPUT at runtime, not the formatting of the prompt you write. A regex that
flags markdown in the prompt text would fire on well-structured prompts.

Examples (instruction → effect):
- Good: "This is a phone call. Reply in plain spoken sentences — no lists or
  markdown. Respond in the caller's language using English letters."
- Bad:  Leaving format unstated, so the agent answers with a bulleted list the
  TTS reads as "asterisk asterisk".
""",
    audit_checks=(
        AuditCheck(
            id="states_phone_call_plain_output",
            judge_question=(
                "Does the prompt make clear that the agent's spoken output must be "
                "plain unformatted sentences suitable for a phone call (no lists, "
                "markdown, or bullets)?"
            ),
            expected="yes",
            quote=(
                "Tell the model it's a phone call and output must be plain spoken "
                "sentences — no lists or markdown."
            ),
        ),
        AuditCheck(
            id="states_response_language",
            judge_question=(
                "Does the prompt state which language the agent should respond in "
                "(and, if non-English, that it should use the English alphabet)?"
            ),
            expected="yes",
            quote=(
                "Response language is unstated — name it, and require English-letter "
                "rendering so the TTS pronounces it right."
            ),
        ),
    ),
    cross_refs=("response_style", "speech_handling"),
)
