"""Topic: spoken form for numbers, dates, and money.

This is the canonical `review_signals` carrier. The signals fire on
literal digit/symbol forms appearing in the *prompt text* — typically
inside examples — because the model echoes the form its examples use.
That is a check on prompt-text CONTENT, not on inferred runtime
behavior, which is what keeps it a legitimate mechanical signal.
"""

from __future__ import annotations

from api.services.voice_prompting_guide._base import (
    AuditCheck,
    ReviewSignal,
    Stage,
    StageLens,
    VoicePromptingTopic,
)

TOPIC = VoicePromptingTopic(
    id="numbers_dates_money",
    title="Use spoken form for numbers, dates, and money",
    severity="high",
    applies_to_node_types=("globalNode", "agentNode", "startCall", "endCall"),
    stages={
        Stage.create: StageLens(
            relevant=True,
            lens=(
                "Tell the agent to speak dates, money, and numbers in spoken form — "
                "'January second, twenty twenty-five', 'two hundred dollars and "
                "forty cents', digits grouped and spaced. Write any examples in the "
                "prompt that same way; the model copies the form it sees."
            ),
        ),
        Stage.review: StageLens(
            relevant=True,
            lens=(
                "Scan prompt examples for digit/symbol forms ('$200.40', '1/2/2025', "
                "long digit runs). Those get echoed by the agent and read out oddly "
                "by the TTS — rewrite them in spoken form."
            ),
        ),
    },
    content="""\
For dates, money, and numbers, instruct the agent to use the spoken form. The
TTS reads raw numerals in unpredictable ways and confuses the caller.

- Dates: "January second, twenty twenty-five", not "1/2/2025".
- Money: "two hundred dollars and forty cents", not "$200.40".
- Phone numbers and codes: speak each character, grouped and spaced — "five
  five five, two three nine, eight one two three", not "5552398123". When
  reading a code, separate characters with hyphens or spaces ("four - one -
  five").

This matters as much in the prompt's examples as in the instruction. Models
follow the form of their sample phrases closely, so if an example in the prompt
says "$200.40" the agent will say "$200.40". Write every numeric example in the
spoken form you want the agent to produce.

This pairs with reading critical values back character-by-character — when you
confirm a phone number or amount, both the readback and the value should be in
spoken form.

Examples (prompt example → what the agent will say):
- Good: 'Confirm the total: "that's two hundred dollars and forty cents, "
  "correct?"'
- Bad:  'Confirm the total: "that's $200.40, correct?"'  (Agent echoes
  "$200.40"; TTS may read it as "dollar two hundred point four zero".)
""",
    review_signals=(
        ReviewSignal(
            id="money_in_digits",
            pattern=r"\$\d",
            quote=(
                "Money written as digits in the prompt (e.g. '$200.40') — the agent "
                "echoes the form it sees; use spoken form ('two hundred dollars and "
                "forty cents')."
            ),
        ),
        ReviewSignal(
            id="numeric_date",
            pattern=r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",
            quote=(
                "Date written as digits in the prompt (e.g. '1/2/2025') — use spoken "
                "form ('January second, twenty twenty-five')."
            ),
        ),
        ReviewSignal(
            id="long_digit_run",
            pattern=r"\b\d{7,}\b",
            quote=(
                "Long digit run in the prompt (e.g. a phone number or code) — write "
                "it grouped and spaced ('five five five, two three nine, eight one "
                "two three') so the agent reads it that way."
            ),
        ),
    ),
    audit_checks=(
        AuditCheck(
            id="instructs_spoken_numeric_form",
            judge_question=(
                "Does the prompt instruct the agent to speak numbers, dates, and "
                "money in spoken form (e.g. 'January second', 'two hundred dollars') "
                "rather than as raw numerals?"
            ),
            expected="yes",
            quote=(
                "No spoken-form guidance for numbers/dates/money — the TTS reads raw "
                "numerals oddly."
            ),
        ),
    ),
    cross_refs=("readback_and_extraction",),
)
