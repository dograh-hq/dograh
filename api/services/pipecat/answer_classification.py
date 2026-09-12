"""Conservative English machine patterns; evaluate on each carrier before rollout."""

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


# Specific negative/screening instructions precede generic voicemail phrases.
_PATTERNS = (
    (
        MachineSubtype.NO_MESSAGE,
        re.compile(
            r"\b(?:mailbox|voice\s*mail)\b.{0,50}\b(?:full|not (?:been )?set up)\b"
            r"|\bnot accepting (?:any |new )?messages\b"
            r"|\b(?:cannot|can't|unable to) (?:leave|record) (?:a |your )?message\b",
            re.IGNORECASE,
        ),
    ),
    (
        MachineSubtype.SCREENER,
        re.compile(
            r"\b(?:name and (?:the )?reason for (?:your )?call(?:ing)?)\b"
            r"|\b(?:say|state|tell me) your name\b.{0,100}\b(?:connect|available)\b"
            # The transcript may start after the caller's name/reason request.
            r"|\b(?:i'll|i will) see if this person is available\b",
            re.IGNORECASE,
        ),
    ),
    (
        MachineSubtype.IVR,
        re.compile(
            r"\b(?:press|dial) (?:[0-9]|zero|one|two|three|four|five|six|seven|eight|nine|star|pound)\b",
            re.IGNORECASE,
        ),
    ),
    (
        MachineSubtype.VOICEMAIL,
        re.compile(
            r"\b(?:leave|record) (?:me |us )?(?:a |your )?(?:message|name and (?:phone )?number)\b"
            r"|\b(?:after|at) the (?:tone|beep)\b",
            re.IGNORECASE,
        ),
    ),
    (
        MachineSubtype.SCREENING_WAIT,
        re.compile(
            r"\b(?:stay|remain) on the line\b"
            r"|\b(?:thanks|thank you)[.! ,;:]+(?:please )?hold\b"
            r"|\b(?:please (?:hold|wait)|one moment)\b.{0,60}"
            r"\bwhile (?:i|we) (?:connect|transfer)\b",
            re.IGNORECASE,
        ),
    ),
)


def classify_machine_utterance(text: str) -> MachineSubtype:
    normalized = " ".join(text.replace("’", "'").split())
    for subtype, pattern in _PATTERNS:
        if pattern.search(normalized):
            return subtype
    return MachineSubtype.UNKNOWN
