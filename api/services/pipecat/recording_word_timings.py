"""Reusable estimated word boundaries for prepared recordings."""

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def estimate_word_ends(text: str, duration: float) -> tuple[tuple[int, float], ...]:
    """Return original-text end offsets and estimated audio end times."""
    words = list(re.finditer(r"\S+", text))
    if not words or duration <= 0:
        return ()
    weights = [max(1, sum(char.isalnum() for char in word.group())) for word in words]
    total = sum(weights)
    cumulative = 0
    ends = []
    for word, weight in zip(words, weights):
        cumulative += weight
        ends.append((word.end(), duration * cumulative / total))
    return tuple(ends)


def recording_prefix(text: str, duration: float, played: float) -> str:
    end = 0
    for offset, word_end in estimate_word_ends(text, duration):
        if word_end > played + 1e-9:
            break
        end = offset
    return text[:end].strip()
