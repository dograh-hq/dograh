"""Bound trace payloads without emitting broken JSON."""

import json
from typing import Any


def trace_json(value: Any) -> str:
    encoded = json.dumps(value, default=str, ensure_ascii=False)
    # Keep normal workflow source and previews intact. Oversized payloads remain
    # readable JSON and explicitly advertise that only a preview was recorded.
    if len(encoded.encode("utf-8")) <= 256_000:
        return encoded
    return json.dumps(
        {
            "truncated": True,
            "original_chars": len(encoded),
            "preview": encoded[:32_000],
        },
        ensure_ascii=False,
    )
