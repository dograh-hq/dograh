// JSON → TypeScript source. Emits flat code the LLM can read and edit:
// imports, a `Workflow` construction, one `addTyped` per node, one `edge`
// per edge. Variable names are derived from `data.name` (falling back to
// the node id) and deduplicated so the AST round-trips back through
// `parse.ts` into the same workflow JSON.

import type {
    GenerateResult,
    NodeSpec,
    PropertySpec,
    WireWorkflow,
} from "./types.ts";

export function generateCode(
    workflow: WireWorkflow,
    specs: NodeSpec[],
    opts: { workflowName?: string } = {},
): GenerateResult {
    const specByName = new Map(specs.map((s) => [s.name, s]));

    // Catch unknown node types up-front — otherwise we'd emit an import
    // line for a factory that doesn't exist.
    for (const n of workflow.nodes) {
        if (!specByName.has(n.type)) {
            return {
                ok: false,
                errors: [
                    {
                        message: `Unknown node type in workflow: "${n.type}"`,
                    },
                ],
            };
        }
    }

    const factoryNames = [
        ...new Set(workflow.nodes.map((n) => n.type)),
    ].sort();
    const nodeVarById = new Map<string, string>();
    const usedNames = new Set<string>();

    const lines: string[] = [];
    lines.push(`import { Workflow } from "@dograh/sdk";`);
    if (factoryNames.length > 0) {
        lines.push(
            `import { ${factoryNames.join(", ")} } from "@dograh/sdk/typed";`,
        );
    }
    lines.push("");
    const wfName = opts.workflowName ?? "";
    lines.push(
        `const wf = new Workflow(${renderObject({ name: wfName }, 0)});`,
    );
    lines.push("");

    for (const node of workflow.nodes) {
        const varName = pickVarName(node, usedNames);
        nodeVarById.set(node.id, varName);

        const spec = specByName.get(node.type)!;
        // Strip legacy/UI-state fields the spec doesn't know about
        // (e.g. `invalid`, `selected`, `dragging`, `is_start`,
        // `validationMessage`). They accumulated in stored workflow
        // data before the parser enforced spec validation, and are
        // pure noise from the LLM's perspective — dropping them keeps
        // the editing surface clean and avoids a pointless save-time
        // rejection round-trip.
        const knownOnly = stripUnknown(node.data, spec);
        const data = stripDefaults(knownOnly, spec);
        const factoryArg = renderObject(data, 0);

        // Positions are intentionally omitted — LLMs don't place nodes
        // sensibly, so we let a downstream auto-layout pass (future
        // enhancement) assign coordinates on save. Existing positions
        // in the DB are preserved by `parse.ts` defaulting to {0,0}
        // and the save path leaving pre-existing node positions alone.
        lines.push(
            `const ${varName} = wf.addTyped(${node.type}(${factoryArg}));`,
        );
    }

    if (workflow.edges.length > 0) {
        lines.push("");
    }
    for (const edge of workflow.edges) {
        const src = nodeVarById.get(edge.source);
        const tgt = nodeVarById.get(edge.target);
        if (!src || !tgt) {
            return {
                ok: false,
                errors: [
                    {
                        message:
                            `Edge ${edge.id} references unknown node ` +
                            `(source=${edge.source}, target=${edge.target}).`,
                    },
                ],
            };
        }
        const cleanedEdge = pickEdgeFields(edge.data);
        const edgeOpts = renderObject(cleanedEdge, 0);
        lines.push(`wf.edge(${src}, ${tgt}, ${edgeOpts});`);
    }

    return { ok: true, code: lines.join("\n") + "\n" };
}

// ─── helpers ──────────────────────────────────────────────────────────

function pickVarName(
    node: { id: string; data: Record<string, unknown> },
    used: Set<string>,
): string {
    const seed =
        typeof node.data["name"] === "string" && node.data["name"].trim()
            ? (node.data["name"] as string)
            : `node_${node.id}`;
    const base = sanitizeIdentifier(seed);
    let candidate = base;
    let i = 2;
    while (used.has(candidate) || RESERVED.has(candidate)) {
        candidate = `${base}_${i++}`;
    }
    used.add(candidate);
    return candidate;
}

function sanitizeIdentifier(raw: string): string {
    const cleaned = raw
        .trim()
        .replace(/[^a-zA-Z0-9_]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .toLowerCase();
    if (!cleaned) return "node";
    if (/^[0-9]/.test(cleaned)) return `n_${cleaned}`;
    return cleaned;
}

const RESERVED = new Set([
    "wf",
    "const",
    "let",
    "var",
    "new",
    "function",
    "class",
    "import",
    "export",
    "return",
    "if",
    "else",
    "for",
    "while",
    "do",
    "switch",
    "case",
    "break",
    "continue",
    "default",
    "throw",
    "try",
    "catch",
    "finally",
    "await",
    "async",
    "true",
    "false",
    "null",
    "undefined",
    "this",
    "super",
    "in",
    "of",
    "typeof",
    "instanceof",
    "delete",
    "void",
    "yield",
    "Workflow",
]);

// Drop keys not declared in the spec. Handles nested `fixed_collection`
// rows by recursing through sub-property specs. Anything that isn't in
// the spec is legacy/UI state and should never reach the LLM.
function stripUnknown(
    data: Record<string, unknown>,
    spec: NodeSpec,
): Record<string, unknown> {
    const known = new Map<string, PropertySpec>();
    for (const p of spec.properties ?? []) known.set(p.name, p);

    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(data)) {
        const prop = known.get(k);
        if (!prop) continue; // drop unknown
        if (prop.type === "fixed_collection" && Array.isArray(v)) {
            const rowSpec: NodeSpec = {
                name: prop.name,
                properties: prop.properties ?? [],
            };
            out[k] = v.map((row) =>
                row && typeof row === "object" && !Array.isArray(row)
                    ? stripUnknown(row as Record<string, unknown>, rowSpec)
                    : row,
            );
        } else {
            out[k] = v;
        }
    }
    return out;
}

// Edge schema is fixed (no NodeSpec for edges). Mirrors the allowed
// fields on `Workflow.edge(...)` in both SDKs.
const KNOWN_EDGE_FIELDS = new Set([
    "label",
    "condition",
    "transition_speech",
    "transition_speech_type",
    "transition_speech_recording_id",
]);

function pickEdgeFields(
    data: Record<string, unknown>,
): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(data)) {
        if (KNOWN_EDGE_FIELDS.has(k)) out[k] = v;
    }
    return out;
}

// Drop keys whose value equals the spec default — keeps emitted code tight.
function stripDefaults(
    data: Record<string, unknown>,
    spec: NodeSpec,
): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    const defaults = new Map<string, unknown>();
    for (const prop of spec.properties ?? []) {
        if (prop.default !== undefined && prop.default !== null) {
            defaults.set(prop.name, prop.default);
        }
    }
    for (const [k, v] of Object.entries(data)) {
        if (defaults.has(k) && deepEqual(defaults.get(k), v)) continue;
        out[k] = v;
    }
    return out;
}

function deepEqual(a: unknown, b: unknown): boolean {
    if (a === b) return true;
    if (typeof a !== typeof b) return false;
    if (a === null || b === null) return false;
    if (Array.isArray(a) && Array.isArray(b)) {
        if (a.length !== b.length) return false;
        return a.every((el, i) => deepEqual(el, b[i]));
    }
    if (typeof a === "object" && typeof b === "object") {
        const ak = Object.keys(a as object).sort();
        const bk = Object.keys(b as object).sort();
        if (ak.length !== bk.length) return false;
        if (ak.some((k, i) => k !== bk[i])) return false;
        return ak.every((k) =>
            deepEqual(
                (a as Record<string, unknown>)[k],
                (b as Record<string, unknown>)[k],
            ),
        );
    }
    return false;
}

// Object renderer biased for readability — strings use single-line JSON,
// nested objects/arrays indent one level per depth.
function renderObject(obj: Record<string, unknown>, depth: number): string {
    const keys = Object.keys(obj);
    if (keys.length === 0) return "{}";
    const pad = "    ".repeat(depth + 1);
    const closingPad = "    ".repeat(depth);
    const parts = keys.map((k) => {
        const v = renderValue(obj[k], depth + 1);
        return `${pad}${k}: ${v}`;
    });
    return `{\n${parts.join(",\n")},\n${closingPad}}`;
}

function renderValue(v: unknown, depth: number): string {
    if (v === null || v === undefined) return "null";
    if (typeof v === "string") return JSON.stringify(v);
    if (typeof v === "number" || typeof v === "boolean") return String(v);
    if (Array.isArray(v)) {
        if (v.length === 0) return "[]";
        const pad = "    ".repeat(depth + 1);
        const closingPad = "    ".repeat(depth);
        const items = v.map((el) => `${pad}${renderValue(el, depth + 1)}`);
        return `[\n${items.join(",\n")},\n${closingPad}]`;
    }
    if (typeof v === "object") {
        return renderObject(v as Record<string, unknown>, depth);
    }
    return JSON.stringify(v);
}
