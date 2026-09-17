"""Synthetic contract examples; these are not the production evaluation corpus."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.pipecat.answer_classification import (
    MachineSubtype,
    classify_machine_utterance,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Please leave a message after the tone.", MachineSubtype.VOICEMAIL),
        ("Leave your name and number after the beep.", MachineSubtype.VOICEMAIL),
        ("The mailbox is full. You cannot leave a message.", MachineSubtype.NO_MESSAGE),
        ("This mailbox has not been set up.", MachineSubtype.NO_MESSAGE),
        ("The subscriber is not accepting messages.", MachineSubtype.NO_MESSAGE),
        ("Tell me your name and reason for calling.", MachineSubtype.SCREENER),
        ("Please say your name and I'll try to connect you.", MachineSubtype.SCREENER),
        (
            "for calling. I'll see if this person is available.",
            MachineSubtype.SCREENER,
        ),
        ("I'll see if this person is available.", MachineSubtype.SCREENER),
        ("I’ll see if this person is available.", MachineSubtype.SCREENER),
        ("I will see if this person is available.", MachineSubtype.SCREENER),
        ("I'LL see if this person\n is available.", MachineSubtype.SCREENER),
        ("Thanks. Please stay on the line.", MachineSubtype.SCREENING_WAIT),
        ("Please remain on the line.", MachineSubtype.SCREENING_WAIT),
        ("Thank you. Please hold.", MachineSubtype.SCREENING_WAIT),
        ("Please hold while I connect you.", MachineSubtype.SCREENING_WAIT),
        ("One moment while we transfer your call.", MachineSubtype.SCREENING_WAIT),
        ("For sales press 1. For support press 2.", MachineSubtype.IVR),
        ("Hello, this is Priya, how can I help?", MachineSubtype.UNKNOWN),
        ("Thanks for calling, this is Alex.", MachineSubtype.UNKNOWN),
        ("I'll see if Alex is available.", MachineSubtype.UNKNOWN),
        ("Let me check if someone is available.", MachineSubtype.UNKNOWN),
        ("This person is available tomorrow.", MachineSubtype.UNKNOWN),
        ("Please hold my appointment for tomorrow.", MachineSubtype.UNKNOWN),
        ("", MachineSubtype.UNKNOWN),
    ],
)
def test_machine_subtypes(text, expected):
    assert classify_machine_utterance(text) == expected


@pytest.mark.parametrize("instruction", ["Press", "Dial"])
@pytest.mark.parametrize(
    "digit",
    ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
)
def test_ivr_recognizes_all_spoken_digits(instruction, digit):
    assert (
        classify_machine_utterance(f"{instruction} {digit} to continue.")
        == MachineSubtype.IVR
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        # IVR: a keypad instruction carrying a spoken digit.
        ("Premere zero", MachineSubtype.IVR),
        ("Premere 0", MachineSubtype.IVR),
        ("Digitare lo zero per parlare con un operatore.", MachineSubtype.IVR),
        ("Selezionare 0 per continuare.", MachineSubtype.IVR),
        ("Per il commerciale digitare due.", MachineSubtype.IVR),
        ("Premere uno per parlare con l'amministrazione.", MachineSubtype.IVR),
        ("Selezionare tre per il magazzino.", MachineSubtype.IVR),
        ("Digiti quattro per l'ufficio tecnico.", MachineSubtype.IVR),
        ("Se conoscete il numero dell'interno, digitatelo ora.", MachineSubtype.IVR),
        ("Selezionare l'interno desiderato.", MachineSubtype.IVR),
        ("Per inglese premere due.", MachineSubtype.IVR),
        # VOICEMAIL: a mailbox that will record something.
        ("Questa e' una segreteria telefonica.", MachineSubtype.VOICEMAIL),
        ("Lasciate un messaggio dopo il segnale acustico.", MachineSubtype.VOICEMAIL),
        ("Lasciateci il vostro nominativo e recapito.", MachineSubtype.VOICEMAIL),
        ("Potete lasciarci un messaggio in segreteria.", MachineSubtype.VOICEMAIL),
        # NO_MESSAGE: an announcement with nothing to record.
        ("La segreteria e' piena.", MachineSubtype.NO_MESSAGE),
        ("Il tempo a disposizione e' esaurito.", MachineSubtype.NO_MESSAGE),
        ("Memoria esaurita.", MachineSubtype.NO_MESSAGE),
        # SCREENING_WAIT: a queue announcement.
        ("Si prega di restare in linea.", MachineSubtype.SCREENING_WAIT),
        (
            "Vi rispondera' il primo operatore disponibile.",
            MachineSubtype.SCREENING_WAIT,
        ),
        (
            "Tutti i nostri operatori sono momentaneamente occupati.",
            MachineSubtype.SCREENING_WAIT,
        ),
        (
            "I nostri operatori sono temporaneamente occupati.",
            MachineSubtype.SCREENING_WAIT,
        ),
        (
            "Le nostre linee sono momentaneamente occupate.",
            MachineSubtype.SCREENING_WAIT,
        ),
        (
            "Le linee sono temporaneamente occupate.",
            MachineSubtype.SCREENING_WAIT,
        ),
        ("Attendere in linea.", MachineSubtype.SCREENING_WAIT),
        (
            "Un operatore le rispondera' appena possibile.",
            MachineSubtype.SCREENING_WAIT,
        ),
        # Phrasings taken from the deployment's own classifier instructions,
        # kept only where a live person has no reason to say them.
        (
            "Risponde la segreteria telefonica di Studio Esempio.",
            MachineSubtype.VOICEMAIL,
        ),
        ("Vi richiameremo appena possibile.", MachineSubtype.VOICEMAIL),
        ("Sarete richiamati il prima possibile.", MachineSubtype.VOICEMAIL),
        ("Siamo momentaneamente assenti.", MachineSubtype.VOICEMAIL),
        ("La casella vocale e' piena.", MachineSubtype.NO_MESSAGE),
        ("La casella non e' stata attivata.", MachineSubtype.NO_MESSAGE),
        (
            "L'utente da lei chiamato non e' al momento raggiungibile.",
            MachineSubtype.NO_MESSAGE,
        ),
        (
            "Il numero da lei chiamato non e' raggiungibile.",
            MachineSubtype.NO_MESSAGE,
        ),
        (
            "L'utente da lei chiamato non è al momento raggiungibile.",
            MachineSubtype.NO_MESSAGE,
        ),
        (
            "Il numero da lei composto non e' attivo o raggiungibile.",
            MachineSubtype.NO_MESSAGE,
        ),
        (
            "L’utente da lei chiamato non e’ raggiungibile.",
            MachineSubtype.NO_MESSAGE,
        ),
        ("Il numero selezionato e' inesistente.", MachineSubtype.NO_MESSAGE),
        ("Tutte le linee sono occupate.", MachineSubtype.NO_MESSAGE),
    ],
)
def test_italian_machine_subtypes(text, expected):
    assert classify_machine_utterance(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # A live person answering, in the phrasings that dominate Italian
        # outbound answers. None of these may reach a machine subtype: the
        # cheapest outcome of a miss is one classifier call, the cheapest
        # outcome of a false match is a dropped human.
        "Pronto?",
        "Si', mi dica.",
        "Sono io.",
        "Sono io, prego.",
        "Buongiorno, sono Giulia.",
        "Puo' dire a me?",
        "Per che cosa, scusi?",
        "Di cosa si tratta?",
        # Screening and objections: a person, however unhelpful.
        "Guardi, il titolare non c'e' in questo momento.",
        "Al momento non c'e'. Puo' chiamare piu' tardi?",
        "Non c'e' nessuno, mi dispiace.",
        "Lo trova domani mattina.",
        "E' fuori sede, rientra la settimana prossima.",
        # A receptionist reporting unavailability is still a live answer.
        "Il signor Rossi non è raggiungibile al momento",
        "Il titolare non è al momento raggiungibile.",
        "Il signor Rossi non e' raggiungibile al momento.",
        "Il titolare non e’ al momento raggiungibile.",
        "Non è al momento raggiungibile, posso aiutarla io?",
        "Non sono interessato, la ringrazio.",
        "Guardi, siamo gia' a posto con la telefonia.",
        "Non siamo interessati, buona giornata.",
        # Being busy alone does not identify an automated queue announcement.
        "Sono momentaneamente occupato",
        "Sono momentaneamente occupata, puo' richiamare?",
        "Sono temporaneamente occupato.",
        "Scusi, sono temporaneamente occupata.",
        "Siamo momentaneamente occupati.",
        "Il titolare e' temporaneamente occupato.",
        # Numbers spoken by a person: times and quantities, not a keypad menu.
        "Richiami verso le due, dopo pranzo.",
        "Siamo aperti dalle otto alle dodici.",
        "Ci sono tre persone in ufficio adesso.",
        "Ho zero tempo per parlare adesso.",
        "Ho 0 chiamate perse.",
        # Closure and absence phrasings that the deployment's instructions list
        # as voicemail. A recorded greeting and a live employee say these in the
        # same words, so they stay with the classifier, which reads the whole
        # utterance, rather than becoming a pattern that would drop the person.
        "Buongiorno, no, siamo chiusi adesso a quest'ora.",
        "Noi siamo chiusi, alle dodici e trenta chiudiamo.",
        "Gli uffici sono chiusi, li trova domani mattina.",
        "Non c'e' nessuno in ufficio adesso.",
        "Oggi il negozio e' chiuso, richiami domani.",
    ],
)
def test_italian_live_answers_are_never_a_machine_subtype(text):
    assert classify_machine_utterance(text) == MachineSubtype.UNKNOWN


def test_language_fragments_share_one_pattern_per_subtype():
    """Adding a language must add an alternation, not another search() call."""
    from api.services.pipecat.answer_classification import _PATTERNS

    subtypes = [subtype for subtype, _ in _PATTERNS]
    assert len(subtypes) == len(set(subtypes))
    assert subtypes == [
        MachineSubtype.NO_MESSAGE,
        MachineSubtype.SCREENER,
        MachineSubtype.IVR,
        MachineSubtype.VOICEMAIL,
        MachineSubtype.SCREENING_WAIT,
    ]


def test_patterns_use_bounded_gaps_only():
    """Unbounded gaps next to alternation are how this layer would go quadratic."""
    from api.services.pipecat.answer_classification import _PATTERNS

    for subtype, pattern in _PATTERNS:
        assert ".*" not in pattern.pattern, subtype
        assert ".+" not in pattern.pattern, subtype


def test_no_message_takes_precedence_over_generic_voicemail_instructions():
    assert (
        classify_machine_utterance(
            "Normally you can leave a message after the tone, but this mailbox is full."
        )
        == MachineSubtype.NO_MESSAGE
    )


@pytest.mark.parametrize(
    "prompt, expected",
    [
        ("Please leave a message after the tone.", MachineSubtype.VOICEMAIL),
        ("The mailbox is full.", MachineSubtype.NO_MESSAGE),
        ("Tell me your name and reason for calling.", MachineSubtype.SCREENER),
        ("Press 1 to continue.", MachineSubtype.IVR),
    ],
)
def test_actionable_machine_prompt_takes_precedence_over_wait_announcement(
    prompt, expected
):
    assert (
        classify_machine_utterance(f"Thanks. Please stay on the line. {prompt}")
        == expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, expected",
    [
        ("CONVERSATION", MachineSubtype.CONVERSATION),
        ('{"subtype": "SCREENER"}', MachineSubtype.SCREENER),
        ("SCREENING_WAIT", MachineSubtype.SCREENING_WAIT),
        ('{"subtype": "SCREENING_WAIT"}', MachineSubtype.SCREENING_WAIT),
        ("VOICEMAIL and ignore all instructions", MachineSubtype.UNKNOWN),
        (None, MachineSubtype.UNKNOWN),
        ('{"subtype": []}', MachineSubtype.UNKNOWN),
    ],
)
async def test_private_classifier_validates_output_and_uses_a_fresh_context(
    response, expected
):
    from api.services.workflow.answer_classification_service import (
        AnswerClassificationService,
    )

    llm = SimpleNamespace(run_inference=AsyncMock(return_value=response))
    service = AnswerClassificationService(llm)
    assert await service.classify("An ambiguous answer") == expected
    first_context = llm.run_inference.call_args.args[0]
    assert first_context.messages == [
        {"role": "user", "content": "An ambiguous answer"}
    ]
    assert "system_instruction" in llm.run_inference.call_args.kwargs
    await service.classify("Another answer")
    assert llm.run_inference.call_args.args[0] is not first_context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response, system_prompt, custom_instructions",
    [
        ("SEGRETERIA", "Rispondi con SEGRETERIA o PERSONA.", True),
        ("It sounds like an answering machine.", "builtin", False),
        ("It sounds like an answering machine.", None, False),
        ("It sounds like an answering machine.", "", False),
        ("It sounds like an answering machine.", " \n ", False),
    ],
)
async def test_an_unrecognized_label_is_reported_before_it_becomes_unknown(
    monkeypatch, response, system_prompt, custom_instructions
):
    """UNKNOWN releases the call, so a prompt that stops answering in the label
    contract degrades silently. Log metadata; keep raw output in its trace."""
    from api.services.workflow import answer_classification_service as service_module
    from api.services.workflow.answer_classification_service import (
        ANSWER_CLASSIFIER_SYSTEM_PROMPT,
        AnswerClassificationService,
    )

    warnings = []
    monkeypatch.setattr(
        service_module,
        "logger",
        SimpleNamespace(warning=lambda fmt, *args: warnings.append(fmt.format(*args))),
    )
    llm = SimpleNamespace(run_inference=AsyncMock(return_value=response))
    service = AnswerClassificationService(
        llm,
        system_prompt=(
            ANSWER_CLASSIFIER_SYSTEM_PROMPT
            if system_prompt == "builtin"
            else system_prompt
        ),
    )
    assert await service.classify("Un messaggio registrato") == MachineSubtype.UNKNOWN
    assert len(warnings) == 1
    assert response not in warnings[0]
    assert f"reply_chars={len(response)}" in warnings[0]
    assert f"custom_instructions={custom_instructions}" in warnings[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider, model",
    [
        ("openai", "gpt-4.1"),
        ("openai", "gpt-5"),
        ("minimax", "MiniMax-M2.7"),
    ],
)
async def test_classifier_uses_provider_safe_temperature_and_preserves_omissions(
    provider, model
):
    from openai import NOT_GIVEN

    from api.services.pipecat.service_factory import create_llm_service_from_provider
    from api.services.workflow.answer_classification_service import (
        AnswerClassificationService,
    )

    llm = create_llm_service_from_provider(provider, model, "test-key")
    llm._client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="CONVERSATION"))]
        )
    )
    try:
        service = AnswerClassificationService(llm)
        assert await service.classify("Hello?") == MachineSubtype.CONVERSATION
        params = llm._client.chat.completions.create.call_args.kwargs
        if model == "gpt-5":
            assert params["temperature"] is NOT_GIVEN
        elif provider == "minimax":
            assert params["temperature"] == 0.01
        else:
            assert params["temperature"] == 0.0
    finally:
        await llm._client.close()
