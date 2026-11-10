// Stdin/stdout dispatch. Reads a single JSON request, routes to
// generate, parse or layout, writes a single JSON response. Exits 0 on request
// success (including validation failures — those are in the JSON), and
// exits 1 only on internal errors (bad input JSON, unhandled exception).

import { generateCode } from "./generate.ts";
import { layoutWorkflowNodes } from "./layout.ts";
import type { LayoutEdge, LayoutNode, RankDir } from "./layout.ts";
import { parseCode } from "./parse.ts";
import type { NodeSpec, WireWorkflow } from "./types.ts";

interface GenerateRequest {
    command: "generate";
    workflow: WireWorkflow;
    specs: NodeSpec[];
    edgeFieldNames: string[];
    workflowName?: string;
}

interface ParseRequest {
    command: "parse";
    code: string;
    specs: NodeSpec[];
    edgeFieldNames: string[];
    tidy?: boolean;
}

// Tidy Up for an unsaved canvas: only ids, types and edge endpoints are
// needed, and only positions come back.
interface LayoutRequest {
    command: "layout";
    nodes: LayoutNode[];
    edges: LayoutEdge[];
    rankdir?: RankDir;
}

type Request = GenerateRequest | ParseRequest | LayoutRequest;

async function readStdin(): Promise<string> {
    const chunks: Buffer[] = [];
    for await (const chunk of process.stdin) {
        chunks.push(chunk as Buffer);
    }
    return Buffer.concat(chunks).toString("utf-8");
}

function writeResult(payload: unknown): void {
    process.stdout.write(JSON.stringify(payload));
}

async function main(): Promise<void> {
    const input = await readStdin();
    let req: Request;
    try {
        req = JSON.parse(input) as Request;
    } catch (e) {
        writeResult({
            ok: false,
            stage: "internal",
            errors: [{ message: `Invalid JSON on stdin: ${(e as Error).message}` }],
        });
        process.exit(1);
    }

    if (req.command === "generate") {
        writeResult(
            generateCode(req.workflow, req.specs, {
                workflowName: req.workflowName,
                edgeFieldNames: req.edgeFieldNames,
            }),
        );
        return;
    }
    if (req.command === "parse") {
        const result = parseCode(req.code, req.specs, req.edgeFieldNames);
        if (result.ok && req.tidy) {
            const tidied = layoutWorkflowNodes(result.workflow.nodes, result.workflow.edges);
            if (tidied) result.workflow.nodes = tidied;
        }
        writeResult(result);
        return;
    }
    if (req.command === "layout") {
        const positioned = layoutWorkflowNodes(req.nodes, req.edges, req.rankdir) ?? [];
        writeResult({
            ok: true,
            positions: positioned.map(({ id, position }) => ({ id, x: position.x, y: position.y })),
        });
        return;
    }
    writeResult({
        ok: false,
        stage: "internal",
        errors: [{ message: `Unknown command: ${(req as { command?: unknown }).command}` }],
    });
    process.exit(1);
}

main().catch((err: unknown) => {
    writeResult({
        ok: false,
        stage: "internal",
        errors: [{ message: (err as Error).stack ?? String(err) }],
    });
    process.exit(1);
});
