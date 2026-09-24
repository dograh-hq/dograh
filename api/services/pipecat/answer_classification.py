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
    r"|\b(?:questa )?segreteria non prende\b"
    r"|\bnumero\b.{0,25}\bnon (?:e'|è) (?:(?:piu'|più) )?(?:attivo|in uso|corretto)\b"
    r"|\b(?:e'|è) occupato e non (?:puo'|può) ricevere\b"
)
_IT_IVR = (
    r"\b(?:digit(?:a|i|are|ate|atelo)|prem(?:a|i|ere|ete)"
    r"|selezion(?:a|i|are|ate))\b.{0,30}\b" + _IT_DIGIT + r"\b"
    r"|\b(?:l'interno desiderato|numero dell'interno)\b"
    r"|\bper (?:english|inglese|italiano)\b.{0,25}\b(?:prem|digit|press|select)"
    # Italian keypad symbols; the digit alternation above cannot reach them.
    r"|\b(?:prem(?:a|i|ere|ete)|digit(?:a|i|are|ate))\b.{0,30}"
    r"\b(?:cancelletto|asterisco|cancellato)\b"
    r"|\bseguito da (?:cancelletto|asterisco|cancellato)\b"
    r"|\b(?:digit\w+|selezion\w+|compon\w+|prem\w+)\b.{0,20}\bl'interno\b"
    r"|\bselezion(?:a|i|are|ate|ene)\b.{0,25}\b(?:una delle )?(?:seguenti )?opzion"
    r"|\bcodice di accesso\b|\binserire (?:il )?codice\b"
    r"|\bper il men(?:u'|ù|u)\b"
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
    # Recorded "we cannot take your call" announcements. 'attenti' is how the
    # 8 kHz STT renders 'assenti'; it is not a word anyone answers a phone with.
    r"|\b(?:al momento|in questo momento|momentaneamente)\b.{0,25}"
    r"\bnon (?:siamo|sono|possiamo|posso|(?:e'|è))(?!\w).{0,20}"
    r"(?:raggiungibil|disponibil|rispond|attiv)"
    r"|\bnon (?:possiamo|posso) rispond(?:ere|ervi|erle|erti)\b"
    r"|\b(?:siamo|siete|stiamo|sono) momentaneamente (?:assent|atten)"
    r"|\bnon (?:puo'|può) (?:prendere|rispondere a) la (?:sua |vostra )?chiamata\b"
    r"|\bsiamo spiacenti\b"
    r"|\bimpossibilitat\w+ a rispond"
    # Closure and opening-hours announcements: nobody is coming to the phone.
    # A bare "gli uffici sono chiusi" is a live employee and stays with the
    # classifier; only the recorded first-person register is matched here.
    r"|\b(?:i )?nostri uffici\b.{0,25}\b(?:sono |(?:e'|è) )?(?:chius|apert)"
    r"|\bchius[oi] per (?:ferie|il periodo|la |le )"
    r"|\borar(?:io|i) di apertura\b"
    r"|\bapert\w+\b.{0,20}"
    r"\bdal(?:le)? (?:luned|marted|mercoled|gioved|venerd|sabato|domenica)"
    r"|\briapr(?:iamo|ir(?:a'|à)|iremo|e)\b"
    r"|\b(?:la|vi|le) (?:preghiamo|invitiamo) di (?:ri)?(?:chiamare|contattarci|contattare)\b"
    # The 440 ms deaf window clips the head of "risponde la segreteria
    # telefonica"; the surviving tail arrives as a whole utterance of its own.
    r"|^(?:via )?telefonica[.,!?]*$"
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
    r"|\b(?:preghiamo|prega|invitiamo|invito) di (?:attendere|rimanere|restare)\b"
    r"|\battend(?:a|ere|ete)\b[ ,]{0,2}prego\b|\bprego\b[ ,]{0,2}attend"
    r"|\b(?:l'|un |il primo |primo )operatore\b.{0,40}"
    r"\b(?:a sua disposizione|sar(?:a'|à)|(?:le |vi )?risponder)"
    r"|\b(?:in attesa di essere|per essere|state per essere|stiamo per) "
    r"(?:collegat|messi in contatto|rispondere)"
    r"|\bverr(?:ete|(?:a'|à)|ai) (?:subito )?(?:messi in contatto|collegat|rispost)"
    r"|\bposizione\b.{0,15}\b(?:in )?(?:questa )?coda\b|\bprima in (?:vista|coda)\b"
    r"|\b(?:stiamo )?trasferendo la (?:tua|sua|vostra) chiamata\b"
    r"|\b(?:vi|la|lo) stiamo trasferendo\b"
    r"|\bla mettiamo in comunicazione\b"
    r"|\bricerca della persona\b"
    r"|\bnel (?:piu'|più) breve tempo possibile\b"
    r"|\battend(?:a|ete) (?:solo )?qualche istante\b"
    r"|\b(?:la linea|le linee) (?:(?:e'|è)|sono) (?:momentaneamente )?occupat"
)

# A recorded switchboard identification says a machine picked up; it does not
# say no one is behind it. On 345 such answers 12% reached a live person within
# seconds, so this waits rather than dropping -- and it is tested last, so an
# identification followed by a mailbox prompt or a closure notice still lands on
# the more actionable subtype above.
_IT_RECORDED_ID = (
    r"\b(?:siete|siamo) in linea con\b"
    r"|\bin linea con (?:la |il |lo |l'|i |gli |le )?\w"
    r"|\b(?:siete|sei) (?:collegat|conness)\w* con\b"
    r"|\bbenvenut[oi]\b[ ,]{0,2}(?:in|a|al|alla|allo|ai|agli|alle|da|nel|nella)\b"
    r"|\b(?:vi|le|ti|la) d(?:a'|[aà]) il benvenuto\b"
    r"|\bgrazie (?:per|di) aver(?:ci)? (?:chiamat|contattat|telefonat)"
    r"|\bper aver(?:ci)? (?:chiamat|contattat)"
    r"|\brisponde (?:la|il|lo|l')\b"
    r"|^(?:ben)?venut[oi][.,!?]*$"
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
    (
        MachineSubtype.SCREENING_WAIT,
        _any(_EN_SCREENING_WAIT, _IT_SCREENING_WAIT, _IT_RECORDED_ID),
    ),
)


def classify_machine_utterance(text: str) -> MachineSubtype:
    normalized = " ".join(text.replace("’", "'").split())
    for subtype, pattern in _PATTERNS:
        if pattern.search(normalized):
            return subtype
    return MachineSubtype.UNKNOWN
