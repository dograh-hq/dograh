"""Dump the FastAPI OpenAPI spec to docs/api-reference/openapi.json.

Run from the repo root with the api environment available:

    python -m scripts.dump_docs_openapi

CI uses this to detect drift: it dumps the spec and asserts the file is
unchanged versus what's checked in.
"""

import json
from pathlib import Path

from loguru import logger

logger.remove()

from fastapi.openapi.utils import get_openapi  # noqa: E402

from api.app import app  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "docs" / "api-reference" / "openapi.json"


def main() -> None:
    spec = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        servers=app.servers,
    )
    OUTPUT.write_text(json.dumps(spec, separators=(",", ":")))
    print(f"Wrote {len(spec['paths'])} paths to {OUTPUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
