"""Stable working-copy identity for optimistic builder saves."""

import hashlib
import json


def workflow_revision(definition: dict) -> str:
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
