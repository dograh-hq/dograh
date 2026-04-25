// Client-side validation of node data against a fetched spec. Mirrors
// `sdk/python/src/dograh_sdk/_validation.py` byte-for-byte where possible
// so the two SDKs raise identical error messages for identical bad input.
//
// Intentionally lightweight: catch typos / missing required / obvious
// scalar mismatches at the call site; leave rigorous coercion to the
// backend Pydantic validators at save time.

import { ValidationError } from "./errors.js";
import type { NodeSpec, PropertySpec } from "./types.js";

// PropertyType → expected JS typeof values (after accounting for `null`
// and arrays). `null` here means "skip scalar-type check" (compound
// types, refs, JSON, etc.).
const SCALAR_TYPES: Record<string, ReadonlyArray<string> | null> = {
    string: ["string"],
    number: ["number"],
    boolean: ["boolean"],
    options: null,
    multi_options: null,
    fixed_collection: ["array"],
    json: null,
    tool_refs: ["array"],
    document_refs: ["array"],
    recording_ref: ["string"],
    credential_ref: ["string"],
    mention_textarea: ["string"],
    url: ["string"],
};

function jsTypeOf(value: unknown): string {
    if (value === null) return "null";
    if (Array.isArray(value)) return "array";
    return typeof value;
}

function withHint(prop: PropertySpec, message: string): string {
    return prop.llm_hint ? `${message}\n  Hint: ${prop.llm_hint}` : message;
}

function checkScalar(prop: PropertySpec, value: unknown): void {
    if (value === undefined || value === null) return;
    const allowed = SCALAR_TYPES[prop.type];
    if (!allowed) return;
    const got = jsTypeOf(value);
    if (!allowed.includes(got)) {
        throw new ValidationError(
            withHint(prop, `${prop.name}: expected ${prop.type}, got ${got}`),
        );
    }
}

function checkOptions(prop: PropertySpec, value: unknown): void {
    if (value === undefined || value === null) return;
    const allowed = new Set((prop.options ?? []).map((o) => o.value));
    if (allowed.size === 0) return;
    if (prop.type === "multi_options") {
        if (!Array.isArray(value)) {
            throw new ValidationError(
                withHint(
                    prop,
                    `${prop.name}: expected list, got ${jsTypeOf(value)}`,
                ),
            );
        }
        const bad = value.filter(
            (v) => !allowed.has(v as string | number | boolean),
        );
        if (bad.length > 0) {
            throw new ValidationError(
                withHint(
                    prop,
                    `${prop.name}: values ${JSON.stringify(bad)} not in allowed ${JSON.stringify(
                        [...allowed].sort(),
                    )}`,
                ),
            );
        }
    } else if (!allowed.has(value as string | number | boolean)) {
        throw new ValidationError(
            withHint(
                prop,
                `${prop.name}: ${JSON.stringify(value)} not in allowed ${JSON.stringify(
                    [...allowed].sort(),
                )}`,
            ),
        );
    }
}

export function validateNodeData(
    spec: NodeSpec | { name: string; properties: PropertySpec[] },
    kwargs: Record<string, unknown>,
): Record<string, unknown> {
    const declared = new Map(spec.properties.map((p) => [p.name, p]));

    // Unknown field names — the most common LLM hallucination.
    const unknown = Object.keys(kwargs).filter((k) => !declared.has(k));
    if (unknown.length > 0) {
        throw new ValidationError(
            `${spec.name}: unknown field(s) ${JSON.stringify(unknown.sort())}. ` +
                `Allowed: ${JSON.stringify([...declared.keys()].sort())}`,
        );
    }

    const data: Record<string, unknown> = {};
    for (const [name, prop] of declared) {
        let value: unknown;
        if (name in kwargs) {
            value = kwargs[name];
        } else if (prop.default !== undefined && prop.default !== null) {
            value = prop.default;
        } else {
            value = undefined;
        }

        if (prop.type === "options" || prop.type === "multi_options") {
            checkOptions(prop, value);
        } else {
            checkScalar(prop, value);
        }

        // Nested fixed_collection rows — validate each row as a sub-spec.
        if (prop.type === "fixed_collection" && Array.isArray(value)) {
            const subSpec = {
                name: `${spec.name}.${name}`,
                properties: prop.properties ?? [],
            };
            data[name] = value.map((row) =>
                validateNodeData(subSpec, row as Record<string, unknown>),
            );
            continue;
        }

        if (value !== undefined) data[name] = value;
    }

    // Required check — must be set AND non-empty for strings.
    for (const [name, prop] of declared) {
        if (!prop.required) continue;
        const val = data[name];
        if (
            val === undefined ||
            val === null ||
            (typeof val === "string" && val.trim() === "")
        ) {
            throw new ValidationError(
                withHint(
                    prop,
                    `${spec.name}: required field missing: ${name}`,
                ),
            );
        }
    }

    return data;
}
