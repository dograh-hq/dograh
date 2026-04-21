// Typed SDK code generator (TypeScript).
//
// Reads NodeSpecs from the live backend or a local JSON file and emits
// one `<kebab-case>.ts` per node type into `src/typed/` — each with a
// discriminated-union interface + a factory. The generated files are
// committed so `npm install @dograh/sdk` ships typed classes without
// requiring a regen step.
//
// Run via `npm run codegen` or:
//
//   node scripts/codegen.mts --api http://localhost:8000 --out src/typed
//   node scripts/codegen.mts --input specs.json --out src/typed

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// ─── Spec types (structural; loaded at runtime via JSON) ──────────────────

interface PropertyOption {
    value: string | number | boolean;
    label: string;
    description?: string;
}

interface PropertySpec {
    name: string;
    type: string;
    display_name: string;
    description: string;
    llm_hint?: string | null;
    default?: unknown;
    required?: boolean;
    options?: PropertyOption[];
    properties?: PropertySpec[];
}

interface NodeSpec {
    name: string;
    display_name: string;
    description: string;
    llm_hint?: string | null;
    category: string;
    icon: string;
    version: string;
    properties: PropertySpec[];
}

// ─── Property type → TS type ──────────────────────────────────────────────

const SCALAR_TS_TYPES: Record<string, string> = {
    string: "string",
    number: "number",
    boolean: "boolean",
    json: "Record<string, unknown>",
    mention_textarea: "string",
    url: "string",
    recording_ref: "string",
    credential_ref: "string",
    tool_refs: "string[]",
    document_refs: "string[]",
};

function pascalCase(name: string): string {
    // startCall → StartCall; agentNode → AgentNode
    return name[0]!.toUpperCase() + name.slice(1);
}

function kebabCase(name: string): string {
    // startCall → start-call; agentNode → agent-node
    return name.replace(/([a-z])([A-Z])/g, "$1-$2").toLowerCase();
}

function literalUnion(options: PropertyOption[] | undefined): string {
    if (!options || options.length === 0) return "string";
    return options.map((o) => JSON.stringify(o.value)).join(" | ");
}

function tsTypeFor(prop: PropertySpec, ownerClass: string): string {
    if (prop.type === "options") return literalUnion(prop.options);
    if (prop.type === "multi_options") {
        return `Array<${literalUnion(prop.options)}>`;
    }
    if (prop.type === "fixed_collection") {
        return `Array<${ownerClass}${pascalCase(prop.name)}Row>`;
    }
    return SCALAR_TS_TYPES[prop.type] ?? "unknown";
}

// ─── JSDoc rendering ──────────────────────────────────────────────────────

function renderJsDoc(description: string, llmHint?: string | null, indent = 0): string {
    const pad = " ".repeat(indent);
    const body = [description, ...(llmHint ? ["", `LLM hint: ${llmHint}`] : [])]
        .join("\n")
        .split("\n")
        .map((line) => `${pad} * ${line}`.trimEnd())
        .join("\n");
    return `${pad}/**\n${body}\n${pad} */`;
}

// ─── Source rendering ─────────────────────────────────────────────────────

function renderNestedRowInterface(
    ownerClass: string,
    parent: PropertySpec,
): string {
    const rowClass = `${ownerClass}${pascalCase(parent.name)}Row`;
    const props = parent.properties ?? [];
    const lines: string[] = [];
    lines.push(
        renderJsDoc(parent.description ?? `Row in ${parent.name}.`, null),
    );
    lines.push(`export interface ${rowClass} {`);
    for (const sub of props) {
        if (sub.description) lines.push(renderJsDoc(sub.description, null, 4));
        const annotation = tsTypeFor(sub, rowClass);
        const optional = sub.required ? "" : "?";
        lines.push(`    ${sub.name}${optional}: ${annotation};`);
    }
    lines.push("}");
    return lines.join("\n");
}

function renderSpecFile(spec: NodeSpec): string {
    const className = pascalCase(spec.name);

    const header = `// GENERATED — do not edit by hand.
//
// Regenerate with \`npm run codegen\` against the target Dograh backend.
// Source of truth: each node's NodeSpec in the backend's
// \`api/services/workflow/node_specs/\` directory.
`;

    const nested: string[] = [];
    for (const prop of spec.properties) {
        if (prop.type === "fixed_collection") {
            nested.push(renderNestedRowInterface(className, prop));
        }
    }

    const classDoc = renderJsDoc(spec.description, spec.llm_hint);
    const fieldLines: string[] = [];
    fieldLines.push(`    type: ${JSON.stringify(spec.name)};`);
    for (const prop of spec.properties) {
        if (prop.description) {
            fieldLines.push(renderJsDoc(prop.description, prop.llm_hint, 4));
        }
        const annotation = tsTypeFor(prop, className);
        // Required field (no spec default) has no `?`; everything else
        // optional, the runtime SDK applies spec defaults.
        const hasDefault = prop.default !== undefined && prop.default !== null;
        const optional = prop.required && !hasDefault ? "" : "?";
        fieldLines.push(`    ${prop.name}${optional}: ${annotation};`);
    }

    const iface = `${classDoc}
export interface ${className} {
${fieldLines.join("\n")}
}`;

    const factoryDoc = `/** Factory — sets \`type\` for you so you don't repeat the discriminator. */`;
    const factory = `${factoryDoc}
export function ${spec.name}(input: Omit<${className}, "type">): ${className} {
    return { type: ${JSON.stringify(spec.name)}, ...input };
}`;

    return [header, ...nested, "", iface, "", factory, ""].join("\n");
}

function renderIndex(specs: NodeSpec[]): string {
    const lines: string[] = [
        "// GENERATED — do not edit by hand.",
        "//",
        "// Re-exports every typed node interface + factory. Also exports the",
        "// `TypedNode` discriminated-union that `Workflow.addTyped` accepts.",
        "",
    ];
    const classNames: string[] = [];
    for (const spec of specs.slice().sort((a, b) => a.name.localeCompare(b.name))) {
        const className = pascalCase(spec.name);
        const module = kebabCase(spec.name);
        lines.push(
            `export { type ${className}, ${spec.name} } from "./${module}.js";`,
        );
        classNames.push(className);
    }
    lines.push("");
    lines.push("import type {");
    for (const name of classNames) lines.push(`    ${name},`);
    lines.push('} from "./index.js";');
    lines.push("");
    lines.push("/** Discriminated union of every generated typed node. */");
    lines.push(`export type TypedNode = ${classNames.join(" | ")};`);
    lines.push("");
    return lines.join("\n");
}

// ─── CLI ─────────────────────────────────────────────────────────────────

function parseArgs(argv: string[]): { api?: string; input?: string; out: string } {
    let api: string | undefined;
    let input: string | undefined;
    let out = "";
    for (let i = 0; i < argv.length; i++) {
        const a = argv[i];
        if (a === "--api") api = argv[++i];
        else if (a === "--input") input = argv[++i];
        else if (a === "--out") out = argv[++i]!;
    }
    if (!out) throw new Error("--out is required");
    if (!api && !input) throw new Error("Provide --api URL or --input PATH");
    return { api, input, out };
}

async function loadSpecs(args: {
    api?: string;
    input?: string;
}): Promise<NodeSpec[]> {
    if (args.api) {
        const resp = await fetch(`${args.api.replace(/\/$/, "")}/api/v1/node-types`);
        if (!resp.ok) {
            throw new Error(
                `GET /api/v1/node-types failed: ${resp.status} ${resp.statusText}`,
            );
        }
        const body = (await resp.json()) as { node_types: NodeSpec[] };
        return body.node_types ?? [];
    }
    const raw = JSON.parse(readFileSync(args.input!, "utf-8"));
    if (Array.isArray(raw)) return raw as NodeSpec[];
    if (raw && typeof raw === "object" && "node_types" in raw) {
        return (raw as { node_types: NodeSpec[] }).node_types;
    }
    throw new Error("JSON must be an array or { node_types: [...] }");
}

async function main(): Promise<void> {
    const args = parseArgs(process.argv.slice(2));
    const specs = await loadSpecs(args);
    mkdirSync(args.out, { recursive: true });

    for (const spec of specs) {
        const module = kebabCase(spec.name);
        writeFileSync(join(args.out, `${module}.ts`), renderSpecFile(spec));
    }
    writeFileSync(join(args.out, "index.ts"), renderIndex(specs));

    console.log(
        `Generated ${specs.length} typed node modules (${specs
            .map((s) => s.name)
            .join(", ")}) into ${args.out}`,
    );
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
