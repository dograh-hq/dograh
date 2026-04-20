"""Opt-in marker for exposing a FastAPI route through the Dograh SDK.

The generated SDK client (`sdk/python/src/dograh_sdk/_generated_client.py`
and the TypeScript equivalent) is built by walking the backend's OpenAPI
schema and picking up any operation tagged with `x-sdk-method`. That
means `generate_sdk.sh` stays in sync with the real HTTP paths — no more
hand-typed URL strings drifting out of date.

Usage:

    from api.sdk_expose import sdk_expose

    @router.post("/initiate-call", **sdk_expose(
        method="test_phone_call",
        description="Place a test call from a workflow to a phone number.",
    ))
    async def initiate_call(...): ...

Anything not wrapped in `sdk_expose` is invisible to the SDK — deliberate,
so the SDK surface stays small and auditable.
"""

from __future__ import annotations

from typing import Any


def sdk_expose(*, method: str, description: str = "") -> dict[str, Any]:
    """Return FastAPI route kwargs that tag the operation for SDK codegen.

    `method` becomes the SDK method name in both Python and TypeScript
    (converted to snake_case / camelCase as appropriate by the codegen).
    `description` is emitted as the method docstring.
    """
    extra: dict[str, Any] = {"x-sdk-method": method}
    if description:
        extra["x-sdk-description"] = description
    return {"openapi_extra": extra}
