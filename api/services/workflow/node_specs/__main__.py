"""Dump the registered NodeSpecs to stdout as JSON.

Used by `scripts/generate_sdk.sh` to feed both SDK codegens without
requiring a running backend. Shape matches the `/api/v1/node-types`
HTTP response so either source is interchangeable.

    python -m api.services.workflow.node_specs > specs.json
"""

from __future__ import annotations

import json
import sys

from api.services.workflow.node_specs import SPEC_VERSION, all_specs


def main() -> None:
    payload = {
        "spec_version": SPEC_VERSION,
        "node_types": [s.model_dump(mode="json") for s in all_specs()],
    }
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
