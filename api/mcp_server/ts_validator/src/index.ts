// Stdin/stdout dispatch. Reads a single JSON request, routes to
// generate or parse, writes a single JSON response. Exits 0 on request
// success (including validation failures — those are in the JSON), and
// exits 1 only on internal errors (bad input JSON, unhandled exception).

import { generateCode } from "./generate.ts";
import { parseCode } from "./parse.ts";
import type { NodeSpec, WireWorkflow } from "./types.ts";

interface GenerateRequest {
    command: "generate";
    workflow: WireWorkflow;
    specs: NodeSpec[];
    workflowName?: string;
}

interface ParseRequest {
    command: "parse";
    code: string;
    specs: NodeSpec[];
}

type Request = GenerateRequest | ParseRequest;

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
        writeResult(generateCode(req.workflow, req.specs, { workflowName: req.workflowName }));
        return;
    }
    if (req.command === "parse") {
        writeResult(parseCode(req.code, req.specs));
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
