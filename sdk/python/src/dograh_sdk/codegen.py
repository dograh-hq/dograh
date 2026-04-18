"""Typed SDK code generator.

Reads NodeSpecs (from the live backend, a JSON file, or the in-process
registry) and emits a dataclass per node type into an output directory.
The generated files live under `dograh_sdk.typed` and are committed to
the repository so `pip install dograh-sdk` ships typed classes without
requiring a regen step.

Run manually:

    python -m dograh_sdk.codegen --api http://localhost:8000 \\
        --out sdk/python/src/dograh_sdk/typed

    python -m dograh_sdk.codegen --input specs.json \\
        --out ./my_typed
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

# ── property type → Python type annotation ────────────────────────────────

_SCALAR_PY_TYPES = {
    "string": "str",
    "number": "float",
    "boolean": "bool",
    "json": "dict[str, Any]",
    "mention_textarea": "str",
    "url": "str",
    "recording_ref": "str",
    "credential_ref": "str",
    "tool_refs": "list[str]",
    "document_refs": "list[str]",
}


def _snake_to_camel(name: str) -> str:
    """`start_call` → `StartCall` (class-name case)."""
    return "".join(part.capitalize() or "_" for part in name.split("_"))


def _spec_class_name(spec_name: str) -> str:
    # startCall → StartCall; agentNode → AgentNode; qa → Qa
    if not spec_name:
        return "Node"
    return spec_name[0].upper() + spec_name[1:]


def _safe_py_repr(value: Any) -> str:
    """Render a JSON-serializable value as a Python literal."""
    return repr(value)


def _py_type_for(prop: dict[str, Any], owner_class_name: str) -> tuple[str, str]:
    """Return (type_annotation, default_source) for one property.

    Defaults are expressed as source code — e.g., `"Start Call"`, `False`,
    `field(default_factory=list)`, etc. An empty string means "no default"
    (the field is required for the dataclass).
    """
    t = prop["type"]
    required = bool(prop.get("required"))
    has_spec_default = prop.get("default") is not None

    # Compound types first
    if t == "options":
        options = prop.get("options") or []
        literals = ", ".join(repr(o["value"]) for o in options)
        annotation = f"Literal[{literals}]" if literals else "str"
    elif t == "multi_options":
        options = prop.get("options") or []
        literals = ", ".join(repr(o["value"]) for o in options)
        inner = f"Literal[{literals}]" if literals else "str"
        annotation = f"list[{inner}]"
    elif t == "fixed_collection":
        row_class = f"{owner_class_name}_{_spec_class_name(prop['name'])}Row"
        annotation = f"list[{row_class}]"
    else:
        annotation = _SCALAR_PY_TYPES.get(t, "Any")

    # Required fields without a spec default get no dataclass default
    # (the user must set them). Optional fields default to None if the
    # spec doesn't declare anything, or to the spec's default literal.
    if has_spec_default:
        spec_default = prop["default"]
        if isinstance(spec_default, (dict, list, set)):
            # Mutable defaults require default_factory — can't appear
            # inline on a dataclass field.
            default_src = f"field(default_factory=lambda: {spec_default!r})"
        else:
            default_src = _safe_py_repr(spec_default)
    elif required:
        default_src = ""  # no default — caller must pass a value
    elif t == "multi_options" or t == "fixed_collection" or t in (
        "tool_refs",
        "document_refs",
    ):
        default_src = "field(default_factory=list)"
    else:
        default_src = "None"
        annotation = f"Optional[{annotation}]"

    return annotation, default_src


def _format_docstring(text: str, indent: int = 4) -> str:
    """Wrap a description into a triple-quoted docstring."""
    pad = " " * indent
    wrapped = textwrap.fill(
        text.strip(),
        width=76,
        initial_indent=pad,
        subsequent_indent=pad,
    )
    return f'{pad}"""\n{wrapped}\n{pad}"""'


# ── source rendering ─────────────────────────────────────────────────────

_FILE_HEADER = '''"""GENERATED — do not edit by hand.

Regenerate with `python -m dograh_sdk.codegen` against the target
Dograh backend. Source of truth: each node's NodeSpec in the backend's
`api/services/workflow/node_specs/` directory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Optional

from dograh_sdk.typed._base import TypedNode
'''


def _render_nested_row_dataclass(
    owner_class_name: str,
    parent_prop: dict[str, Any],
) -> str:
    row_class = f"{owner_class_name}_{_spec_class_name(parent_prop['name'])}Row"
    props = parent_prop.get("properties") or []
    lines = [f"@dataclass(kw_only=True)", f"class {row_class}:"]

    desc = parent_prop.get("description") or "Row in " + parent_prop["name"]
    lines.append(_format_docstring(desc))
    lines.append("")

    if not props:
        lines.append("    pass")
        return "\n".join(lines)

    for sub in props:
        annotation, default_src = _py_type_for(sub, row_class)
        if default_src:
            lines.append(f"    {sub['name']}: {annotation} = {default_src}")
        else:
            lines.append(f"    {sub['name']}: {annotation}")
        if sub.get("description"):
            lines.append(_format_docstring(sub["description"]))
    return "\n".join(lines)


def _render_spec_class(spec: dict[str, Any]) -> str:
    class_name = _spec_class_name(spec["name"])
    lines: list[str] = []

    # Emit nested row dataclasses first so the main class can reference them.
    nested_rendered: list[str] = []
    for prop in spec.get("properties", []):
        if prop["type"] == "fixed_collection":
            nested_rendered.append(
                _render_nested_row_dataclass(class_name, prop)
            )
    lines.extend(nested_rendered)
    if nested_rendered:
        lines.append("")

    lines.append("@dataclass(kw_only=True)")
    lines.append(f"class {class_name}(TypedNode):")

    # Class docstring: description + optional llm_hint
    description = spec.get("description") or ""
    llm_hint = spec.get("llm_hint")
    doc_text = description
    if llm_hint:
        doc_text = f"{description}\n\nLLM hint: {llm_hint}"
    lines.append(_format_docstring(doc_text))
    lines.append("")

    # Spec-name discriminator
    lines.append(f'    type: ClassVar[str] = {spec["name"]!r}')
    lines.append("")

    # Split fields into "has default" and "required-no-default" so we can
    # emit required ones first (dataclass rule, even though we use
    # kw_only=True — still cleaner output).
    with_defaults: list[tuple[dict, str, str]] = []
    without_defaults: list[tuple[dict, str]] = []

    for prop in spec.get("properties", []):
        annotation, default_src = _py_type_for(prop, class_name)
        if default_src:
            with_defaults.append((prop, annotation, default_src))
        else:
            without_defaults.append((prop, annotation))

    for prop, annotation in without_defaults:
        lines.append(f"    {prop['name']}: {annotation}")
        if prop.get("description"):
            lines.append(_format_docstring(prop["description"]))
        lines.append("")

    for prop, annotation, default_src in with_defaults:
        lines.append(f"    {prop['name']}: {annotation} = {default_src}")
        if prop.get("description"):
            lines.append(_format_docstring(prop["description"]))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_init_module(spec_names: list[str]) -> str:
    lines = [
        '"""GENERATED — do not edit by hand.',
        "",
        "Re-exports every typed node class so users can write",
        "`from dograh_sdk.typed import StartCall, AgentNode`.",
        '"""',
        "",
    ]
    exports: list[str] = []
    for spec_name in sorted(spec_names):
        module_name = re.sub(r"(?<!^)(?=[A-Z])", "_", spec_name).lower()
        # Handle abbreviations (qa, webhook, trigger, etc.): no underscore needed.
        class_name = _spec_class_name(spec_name)
        lines.append(f"from dograh_sdk.typed.{module_name} import {class_name}")
        exports.append(class_name)

    lines.append("from dograh_sdk.typed._base import TypedNode")
    exports.append("TypedNode")
    lines.append("")
    lines.append("__all__ = [")
    for name in sorted(exports):
        lines.append(f'    "{name}",')
    lines.append("]")
    lines.append("")
    return "\n".join(lines)


def _module_name_for(spec_name: str) -> str:
    """startCall → start_call.py; agentNode → agent_node.py."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", spec_name).lower()


# ── public entry points ──────────────────────────────────────────────────


def generate_all(specs: list[dict[str, Any]], out_dir: Path) -> None:
    """Emit typed dataclasses for every spec into `out_dir`.

    Preserves `out_dir/_base.py` if present (it's hand-written). Writes
    one `<spec>.py` file per spec and regenerates `__init__.py`.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    for spec in specs:
        module_name = _module_name_for(spec["name"])
        source = _FILE_HEADER + "\n\n" + _render_spec_class(spec) + "\n"
        (out_dir / f"{module_name}.py").write_text(source)

    (out_dir / "__init__.py").write_text(
        _render_init_module([s["name"] for s in specs])
    )


def _load_specs_from_json(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text())
    if isinstance(raw, dict) and "node_types" in raw:
        return raw["node_types"]
    if isinstance(raw, list):
        return raw
    raise SystemExit(f"{path}: expected list or {{node_types: [...]}}")


def _load_specs_from_api(base_url: str) -> list[dict[str, Any]]:
    import httpx

    resp = httpx.get(
        f"{base_url.rstrip('/')}/api/v1/node-types",
        timeout=30.0,
    )
    resp.raise_for_status()
    body = resp.json()
    return body.get("node_types", [])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m dograh_sdk.codegen",
        description="Generate typed SDK dataclasses from the Dograh node-spec catalog.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--api", help="Dograh backend base URL")
    source.add_argument("--input", help="Local JSON file with specs")
    parser.add_argument(
        "--out", required=True, help="Output directory for generated modules"
    )
    args = parser.parse_args(argv)

    if args.api:
        specs = _load_specs_from_api(args.api)
    else:
        specs = _load_specs_from_json(Path(args.input))

    out_dir = Path(args.out)
    generate_all(specs, out_dir)

    print(
        f"Generated {len(specs)} typed node modules "
        f"({', '.join(s['name'] for s in specs)}) into {out_dir}"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
