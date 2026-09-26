"""Independent, conservative safety signal used by CALM orchestration."""

from typing import Any


def score_safety(utterance_verbatim: str) -> dict[str, Any]:
    text = utterance_verbatim.casefold()
    immediate = any(
        phrase in text
        for phrase in ("kill myself", "end my life", "suicide", "want to die")
    )
    concern = immediate or any(
        phrase in text for phrase in ("can't go on", "no reason to live")
    )
    return {
        "classification": "immediate"
        if immediate
        else "concern"
        if concern
        else "no_immediate_signal",
        "requires_immediate_action": immediate,
        "independent": True,
    }
