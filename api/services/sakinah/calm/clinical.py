"""Context-only clinical evaluation boundary.

This intentionally reports observable conversation context rather than a
diagnosis or risk decision.  Safety remains a separate system.
"""

from typing import Any, Mapping


def evaluate_clinical_context(
    utterance_verbatim: str,
    calm_scores: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    low_scores = [
        name for name, value in calm_scores.items() if (value.get("score") or 0) <= 3
    ]
    return {
        "context": "distress indicators present"
        if low_scores
        else "no prominent low protective indicators",
        "low_protective_dimensions": low_scores,
        "utterance_length": len(utterance_verbatim),
        "diagnosis": None,
    }
