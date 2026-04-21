"""Base class for generated per-node-type dataclasses.

The typed SDK (`dograh_sdk.typed`) contains one generated dataclass per
node spec. Each subclass declares its spec name as a class-level `type`
and carries fields mirroring the spec's properties — giving IDEs full
autocomplete, docstrings on hover, and mypy/pyright coverage.

At runtime the typed objects feed into `Workflow.add_typed(node)`, which
unpacks them into the same kwargs the generic `add()` already accepts.
Wire format and validation rules are unchanged — typed SDK is an
ergonomic layer, not a second validator.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, ClassVar


@dataclass(kw_only=True)
class TypedNode:
    """Common base for every generated typed node class.

    Subclasses override `type` with their spec name (e.g. `"startCall"`).
    Subclasses should be declared with `@dataclass(kw_only=True)` so
    required fields can appear after optional ones without triggering
    Python's default-ordering rule.
    """

    # Overridden per subclass via dataclass inheritance + ClassVar shadowing.
    type: ClassVar[str] = ""

    def to_dict(self) -> dict[str, Any]:
        """Dataclass fields as a plain dict, suitable for feeding directly
        into `Workflow.add(type=..., **kwargs)`.

        `type` is a ClassVar and is NOT included — the caller passes it
        separately.

        Fields with "unset" sentinels (`None`, empty list) are filtered
        out so the output matches what `Workflow.add(**kwargs)` would
        produce when the user omits them. Downstream validation applies
        spec defaults for absent keys.
        """
        raw = asdict(self)
        return {
            k: v for k, v in raw.items()
            if v is not None and v != []
        }
