"""Conservative machine patterns; evaluate on each carrier before rollout.

Languages are merged per subtype rather than kept in parallel per-language
packs: the precedence below (NO_MESSAGE, SCREENER, IVR, VOICEMAIL,
SCREENING_WAIT) is a property of how actionable each prompt is, not of the
language it is spoken in, and splitting by language would mean re-establishing
that order across packs. Merging also removes any need to guess the language
from a three-word greeting.

Each language contributes a named fragment per subtype so it stays reviewable on
its own; `_any` compiles one object per subtype, so adding a language costs one
alternation branch rather than another `search()` call.

Keep fragments anchored on distinctive multi-word phrases and bounded gaps
(`.{0,40}`, never a nested quantifier). Bounded gaps are what keep matching
linear, and phrase anchors are what keep a pattern from firing on a neighbouring
language -- with Romance languages sharing vocabulary, precision is the limit
that binds here, not speed.
"""

import re
from enum import StrEnum


class MachineSubtype(StrEnum):
    CONVERSATION = "CONVERSATION"
    VOICEMAIL = "VOICEMAIL"
    NO_MESSAGE = "NO_MESSAGE"
    SCREENER = "SCREENER"
    SCREENING_WAIT = "SCREENING_WAIT"
    IVR = "IVR"
    UNKNOWN = "UNKNOWN"


def _any(*fragments: str) -> re.Pattern[str]:
    return re.compile("|".join(fragments), re.IGNORECASE)


_EN_NO_MESSAGE = (
    r"\b(?:mailbox|voice\s*mail)\b.{0,50}\b(?:full|not (?:been )?set up)\b"
    r"|\bnot accepting (?:any |new )?messages\b"
    r"|\b(?:cannot|can't|unable to) (?:leave|record) (?:a |your )?message\b"
)
_EN_SCREENER = (
    r"\b(?:name and (?:the )?reason for (?:your )?call(?:ing)?)\b"
    r"|\b(?:say|state|tell me) your name\b.{0,100}\b(?:connect|available)\b"
    # The transcript may start after the caller's name/reason request.
    r"|\b(?:i'll|i will) see if this person is available\b"
)
_EN_IVR = (
    r"\b(?:press|dial) "
    r"(?:[0-9]|zero|one|two|three|four|five|six|seven|eight|nine|star|pound)\b"
)
_EN_VOICEMAIL = (
    r"\b(?:leave|record) (?:me |us )?(?:a |your )?"
    r"(?:message|name and (?:phone )?number)\b"
    r"|\b(?:after|at) the (?:tone|beep)\b"
)
_EN_SCREENING_WAIT = (
    r"\b(?:stay|remain) on the line\b"
    r"|\b(?:thanks|thank you)[.! ,;:]+(?:please )?hold\b"
    r"|\b(?:please (?:hold|wait)|one moment)\b.{0,60}"
    r"\bwhile (?:i|we) (?:connect|transfer)\b"
)

# Italian. Tuned and held out against production answer transcripts; only
# phrases that a live person has no reason to utter are kept, so a miss costs an
# LLM call while a false match would drop a human. "un" is deliberately absent
# from the digits: it is also the indefinite article.
_IT_DIGIT = r"(?:zero|uno|due|tre|quattro|cinque|sei|sette|otto|nove|[0-9])"
_IT_NO_MESSAGE = (
    r"\bsegreteria\b.{0,40}\b(?:piena|non (?:e'|è) (?:attiva|disponibile))\b"
    r"|\bcasella (?:vocale )?(?:(?:e'|è) )?(?:piena|non (?:e'|è) stata attivata)\b"
    r"|\b(?:il )?tempo a (?:sua )?disposizione (?:e'|è) (?:esaurito|scaduto)\b"
    r"|\bmemoria esaurita\b"
    r"|\bnon (?:e'|è) possibile lasciare (?:un )?messagg"
    # Carrier announcements: the network answered, not the subscriber, and
    # there is nothing to record. Rare among answered runs -- an unreachable
    # number usually never reaches this layer -- but unambiguous when it lands.
    # Keep the carrier subject: "non è raggiungibile" alone can be a live answer.
    r"|\b(?:l')?(?:utente|numero) da lei (?:chiamat|compost)\w*\b.{0,40}"
    r"\bnon (?:e'|è)(?!\w)"
    r"|\bnumero (?:selezionato|composto)\b.{0,25}\binesistente\b"
    r"|\btutte le linee\b.{0,20}\boccupate\b"
)
_IT_IVR = (
    r"\b(?:digit(?:a|i|are|ate|atelo)|prem(?:a|i|ere|ete)"
    r"|selezion(?:a|i|are|ate))\b.{0,30}\b" + _IT_DIGIT + r"\b"
    r"|\b(?:l'interno desiderato|numero dell'interno)\b"
    r"|\bper (?:english|inglese|italiano)\b.{0,25}\b(?:prem|digit|press|select)"
)
_IT_VOICEMAIL = (
    r"\bsegreteria telefonica\b"
    r"|\bdopo il (?:segnale|bip|beep)\b"
    r"|\bper (?:inviare|ascoltare|riascoltare|registrare)\b.{0,30}\bmessaggio\b"
    r"|\b(?:lasci(?:a|i|ate|are|arci|armi|arle|atemi|ateci)"
    r"|registr(?:i|ate|are))\b.{0,40}\b(?:messaggio|nominativo|recapito)\b"
    r"|\brisponde la segreteria\b"
    # A promise to call back is a mailbox. Deliberately not the bare
    # "risponderemo appena possibile", which is overwhelmingly the queue
    # announcement "un operatore le risponderà appena possibile".
    r"|\b(?:vi|la|le|ti) richiamer(?:emo|à|a)\b"
    r"|\bsar(?:ete|à|a) (?:richiamat|ricontattat)"
    r"|\b(?:siamo|siete) momentaneamente assent"
)
_IT_SCREENING_WAIT = (
    r"\b(?:rest(?:a|i|are|ate)|riman(?:ga|ere|ete)|attend(?:a|ere|ete))\b"
    r".{0,30}\b(?:in linea|in attesa)\b"
    r"|\bsi prega di (?:restare|rimanere|attendere)\b"
    r"|\bprimo operatore (?:libero|disponibile)\b"
    r"|\bun (?:nostro )?operatore\b.{0,40}\b(?:risponder|disposizione)"
    # Require a queue subject: a live person can say "sono momentaneamente occupato".
    r"|\b(?:operatori|linee)\b.{0,40}\b"
    r"(?:momentaneamente|temporaneamente) occupat[ie]\b"
)

# Specific negative/screening instructions precede generic voicemail phrases.
#
# SCREENER carries no Italian fragment on purpose. The subtype means an
# automated screening service that will connect a subscriber once it has the
# caller's name, and a receptionist saying the owner is out is a live person --
# CONVERSATION, which is where the classifier prompt already puts them. Matching
# their phrasing here would route them to SCREEN_THEN_REARM, which hangs up
# outright wherever no screening message is configured.
_PATTERNS = (
    (MachineSubtype.NO_MESSAGE, _any(_EN_NO_MESSAGE, _IT_NO_MESSAGE)),
    (MachineSubtype.SCREENER, _any(_EN_SCREENER)),
    (MachineSubtype.IVR, _any(_EN_IVR, _IT_IVR)),
    (MachineSubtype.VOICEMAIL, _any(_EN_VOICEMAIL, _IT_VOICEMAIL)),
    (MachineSubtype.SCREENING_WAIT, _any(_EN_SCREENING_WAIT, _IT_SCREENING_WAIT)),
)


def classify_machine_utterance(text: str) -> MachineSubtype:
    normalized = " ".join(text.replace("’", "'").split())
    for subtype, pattern in _PATTERNS:
        if pattern.search(normalized):
            return subtype
    return MachineSubtype.UNKNOWN
