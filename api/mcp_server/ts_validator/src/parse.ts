// TypeScript → workflow JSON.
//
// Parses LLM-authored SDK code with the TypeScript compiler, walks the
// AST statement by statement, and builds up a workflow JSON from the
// recognized SDK patterns:
//
//   const wf = new Workflow({ name: "..." });
//   const X  = wf.addTyped(startCall({ ...fields }));
//   const Y  = wf.add({ type: "endCall", ...fields });
//   wf.edge(X, Y, { label: "...", condition: "..." });
//
// No code is executed. Any top-level statement that doesn't match one
// of the recognized shapes is a parse error with a file:line:col pointer
// so the LLM can iterate. Node data is validated against the spec
// catalog before returning.

import ts from "typescript";

import type {
    NodeSpec,
    ParseErrorItem,
    ParseResult,
    PropertySpec,
    WireEdge,
    WireNode,
} from "./types.ts";

export function parseCode(code: string, specs: NodeSpec[]): ParseResult {
    const specByName = new Map(specs.map((s) => [s.name, s]));
    const sourceFile = ts.createSourceFile(
        "workflow.ts",
        code,
        ts.ScriptTarget.ESNext,
        true,
        ts.ScriptKind.TS,
    );

    const errors: ParseErrorItem[] = [];
    const nodes: WireNode[] = [];
    const edges: WireEdge[] = [];
    const nodeRefs = new Map<string, WireNode>();
    let workflowVar: string | null = null;
    let workflowName = "";
    let nextId = 1;

    const addError = (node: ts.Node, message: string): void => {
        const pos = sourceFile.getLineAndCharacterOfPosition(node.getStart());
        errors.push({
            message,
            line: pos.line + 1,
            column: pos.character + 1,
        });
    };

    for (const stmt of sourceFile.statements) {
        if (ts.isImportDeclaration(stmt)) continue; // imports are harmless
        if (
            ts.isExportAssignment(stmt) ||
            stmt.kind === ts.SyntaxKind.EmptyStatement
        ) {
            continue;
        }

        // `const X = ...;` or `wf.edge(...);`
        if (ts.isVariableStatement(stmt)) {
            handleVariableStatement(stmt);
            continue;
        }
        if (ts.isExpressionStatement(stmt)) {
            handleExpressionStatement(stmt);
            continue;
        }
        addError(
            stmt,
            `Only imports, \`const X = ...\` bindings, and \`wf.edge(...)\` calls are allowed at the top level. Found: ${ts.SyntaxKind[stmt.kind]}.`,
        );
    }

    function handleVariableStatement(stmt: ts.VariableStatement): void {
        const modifiers = ts.getModifiers(stmt);
        if (modifiers && modifiers.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)) {
            addError(stmt, "`export` is not allowed on workflow bindings.");
            return;
        }
        if ((stmt.declarationList.flags & ts.NodeFlags.Const) === 0) {
            addError(stmt, "Use `const` for all bindings.");
            return;
        }
        for (const decl of stmt.declarationList.declarations) {
            if (!ts.isIdentifier(decl.name)) {
                addError(decl, "Destructuring is not allowed — use a single identifier.");
                continue;
            }
            if (!decl.initializer) {
                addError(decl, "Bindings must have an initializer.");
                continue;
            }
            const varName = decl.name.text;
            handleBinding(varName, decl.initializer, decl);
        }
    }

    function handleBinding(
        varName: string,
        initializer: ts.Expression,
        origin: ts.Node,
    ): void {
        const expr = unwrapAwait(initializer);

        // `const wf = new Workflow({ name: "..." })`
        if (ts.isNewExpression(expr)) {
            if (!ts.isIdentifier(expr.expression) || expr.expression.text !== "Workflow") {
                addError(origin, "Only `new Workflow(...)` is supported for object construction.");
                return;
            }
            if (workflowVar) {
                addError(origin, `A Workflow is already bound (as \`${workflowVar}\`). Only one Workflow is allowed.`);
                return;
            }
            const args = expr.arguments ?? ts.factory.createNodeArray();
            if (args.length > 0) {
                const val = literalToJs(args[0]!, addError);
                if (
                    val &&
                    typeof val === "object" &&
                    !Array.isArray(val) &&
                    typeof (val as Record<string, unknown>)["name"] === "string"
                ) {
                    workflowName = (val as Record<string, unknown>)["name"] as string;
                }
            }
            workflowVar = varName;
            return;
        }

        // `const X = wf.addTyped(factory({...}))` or `wf.add({ type: "...", ... })`
        if (ts.isCallExpression(expr)) {
            const call = expr;
            const callee = call.expression;

            // Must be `wf.XYZ(...)` — property access off the workflow var
            if (
                !ts.isPropertyAccessExpression(callee) ||
                !ts.isIdentifier(callee.expression) ||
                (workflowVar !== null && callee.expression.text !== workflowVar)
            ) {
                addError(
                    origin,
                    `Expected \`${workflowVar ?? "wf"}.addTyped(...)\` or \`${workflowVar ?? "wf"}.add(...)\`.`,
                );
                return;
            }
            if (!workflowVar) {
                addError(origin, "Workflow must be constructed before adding nodes.");
                return;
            }

            const method = callee.name.text;
            if (method === "addTyped") {
                handleAddTyped(varName, call, origin);
            } else if (method === "add") {
                handleAddGeneric(varName, call, origin);
            } else {
                addError(
                    origin,
                    `Unsupported method \`${method}\`. Use \`addTyped\` or \`add\`.`,
                );
            }
            return;
        }

        addError(
            origin,
            "Only `new Workflow(...)`, `wf.addTyped(...)`, and `wf.add(...)` are allowed as binding initializers.",
        );
    }

    function handleAddTyped(
        varName: string,
        call: ts.CallExpression,
        origin: ts.Node,
    ): void {
        if (call.arguments.length < 1 || call.arguments.length > 2) {
            addError(origin, "`addTyped` takes 1 or 2 arguments.");
            return;
        }
        const inner = call.arguments[0]!;
        if (!ts.isCallExpression(inner) || !ts.isIdentifier(inner.expression)) {
            addError(
                origin,
                "`addTyped` must be called with a factory invocation, e.g. `addTyped(startCall({ ... }))`.",
            );
            return;
        }
        const factoryName = inner.expression.text;
        if (!specByName.has(factoryName)) {
            addError(
                origin,
                `Unknown node type: \`${factoryName}\`. Check the list of registered node types.`,
            );
            return;
        }
        const factoryArgs = inner.arguments;
        let data: Record<string, unknown> = {};
        if (factoryArgs.length > 0) {
            const parsed = literalToJs(factoryArgs[0]!, addError);
            if (parsed !== undefined) {
                if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
                    addError(inner, "Factory argument must be an object literal.");
                    return;
                }
                data = parsed as Record<string, unknown>;
            }
        }
        // Optional position arg
        const position = extractPositionArg(call.arguments[1], addError);
        bindNode(varName, factoryName, data, position, origin);
    }

    function handleAddGeneric(
        varName: string,
        call: ts.CallExpression,
        origin: ts.Node,
    ): void {
        if (call.arguments.length !== 1) {
            addError(origin, "`add` takes exactly 1 object argument.");
            return;
        }
        const parsed = literalToJs(call.arguments[0]!, addError);
        if (parsed === undefined) return;
        if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
            addError(origin, "`add` argument must be an object literal.");
            return;
        }
        const obj = parsed as Record<string, unknown>;
        const typeValue = obj["type"];
        if (typeof typeValue !== "string") {
            addError(origin, "`add({ type, ... })` requires a string `type` field.");
            return;
        }
        if (!specByName.has(typeValue)) {
            addError(origin, `Unknown node type: \`${typeValue}\`.`);
            return;
        }
        let position: { x: number; y: number } | undefined;
        if (obj["position"] !== undefined) {
            const p = obj["position"];
            if (
                Array.isArray(p) &&
                p.length === 2 &&
                typeof p[0] === "number" &&
                typeof p[1] === "number"
            ) {
                position = { x: p[0], y: p[1] };
            } else {
                addError(
                    origin,
                    "`position` must be a [x, y] tuple of numbers.",
                );
                return;
            }
        }
        const { type: _ignored, position: _ignored2, ...rest } = obj;
        bindNode(varName, typeValue, rest, position, origin);
    }

    function bindNode(
        varName: string,
        type: string,
        data: Record<string, unknown>,
        position: { x: number; y: number } | undefined,
        origin: ts.Node,
    ): void {
        if (nodeRefs.has(varName)) {
            addError(origin, `Variable \`${varName}\` is already bound.`);
            return;
        }
        const node: WireNode = {
            id: String(nextId++),
            type,
            position: position ?? { x: 0, y: 0 },
            data,
        };
        nodes.push(node);
        nodeRefs.set(varName, node);
    }

    function handleExpressionStatement(stmt: ts.ExpressionStatement): void {
        const expr = unwrapAwait(stmt.expression);
        if (!ts.isCallExpression(expr)) {
            addError(stmt, "Only `wf.edge(...)` calls are allowed as bare statements.");
            return;
        }
        const callee = expr.expression;
        if (
            !ts.isPropertyAccessExpression(callee) ||
            !ts.isIdentifier(callee.expression) ||
            (workflowVar !== null && callee.expression.text !== workflowVar) ||
            callee.name.text !== "edge"
        ) {
            addError(stmt, "Only `wf.edge(source, target, opts)` is allowed as a bare statement.");
            return;
        }
        if (expr.arguments.length !== 3) {
            addError(stmt, "`edge` takes exactly 3 arguments: (source, target, opts).");
            return;
        }
        const [srcArg, tgtArg, optsArg] = expr.arguments;
        if (!ts.isIdentifier(srcArg!) || !ts.isIdentifier(tgtArg!)) {
            addError(stmt, "`edge` source and target must be variable identifiers bound by `addTyped`/`add`.");
            return;
        }
        const src = nodeRefs.get(srcArg.text);
        const tgt = nodeRefs.get(tgtArg.text);
        if (!src) {
            addError(srcArg, `Unknown node variable: \`${srcArg.text}\`.`);
            return;
        }
        if (!tgt) {
            addError(tgtArg, `Unknown node variable: \`${tgtArg.text}\`.`);
            return;
        }
        const opts = literalToJs(optsArg!, addError);
        if (opts === undefined) return;
        if (typeof opts !== "object" || opts === null || Array.isArray(opts)) {
            addError(stmt, "`edge` options must be an object literal.");
            return;
        }
        const optsObj = opts as Record<string, unknown>;
        if (typeof optsObj["label"] !== "string" || (optsObj["label"] as string).trim() === "") {
            addError(stmt, "`edge` requires a non-empty `label` string.");
            return;
        }
        if (typeof optsObj["condition"] !== "string" || (optsObj["condition"] as string).trim() === "") {
            addError(stmt, "`edge` requires a non-empty `condition` string.");
            return;
        }
        edges.push({
            id: `${src.id}-${tgt.id}`,
            source: src.id,
            target: tgt.id,
            data: optsObj,
        });
    }

    // ── terminate early on parse errors ──────────────────────────────
    if (errors.length > 0) {
        return { ok: false, stage: "parse", errors };
    }

    if (!workflowVar) {
        return {
            ok: false,
            stage: "parse",
            errors: [
                {
                    message:
                        "No Workflow construction found. Expected `const wf = new Workflow({ name: \"...\" });`.",
                },
            ],
        };
    }

    // ── spec-driven node validation ─────────────────────────────────
    const validationErrors: ParseErrorItem[] = [];
    for (const node of nodes) {
        const spec = specByName.get(node.type)!;
        const validated = validateNodeData(
            spec,
            node.data,
            (msg) => validationErrors.push({ message: `[${node.type}] ${msg}` }),
        );
        if (validated !== null) node.data = validated;
    }
    if (validationErrors.length > 0) {
        return { ok: false, stage: "validate", errors: validationErrors };
    }

    return {
        ok: true,
        workflow: {
            nodes,
            edges,
            viewport: { x: 0, y: 0, zoom: 1 },
        },
        workflowName,
    };
}

// ─── helpers ──────────────────────────────────────────────────────────

function unwrapAwait(expr: ts.Expression): ts.Expression {
    return ts.isAwaitExpression(expr) ? expr.expression : expr;
}

function extractPositionArg(
    arg: ts.Expression | undefined,
    addError: (n: ts.Node, m: string) => void,
): { x: number; y: number } | undefined {
    if (!arg) return undefined;
    const parsed = literalToJs(arg, addError);
    if (parsed === undefined || parsed === null) return undefined;
    if (
        typeof parsed === "object" &&
        !Array.isArray(parsed) &&
        Array.isArray((parsed as Record<string, unknown>)["position"])
    ) {
        const p = (parsed as Record<string, unknown>)["position"] as unknown[];
        if (p.length === 2 && typeof p[0] === "number" && typeof p[1] === "number") {
            return { x: p[0], y: p[1] };
        }
    }
    addError(arg, "Optional second arg must be `{ position: [x, y] }`.");
    return undefined;
}

// Convert an expression to a plain JS value. Accepts: string, number,
// boolean, null, undefined (→ undefined), array/object literals of the
// same. Rejects any expression with runtime semantics (identifiers other
// than `true/false/null/undefined`, function calls, arrow fns, etc.).
function literalToJs(
    expr: ts.Expression,
    addError: (n: ts.Node, m: string) => void,
): unknown {
    if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr)) {
        return expr.text;
    }
    if (ts.isNumericLiteral(expr)) return Number(expr.text);
    if (expr.kind === ts.SyntaxKind.TrueKeyword) return true;
    if (expr.kind === ts.SyntaxKind.FalseKeyword) return false;
    if (expr.kind === ts.SyntaxKind.NullKeyword) return null;
    if (ts.isIdentifier(expr) && expr.text === "undefined") return undefined;
    if (ts.isPrefixUnaryExpression(expr)) {
        if (expr.operator === ts.SyntaxKind.MinusToken) {
            const inner = literalToJs(expr.operand, addError);
            if (typeof inner === "number") return -inner;
        }
        if (expr.operator === ts.SyntaxKind.PlusToken) {
            const inner = literalToJs(expr.operand, addError);
            if (typeof inner === "number") return inner;
        }
        addError(expr, "Unsupported unary operator; only numeric negation is allowed.");
        return undefined;
    }
    if (ts.isArrayLiteralExpression(expr)) {
        const out: unknown[] = [];
        for (const el of expr.elements) {
            if (el.kind === ts.SyntaxKind.OmittedExpression) {
                addError(el, "Sparse arrays are not allowed.");
                return undefined;
            }
            if (ts.isSpreadElement(el)) {
                addError(el, "Spread elements are not allowed in array literals.");
                return undefined;
            }
            const v = literalToJs(el, addError);
            if (v === undefined && el.kind !== ts.SyntaxKind.Identifier) {
                return undefined;
            }
            out.push(v);
        }
        return out;
    }
    if (ts.isObjectLiteralExpression(expr)) {
        const out: Record<string, unknown> = {};
        for (const prop of expr.properties) {
            if (!ts.isPropertyAssignment(prop)) {
                addError(prop, "Only plain `key: value` properties are allowed (no methods, shorthand, spread, or computed keys).");
                return undefined;
            }
            let key: string;
            if (ts.isIdentifier(prop.name) || ts.isStringLiteral(prop.name)) {
                key = prop.name.text;
            } else {
                addError(prop.name, "Property keys must be identifiers or string literals.");
                return undefined;
            }
            const val = literalToJs(prop.initializer, addError);
            if (val === undefined && prop.initializer.kind !== ts.SyntaxKind.Identifier) {
                // treat explicit `undefined` as omission
                continue;
            }
            out[key] = val;
        }
        return out;
    }
    if (ts.isTemplateExpression(expr)) {
        addError(expr, "Template literals with interpolation are not allowed — use plain strings.");
        return undefined;
    }
    addError(expr, `Unsupported expression (${ts.SyntaxKind[expr.kind]}). Only literals are allowed in data positions.`);
    return undefined;
}

// Spec-driven validation, mirrors the shape of
// `sdk/python/src/dograh_sdk/_validation.py` but lightweight — applies
// defaults for missing optionals, catches unknown keys, enforces `options`
// membership, and type-shapes the scalar and `fixed_collection` cases.
function validateNodeData(
    spec: NodeSpec,
    data: Record<string, unknown>,
    addError: (message: string) => void,
): Record<string, unknown> | null {
    const out: Record<string, unknown> = {};
    const known = new Map<string, PropertySpec>();
    for (const p of spec.properties ?? []) known.set(p.name, p);

    for (const key of Object.keys(data)) {
        if (!known.has(key)) {
            addError(`Unknown field: \`${key}\`.`);
            return null;
        }
    }

    for (const [key, prop] of known) {
        if (key in data) {
            out[key] = data[key];
        } else if (prop.default !== undefined && prop.default !== null) {
            out[key] = prop.default;
        } else if (prop.required) {
            addError(`Missing required field: \`${key}\`.`);
            return null;
        }
    }

    for (const [key, prop] of known) {
        if (!(key in out)) continue;
        const value = out[key];
        const err = checkPropertyShape(prop, value);
        if (err) {
            addError(`Field \`${key}\`: ${err}`);
            return null;
        }
    }

    return out;
}

function checkPropertyShape(prop: PropertySpec, value: unknown): string | null {
    switch (prop.type) {
        case "string":
        case "mention_textarea":
        case "url":
        case "recording_ref":
        case "credential_ref":
            if (typeof value !== "string") return `expected string, got ${jsTypeOf(value)}.`;
            return null;
        case "number":
            if (typeof value !== "number") return `expected number, got ${jsTypeOf(value)}.`;
            return null;
        case "boolean":
            if (typeof value !== "boolean") return `expected boolean, got ${jsTypeOf(value)}.`;
            return null;
        case "tool_refs":
        case "document_refs":
        case "multi_options":
            if (!Array.isArray(value)) return `expected array, got ${jsTypeOf(value)}.`;
            for (const el of value) {
                if (prop.type === "multi_options") {
                    if (!isInOptions(prop, el)) {
                        return `value \`${JSON.stringify(el)}\` is not in the allowed options.`;
                    }
                } else if (typeof el !== "string") {
                    return `array elements must be strings.`;
                }
            }
            return null;
        case "options":
            if (!isInOptions(prop, value)) {
                return `value \`${JSON.stringify(value)}\` is not in the allowed options.`;
            }
            return null;
        case "json":
            if (typeof value !== "object" || value === null || Array.isArray(value)) {
                return `expected JSON object, got ${jsTypeOf(value)}.`;
            }
            return null;
        case "fixed_collection":
            if (!Array.isArray(value)) return `expected array of rows, got ${jsTypeOf(value)}.`;
            for (let i = 0; i < value.length; i++) {
                const row = value[i];
                if (typeof row !== "object" || row === null || Array.isArray(row)) {
                    return `row ${i}: expected object, got ${jsTypeOf(row)}.`;
                }
                for (const sub of prop.properties ?? []) {
                    const subVal = (row as Record<string, unknown>)[sub.name];
                    if (subVal === undefined) {
                        if (sub.required && (sub.default === undefined || sub.default === null)) {
                            return `row ${i}: missing required field \`${sub.name}\`.`;
                        }
                        continue;
                    }
                    const subErr = checkPropertyShape(sub, subVal);
                    if (subErr) return `row ${i}, \`${sub.name}\`: ${subErr}`;
                }
            }
            return null;
        default:
            return null; // Unknown types pass — forward compat.
    }
}

function isInOptions(prop: PropertySpec, value: unknown): boolean {
    if (!prop.options) return true;
    return prop.options.some((o) => o.value === value);
}

function jsTypeOf(v: unknown): string {
    if (v === null) return "null";
    if (Array.isArray(v)) return "array";
    return typeof v;
}
