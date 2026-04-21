"""Generate SDK client mixins (Python + TypeScript) from a filtered OpenAPI dump.

Input: a spec produced by calling FastAPI's `get_openapi(routes=...)` with
only the routes tagged via `sdk_expose(...)`. Because it's already filtered,
this script does *no* filtering — it just walks the operations and emits
typed method stubs.

Request/response types come from sibling model files already produced by
`datamodel-codegen` (Python) and `openapi-typescript --root-types
--root-types-no-schema-prefix` (TypeScript). We only import the names
here; this script doesn't generate types itself.

Output:
    --py-out  sdk/python/src/dograh_sdk/_generated_client.py
    --ts-out  sdk/typescript/src/_generated_client.ts
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_API_PREFIX = "/api/v1"

# openapi scalar → (python, typescript)
_TYPE_MAP = {
    "integer": ("int", "number"),
    "number": ("float", "number"),
    "string": ("str", "string"),
    "boolean": ("bool", "boolean"),
}


def _map_scalar(schema: dict[str, Any]) -> tuple[str, str]:
    t = schema.get("type")
    if t in _TYPE_MAP:
        return _TYPE_MAP[t]
    # optional string often shown as anyOf:[{type:string}, {type:null}]
    for branch in schema.get("anyOf") or []:
        if branch.get("type") in _TYPE_MAP and branch.get("type") != "null":
            return _TYPE_MAP[branch["type"]]
    return ("Any", "unknown")


def _ref_name(schema: dict[str, Any]) -> str | None:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        return ref.rsplit("/", 1)[-1]
    return None


@dataclass
class ResponseType:
    """What comes back from an operation. `class_name` is a model class from
    `_generated_models`; `is_list` wraps it as a list."""
    class_name: str | None = None
    is_list: bool = False

    @property
    def py(self) -> str:
        if self.class_name is None:
            return "Any"
        return f"list[{self.class_name}]" if self.is_list else self.class_name

    @property
    def ts(self) -> str:
        if self.class_name is None:
            return "unknown"
        return f"{self.class_name}[]" if self.is_list else self.class_name


@dataclass
class Param:
    name: str
    py_type: str
    ts_type: str
    required: bool


@dataclass
class Operation:
    method: str
    verb: str
    path: str
    description: str
    path_params: list[Param] = field(default_factory=list)
    query_params: list[Param] = field(default_factory=list)
    request_class: str | None = None        # None → no body
    response: ResponseType = field(default_factory=ResponseType)


def _collect(spec: dict[str, Any]) -> list[Operation]:
    ops: list[Operation] = []
    used_models: set[str] = set()

    for path, methods in spec.get("paths", {}).items():
        for verb, op in methods.items():
            if not isinstance(op, dict) or "x-sdk-method" not in op:
                continue

            description = (op.get("x-sdk-description") or op.get("summary") or "").strip()
            sdk_path = path[len(_API_PREFIX):] if path.startswith(_API_PREFIX) else path

            path_params: list[Param] = []
            query_params: list[Param] = []
            for p in op.get("parameters") or []:
                py_t, ts_t = _map_scalar(p.get("schema") or {})
                param = Param(
                    name=p["name"],
                    py_type=py_t,
                    ts_type=ts_t,
                    required=bool(p.get("required")),
                )
                if p.get("in") == "path":
                    path_params.append(param)
                elif p.get("in") == "query":
                    query_params.append(param)

            request_class: str | None = None
            rb = op.get("requestBody") or {}
            rb_schema = (
                (rb.get("content") or {}).get("application/json", {}).get("schema") or {}
            )
            if rb_schema:
                request_class = _ref_name(rb_schema)

            response = ResponseType()
            r200 = (
                op.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema")
                or {}
            )
            if r200:
                name = _ref_name(r200)
                if name:
                    response = ResponseType(class_name=name)
                elif r200.get("type") == "array":
                    item = r200.get("items") or {}
                    name = _ref_name(item)
                    if name:
                        response = ResponseType(class_name=name, is_list=True)

            for cls in (request_class, response.class_name):
                if cls:
                    used_models.add(cls)

            ops.append(Operation(
                method=op["x-sdk-method"],
                verb=verb.lower(),
                path=sdk_path,
                description=description,
                path_params=path_params,
                query_params=query_params,
                request_class=request_class,
                response=response,
            ))

    ops.sort(key=lambda o: o.method)
    return ops, sorted(used_models)


# ── Python emitter ─────────────────────────────────────────────────────


def _py_method(op: Operation) -> str:
    positional = [f"{p.name}: {p.py_type}" for p in op.path_params]
    kw_only: list[str] = []
    if op.request_class:
        kw_only.append(f"body: {op.request_class}")
    for p in op.query_params:
        kw_only.append(f"{p.name}: {p.py_type} | None = None")

    sig = ", ".join(["self", *positional] + (["*"] + kw_only if kw_only else []))

    lines: list[str] = []
    lines.append(f"    def {op.method}({sig}) -> {op.response.py}:")
    lines.append(f'        """{op.description or op.verb.upper() + " " + op.path}"""')

    path_expr = f'f"{op.path}"' if op.path_params else f'"{op.path}"'

    call_kwargs: list[str] = []
    if op.query_params:
        lines.append("        params: dict[str, Any] = {}")
        for p in op.query_params:
            lines.append(f"        if {p.name} is not None:")
            lines.append(f'            params["{p.name}"] = {p.name}')
        call_kwargs.append("params=params")
    if op.request_class:
        call_kwargs.append('json=body.model_dump(mode="json", exclude_none=True)')

    extra = (", " + ", ".join(call_kwargs)) if call_kwargs else ""
    raw_call = f'self._request("{op.verb.upper()}", {path_expr}{extra})'

    if op.response.class_name is None:
        lines.append(f"        return {raw_call}")
    elif op.response.is_list:
        lines.append(f"        data = {raw_call}")
        lines.append(f"        return [{op.response.class_name}.model_validate(x) for x in data]")
    else:
        lines.append(f"        data = {raw_call}")
        lines.append(f"        return {op.response.class_name}.model_validate(data)")

    lines.append("")
    return "\n".join(lines)


_PY_HEADER = '''\
"""GENERATED — do not edit. Source: filtered OpenAPI from `api.app`.

Regenerate with `./scripts/generate_sdk.sh`.

`DograhClient` mixes in this class to get HTTP methods for every route
decorated with `sdk_expose(...)` on the backend. Request/response types
come from `_generated_models` (datamodel-codegen output).
"""

from __future__ import annotations

from typing import Any

from dograh_sdk._generated_models import (
{imports}
)


class _GeneratedClient:
    # `DograhClient.__init__` installs `self._request` (see client.py).

'''


def emit_python(ops: list[Operation], models: list[str]) -> str:
    imports = "\n".join(f"    {m}," for m in models)
    body = "\n".join(_py_method(op) for op in ops)
    return _PY_HEADER.format(imports=imports) + body


# ── TypeScript emitter ─────────────────────────────────────────────────


def _snake_to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def _ts_method(op: Operation) -> str:
    name = _snake_to_camel(op.method)
    positional = [f"{_snake_to_camel(p.name)}: {p.ts_type}" for p in op.path_params]

    opts_props: list[str] = []
    if op.request_class:
        opts_props.append(f"body: {op.request_class}")
    for p in op.query_params:
        opts_props.append(f"{_snake_to_camel(p.name)}?: {p.ts_type}")

    args = list(positional)
    if opts_props:
        required_in_opts = op.request_class is not None
        opts_sig = "{ " + "; ".join(opts_props) + " }"
        # If body is required, opts is required too (no `= {}` default)
        args.append(f"opts: {opts_sig}" if required_in_opts else f"opts: {opts_sig} = {{}}")

    sig = ", ".join(args)
    ret = op.response.ts

    lines: list[str] = []
    lines.append(f"    /** {op.description or op.verb.upper() + ' ' + op.path} */")
    lines.append(f"    async {name}({sig}): Promise<{ret}> {{")

    path_expr = op.path
    for p in op.path_params:
        path_expr = path_expr.replace("{" + p.name + "}", "${" + _snake_to_camel(p.name) + "}")
    tmpl = f"`{path_expr}`" if op.path_params else f'"{op.path}"'

    call_opts: list[str] = []
    if op.query_params:
        entries: list[str] = []
        for p in op.query_params:
            camel = _snake_to_camel(p.name)
            entries.append(f'            ...(opts.{camel} !== undefined ? {{ "{p.name}": opts.{camel} }} : {{}}),')
        lines.append("        const params: Record<string, unknown> = {")
        lines.extend(entries)
        lines.append("        };")
        call_opts.append("params")
    if op.request_class:
        call_opts.append("json: opts.body")

    extra = (", { " + ", ".join(call_opts) + " }") if call_opts else ""
    generic = f"<{ret}>" if ret != "unknown" else ""
    lines.append(f'        return this.request{generic}("{op.verb.upper()}", {tmpl}{extra});')
    lines.append("    }")
    lines.append("")
    return "\n".join(lines)


_TS_HEADER = """\
// GENERATED — do not edit. Source: filtered OpenAPI from `api.app`.
//
// Regenerate with `./scripts/generate_sdk.sh`.
//
// `DograhClient` extends this base to get HTTP methods for every route
// decorated with `sdk_expose(...)`. Request/response types come from
// `_generated_models` (openapi-typescript output, --root-types).

import type {{
{imports}
}} from "./_generated_models.js";

export abstract class _GeneratedClient {{
    protected abstract request<T = unknown>(
        method: string,
        path: string,
        opts?: {{ json?: unknown; params?: Record<string, unknown> }},
    ): Promise<T>;

"""


def emit_typescript(ops: list[Operation], models: list[str]) -> str:
    imports = "\n".join(f"    {m}," for m in models)
    body = "\n".join(_ts_method(op) for op in ops)
    return _TS_HEADER.format(imports=imports) + body + "}\n"


# ── CLI ────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="Path to filtered openapi.json")
    ap.add_argument("--py-out", required=True)
    ap.add_argument("--ts-out", required=True)
    args = ap.parse_args()

    spec = json.loads(Path(args.input).read_text())
    ops, models = _collect(spec)
    if not ops:
        raise SystemExit("No x-sdk-method operations — nothing to emit.")

    Path(args.py_out).write_text(emit_python(ops, models))
    Path(args.ts_out).write_text(emit_typescript(ops, models))
    print(f"  → {len(ops)} operations, {len(models)} models referenced")
    print(f"  → {args.py_out}")
    print(f"  → {args.ts_out}")


if __name__ == "__main__":
    main()
