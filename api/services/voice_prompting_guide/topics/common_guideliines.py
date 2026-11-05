"""Topic: concise shared instructions for phone-call agents."""

from api.services.voice_prompting_guide._base import VoicePromptingTopic

TOPIC = VoicePromptingTopic(
    id="common_guidelines",
    title="Shared identity, voice style, and business boundaries",
    content="""\
Use the global node for instructions shared by conversation steps. Include only
what this agent needs: its role and goal, language, voice style, and business rules.
Use the supplied business identity; do not invent a name, company, policies, or persona.

Speak naturally and concisely. Ask one question at a time and allow the caller to
answer. Contractions are fine; forced fillers, deliberate grammar mistakes, and
questions after every response are not required. Acknowledge requests without
repeating the same phrase on every turn.

Use only known business information and supplied call context. If something is
missing, acknowledge it and ask only when it matters to the call. Do not claim a
booking, update, or transfer succeeded without its result.

If speech is unclear, ask for the relevant detail again. If the caller asks for a
repeat, repeat the relevant part. Handle brief interruptions and return to the
current task. Follow the supplied policy for out-of-scope requests and ending calls.

Include current time only when the task needs it: {{current_time}} and
{{current_weekday}} are UTC. Known IANA timezones can use variables such as
{{current_time_America/New_York}} and {{current_weekday_America/New_York}}.
""",
)
