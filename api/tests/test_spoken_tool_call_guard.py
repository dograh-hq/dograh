"""Spoken-tool recovery must be independent of provider chunk boundaries."""

import json

import pytest

from api.services.pipecat.spoken_tool_call_guard import SpokenToolCallGuard

TOOLS = {
    "end_call": {"type": "object", "properties": {}, "required": []},
    "salva_richiamo": {
        "type": "object",
        "properties": {
            "data_ora": {"type": "string"},
            "motivo": {"type": "string"},
            "tipo": {"type": "string", "enum": ["generico"]},
        },
        "required": ["data_ora", "motivo", "tipo"],
        "additionalProperties": False,
    },
}
ARGS = {
    "data_ora": "2026-10-20T09:30",
    "motivo": "Cliente non disponibile. Richiamare domani.",
    "tipo": "generico",
}


def parse(chunks, tools=TOOLS):
    guard = SpokenToolCallGuard(tools)
    spoken = "".join(guard.feed(chunk) for chunk in chunks) + guard.finish()
    return spoken, [(c.name, c.arguments) for c in guard.calls]


@pytest.mark.parametrize(
    "fragment",
    [
        "severe:default_api:end_call{}",
        "\ufeffcall:default_api:end_call{}",
        "default_api.end_call{}",
        "<call:default_api:end_call/>",
        "`default_api:end_call`",
        "`end_call `",
        "end_call",
        "Agent: end_call.",
        "severe:end_call{}",
        "*(Esegue end_call per terminare la chiamata)*",
        '{"action": "default_api:end_call", "action_input": {}}',
        '{"action": "end_call"}',
    ],
)
def test_goodbye_and_tool_at_every_split(fragment):
    text = "Buona giornata! " + fragment
    for split in range(len(text) + 1):
        speech, calls = parse([text[:split], text[split:]])
        assert speech.strip() == "Buona giornata!", (split, speech)
        assert calls == [("end_call", {})], (split, calls)
    assert parse(list(text))[1] == [("end_call", {})]


@pytest.mark.parametrize("encoded", [False, True])
def test_react_arguments_with_punctuation_and_following_speech(encoded):
    text = json.dumps(
        {
            "action": "salva_richiamo",
            "action_input": json.dumps(ARGS) if encoded else ARGS,
        }
    )
    speech, calls = parse(list(text + " La richiamo domani."))
    assert speech.strip() == "La richiamo domani."
    assert calls == [("salva_richiamo", ARGS)]


def test_unquoted_arguments_are_recovered_without_guessing_values():
    text = (
        "La richiamo domani "
        "trattamento_dati:default_api:salva_richiamo{"
        "data_ora:2026-10-20T09:30,motivo:richiamare per i titolari, chiusi il lunedì,"
        "tipo:generico} Buona giornata."
    )
    speech, calls = parse(list(text))
    assert "default_api" not in speech
    assert speech.strip() == "La richiamo domani  Buona giornata."
    assert calls == [
        (
            "salva_richiamo",
            {
                "data_ora": "2026-10-20T09:30",
                "motivo": "richiamare per i titolari, chiusi il lunedì",
                "tipo": "generico",
            },
        )
    ]


@pytest.mark.parametrize(
    "text",
    [
        'Buongiorno. {"thought": "Need to call a tool. First check."} Come sta?',
        "Buongiorno. final_thought: The customer said goodbye. Call end_call.",
        "Buongiorno. no-thought",
        "Buongiorno. italics{thought}",
        'Buongiorno. { "thought": "" } } Come sta?',
        "Buongiorno. thought",
    ],
)
def test_thought_scaffolding_is_never_spoken_or_executed(text):
    speech, calls = parse(list(text))
    assert "thought" not in speech
    assert "end_call" not in speech
    assert not calls
    assert "Buongiorno." in speech
    if "Come sta?" in text:
        assert "Come sta?" in speech


@pytest.mark.parametrize(
    "text",
    [
        "Il nome della funzione è end_call.",
        "Non chiamare end_call per ora.",
        "Il tool si chiama `end_call`.",
        "La funzione end_call{} termina la chiamata.",
        "I thought you wanted a callback.",
        "I had a thought: call tomorrow.",
        "Agent: How can I help?",
        '{"message": "This is legitimate JSON."}',
    ],
)
def test_ordinary_prose_is_preserved(text):
    assert parse(list(text)) == (text, [])


@pytest.mark.parametrize(
    "fragment",
    [
        "default_api:unknown_tool{}",
        "default_api:salva_richiamo{}",
        '{"action": "salva_richiamo", "action_input": "{ \\"data_ora\\": \\"2026-10-20T19:30\\",',
        json.dumps(
            {"action": "salva_richiamo", "action_input": {**ARGS, "tipo": "wrong"}}
        ),
        json.dumps(
            {"action": "salva_richiamo", "action_input": {**ARGS, "motivo": None}}
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
    assert guard.feed("Buona giornata! Come sta? ") == "Buona giornata! Come sta? "
    assert guard.finish() == ""


def test_fenced_react_call():
    speech, calls = parse(
        list('```json\n{"action": "end_call", "action_input": {}}\n```')
    )
    assert speech == ""
    assert calls == [("end_call", {})]


def test_escaped_quotes_and_braces_inside_json_values():
    args = {**ARGS, "motivo": 'Ha detto "richiamare". Nota: {domani}.'}
    text = json.dumps({"action": "salva_richiamo", "action_input": args})
    assert parse(list(text)) == ("", [("salva_richiamo", args)])


def test_oversized_fragment_is_suppressed_and_not_recovered():
    fragment = '{"action":"end_call","action_input":"' + "x" * 20000
    assert parse([fragment, '"}']) == ("", [])


def test_duplicate_json_keys_are_not_repaired_into_an_action():
    assert parse(['{"action":"end_call","action":"salva_richiamo"}']) == ("", [])


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
        {"action": "salva_richiamo", "action_input": {**ARGS, "motivo": "x" * 20000}}
    )
    assert parse([text[i : i + width] for i in range(0, len(text), width)]) == ("", [])
