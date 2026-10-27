"""Spoken-tool recovery must be independent of provider chunk boundaries."""

import json

import pytest

from api.services.pipecat.spoken_tool_call_guard import SpokenToolCallGuard

TOOLS = {
    "end_call": {"type": "object", "properties": {}, "required": []},
    "schedule_visit": {
        "type": "object",
        "properties": {
            "scheduled_at": {"type": "string"},
            "reason": {"type": "string"},
            "category": {"type": "string", "enum": ["standard"]},
        },
        "required": ["scheduled_at", "reason", "category"],
        "additionalProperties": False,
    },
}
ARGS = {
    "scheduled_at": "2032-04-05T09:30",
    "reason": "The site is closed. Try the following day.",
    "category": "standard",
}


def parse(chunks, tools=TOOLS, spoken_output=True):
    guard = SpokenToolCallGuard(tools, spoken_output=spoken_output)
    spoken = "".join(guard.feed(chunk) for chunk in chunks) + guard.finish()
    return spoken, [(c.name, c.arguments) for c in guard.calls]


@pytest.mark.parametrize(
    "fragment",
    [
        "invoke:default_api:end_call{}",
        "\ufeffcall:default_api:end_call{}",
        "default_api.end_call{}",
        "<call:default_api:end_call/>",
        "`default_api:end_call`",
        "`end_call `",
        "end_call",
        "Agent: end_call.",
        "invoke:end_call{}",
        '{"action": "default_api:end_call", "action_input": {}}',
        '{"action": "end_call"}',
        "non-executable-call:default_api:end_call{}",
        "\u0300call:default_api:end_call{}",
        "`default_api:end_call`{}",
        "invoke`default_api:end_call`{}",
        "Final: default_api:end_call{}",
        '{"end_call": {}}',
        '{"name": "end_call", "arguments": "{}"}',
    ],
)
@pytest.mark.parametrize("spoken_output", [True, False])
def test_goodbye_and_tool_at_every_split(fragment, spoken_output):
    text = "Have a nice day! " + fragment
    for split in range(len(text) + 1):
        speech, calls = parse([text[:split], text[split:]], spoken_output=spoken_output)
        assert speech.strip() == "Have a nice day!", (split, speech)
        assert calls == [("end_call", {})], (split, calls)
    assert parse(list(text), spoken_output=spoken_output)[1] == [("end_call", {})]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Goodbye!default_api:end_call{}", "Goodbye!"),
        ("Goodbye.invoke:end_call{}", "Goodbye."),
        ("Thanks.`default_api:end_call`{}", "Thanks."),
        ("Ok?default_api:end_call{}", "Ok?"),
        # A junk word that ends like a sentence is spoken rather than risked.
        ("Have a nice day.junk.end_call{}", "Have a nice day.junk."),
    ],
)
@pytest.mark.parametrize("spoken_output", [True, False])
def test_finished_sentence_before_a_glued_call_is_spoken(text, expected, spoken_output):
    for split in range(len(text) + 1):
        speech, calls = parse([text[:split], text[split:]], spoken_output=spoken_output)
        assert speech == expected, (split, speech)
        assert calls == [("end_call", {})], (split, calls)
    assert parse(list(text), spoken_output=spoken_output) == (
        expected,
        [("end_call", {})],
    )


def normalized(speech):
    return " ".join(speech.split())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Thank you, goodbye. acoustic_end_call_signal", "Thank you, goodbye."),
        ("Thank you, goodbye! CONVERSATION_OVER", "Thank you, goodbye!"),
        ("Goodbye. status_code: call_closed_by_agent", "Goodbye."),
        ("Goodbye.event_id:null", "Goodbye."),
        ("Goodbye. trace_output_17_0.2312", "Goodbye."),
        ("no_speech", ""),
        ("[silence]", ""),
        ("Goodbye! [call ended]", "Goodbye!"),
        ("(The agent waits in silence for an operator.) Hello?", "Hello?"),
        ("Goodbye.\n\n*Ends the call silently*", "Goodbye."),
        ("Goodbye!\n\n*(Closes the call", "Goodbye!"),
        (
            "Goodbye. ---\n*Note: closing the call\nHave a nice day.",
            "Goodbye. Have a nice day.",
        ),
        (
            "# Agent title\n\nGood morning, how can I help?",
            "Good morning, how can I help?",
        ),
        ("**Good morning**, how can I help?", "Good morning, how can I help?"),
        ("| Wait for the answer.", "Wait for the answer."),
        (
            '{"steer": "gatekeeper"}Understood, when can I call back?',
            "Understood, when can I call back?",
        ),
        ('{"date": "2032-04-05", "reason": "callback"} <|thought|>Perfect', "Perfect"),
        ('{ "label": "" }', ""),
        ("{}", ""),
        ("Ok. {}", "Ok."),
        (
            "The function end_call{} finishes the session.",
            "The function end_call finishes the session.",
        ),
        ('# (No words spoken)\n{\n  "unknown": {}', ""),
        ("It is called `end_call`.", "It is called ."),
    ],
)
def test_control_text_is_not_spoken_at_any_split(text, expected):
    for split in range(len(text) + 1):
        speech, calls = parse([text[:split], text[split:]])
        assert normalized(speech) == expected, (split, speech)
        assert calls == [], (split, calls)
    speech, calls = parse(list(text))
    assert (normalized(speech), calls) == (expected, [])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<thought", ""),
        ("<|thought|>Good morning.", "Good morning."),
        (
            "<think>The user wants a callback.</think>Sure, tomorrow at ten.",
            "Sure, tomorrow at ten.",
        ),
        ("<THOUGHT>plan</THOUGHT> Sure.", "Sure."),
        ("<thinking>plan</thinking> Sure.", "Sure."),
        ("<Thinking>\nplan\n</Thinking>\nSure.", "Sure."),
        ("</thinking> Sure.", "Sure."),
        ("Sure.<|im_end|>", "Sure."),
        ("Goodbye. non-thought", "Goodbye."),
        (
            '{"choices": [{"message": {"content": "Sure, tomorrow at ten."}}]}',
            "Sure, tomorrow at ten.",
        ),
        ("Goodbye.\u200b", "Goodbye."),
    ],
)
@pytest.mark.parametrize("spoken_output", [True, False])
def test_model_scaffolding_is_removed_in_voice_and_chat(text, expected, spoken_output):
    for split in range(len(text) + 1):
        speech, calls = parse([text[:split], text[split:]], spoken_output=spoken_output)
        assert normalized(speech) == expected, (split, speech)
        assert calls == []
    assert normalized(parse(list(text), spoken_output=spoken_output)[0]) == expected


def test_reasoning_longer_than_the_pending_limit_is_dropped_incrementally():
    reasoning = "<think>" + "x" * (SpokenToolCallGuard.MAX_PENDING_CHARS * 2)
    chunks = [reasoning[i : i + 700] for i in range(0, len(reasoning), 700)]
    assert parse([*chunks, "</think>Sure."]) == ("Sure.", [])


def test_envelope_reply_still_passes_through_the_guard():
    text = json.dumps(
        {"choices": [{"message": {"content": "Goodbye! default_api:end_call{}"}}]}
    )
    speech, calls = parse(list(text))
    assert speech.strip() == "Goodbye!"
    assert calls == [("end_call", {})]


def test_keyed_json_call_requires_a_valid_schema():
    text = json.dumps({"schedule_visit": {"scheduled_at": "2032-04-05T09:30"}})
    assert parse(list(text)) == ("", [])
    assert parse(list(json.dumps({"schedule_visit": ARGS}))) == (
        "",
        [("schedule_visit", ARGS)],
    )


def test_stage_direction_naming_a_tool_does_not_run_it():
    speech, calls = parse(list("Goodbye.\n*Silently runs end_call*"))
    assert normalized(speech) == "Goodbye."
    assert calls == []


@pytest.mark.parametrize("encoded", [False, True])
def test_react_arguments_with_punctuation_and_following_speech(encoded):
    text = json.dumps(
        {
            "action": "schedule_visit",
            "action_input": json.dumps(ARGS) if encoded else ARGS,
        }
    )
    speech, calls = parse(list(text + " I will call tomorrow."))
    assert speech.strip() == "I will call tomorrow."
    assert calls == [("schedule_visit", ARGS)]


def test_unquoted_arguments_are_recovered_without_guessing_values():
    text = (
        "I will call tomorrow "
        "dispatch:default_api:schedule_visit{"
        "scheduled_at:2032-04-05T09:30,reason:call again for the manager, closed on Mondays,"
        "category:standard} Have a nice day."
    )
    speech, calls = parse(list(text))
    assert "default_api" not in speech
    assert speech.strip() == "I will call tomorrow  Have a nice day."
    assert calls == [
        (
            "schedule_visit",
            {
                "scheduled_at": "2032-04-05T09:30",
                "reason": "call again for the manager, closed on Mondays",
                "category": "standard",
            },
        )
    ]


@pytest.mark.parametrize(
    "text",
    [
        'Hello. {"thought": "Need to call a tool. First check."} How are you?',
        "Hello. final_thought: The customer said goodbye. Call end_call.",
        "Hello. no-thought",
        "Hello. italics{thought}",
        'Hello. { "thought": "" } } How are you?',
        "Hello. thought",
    ],
)
def test_thought_scaffolding_is_never_spoken_or_executed(text):
    speech, calls = parse(list(text))
    assert "thought" not in speech
    assert "end_call" not in speech
    assert not calls
    assert "Hello." in speech
    if "How are you?" in text:
        assert "How are you?" in speech


@pytest.mark.parametrize(
    "text",
    [
        "The function name is end_call.",
        "Do not invoke end_call yet.",
        "I thought you wanted a callback.",
        "I had a thought: call tomorrow.",
        "Agent: How can I help?",
        "Note: the office opens at 9:30.",
        "Yes.Sure, see you tomorrow.",
        "Write to sample_user@example.org today.",
        "Your username is sample_user, correct?",
        "We meet on [Monday] at (around) ten.",
        "The total is 5 * 3 = 15.",
        "- First option, then the second.",
    ],
)
@pytest.mark.parametrize("spoken_output", [True, False])
def test_ordinary_prose_is_preserved(text, spoken_output):
    assert parse(list(text), spoken_output=spoken_output) == (text, [])
    assert parse([text], spoken_output=spoken_output) == (text, [])


@pytest.mark.parametrize(
    "text",
    [
        "The tool is named `end_call`.",
        '{"message": "This is legitimate JSON."}',
        "{}",
        "Ok. {}",
        "The function end_call{} finishes the session.",
        "# Heading\n\n**Bold** text | cell",
        "[Note] (aside) sample_user",
        "*waves* hello",
    ],
)
def test_text_chat_keeps_markup_and_json(text):
    assert parse(list(text), spoken_output=False) == (text, [])


@pytest.mark.parametrize(
    "fragment",
    [
        "default_api:unknown_tool{}",
        "default_api:schedule_visit{}",
        '{"action": "schedule_visit", "action_input": "{ \\"scheduled_at\\": \\"2032-04-05T19:30\\",',
        json.dumps(
            {"action": "schedule_visit", "action_input": {**ARGS, "category": "wrong"}}
        ),
        json.dumps(
            {"action": "schedule_visit", "action_input": {**ARGS, "reason": None}}
        ),
        'default_api:end_call{"unexpected": 1}',
    ],
)
def test_unknown_incomplete_or_invalid_calls_are_suppressed_without_execution(fragment):
    speech, calls = parse(list(fragment))
    assert speech == ""
    assert calls == []


def test_current_tool_schema_is_required():
    assert parse(list("default_api:end_call{}"), tools={}) == ("", [])


def test_normal_speech_is_released_before_response_end():
    guard = SpokenToolCallGuard(TOOLS)
    assert (
        guard.feed("Have a nice day! How are you? ") == "Have a nice day! How are you? "
    )
    assert guard.finish() == ""


def test_fenced_react_call():
    speech, calls = parse(
        list('```json\n{"action": "end_call", "action_input": {}}\n```')
    )
    assert speech == ""
    assert calls == [("end_call", {})]


def test_escaped_quotes_and_braces_inside_json_values():
    args = {**ARGS, "reason": 'They said "call again". Note: {tomorrow}.'}
    text = json.dumps({"action": "schedule_visit", "action_input": args})
    assert parse(list(text)) == ("", [("schedule_visit", args)])


def test_oversized_fragment_is_suppressed_and_not_recovered():
    fragment = '{"action":"end_call","action_input":"' + "x" * 20000
    assert parse([fragment, '"}']) == ("", [])


def test_duplicate_json_keys_are_not_repaired_into_an_action():
    assert parse(['{"action":"end_call","action":"schedule_visit"}']) == ("", [])


def test_schema_references_cannot_fetch_external_documents():
    schemas = {"end_call": {"$ref": "https://invalid.example/tool-schema"}}
    assert parse(["default_api:end_call{}"], tools=schemas) == ("", [])


@pytest.mark.parametrize("value", ["1e999", "-1e999", "NaN", "Infinity"])
def test_nonfinite_numbers_cannot_be_recovered(value):
    schemas = {
        "save": {
            "type": "object",
            "properties": {"value": {"type": "number"}},
            "required": ["value"],
        }
    }
    assert parse([f'default_api:save{{"value":{value}}}'], tools=schemas) == ("", [])


@pytest.mark.parametrize("width", [1, 512, 100000])
def test_complete_oversized_fragments_are_rejected_independently_of_chunk_size(width):
    text = json.dumps(
        {"action": "schedule_visit", "action_input": {**ARGS, "reason": "x" * 20000}}
    )
    assert parse([text[i : i + width] for i in range(0, len(text), width)]) == ("", [])


@pytest.mark.parametrize("name", ["end-call", "end.call"])
@pytest.mark.parametrize(
    "form",
    [
        "default_api:{name}{{}}",
        "default_api.{name}{{}}",
        "<call:default_api:{name}/>",
        "{name}{{}}",
        "`{name}`",
        "Agent: {name}.",
        "invoke:{name}{{}}",
    ],
)
def test_advertised_names_with_punctuation_at_every_split(name, form):
    tools = {name: TOOLS["end_call"], "end": TOOLS["end_call"]}
    fragment = form.format(name=name)
    for split in range(len(fragment) + 1):
        assert parse([fragment[:split], fragment[split:]], tools=tools) == (
            "",
            [(name, {})],
        )
    assert parse(list(fragment), tools=tools) == ("", [(name, {})])


@pytest.mark.parametrize("suffix", ["-unavailable", ".unavailable"])
def test_namespace_does_not_recover_known_prefix_of_an_unknown_tool(suffix):
    assert parse(list(f"default_api:end_call{suffix}{{}}")) == ("", [])


def test_comparisons_do_not_buffer_or_discard_ordinary_speech():
    guard = SpokenToolCallGuard(TOOLS)
    assert guard.feed("<") == ""
    assert guard.feed(" 5. This is normal speech. ") == "< 5. This is normal speech. "
    text = "The value is < five. " * 2000
    assert guard.feed(text) == text
    assert guard.finish() == ""
    assert not guard.calls
