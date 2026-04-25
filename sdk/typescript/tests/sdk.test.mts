// Unit tests for @dograh/sdk. Uses Node's built-in `node:test` runner and
// an in-memory spec stub — no HTTP, no backend dependency. Mirrors the
// Python SDK tests in api/tests/test_dograh_sdk.py.
//
// Run via `npm test` in sdk/typescript/.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// Import the BUILT artifact — same shape consumers get from `npm install`.
// `npm test` runs `tsc` first so dist/ is fresh.
import {
    ApiError,
    DograhClient,
    SpecMismatchError,
    ValidationError,
    Workflow,
} from "../dist/index.js";
import type { NodeSpec, SpecProvider } from "../dist/index.js";

// ─── Minimal fixture specs (enough to cover the SDK's code paths) ─────────

const SPECS: Record<string, NodeSpec> = {
    startCall: {
        name: "startCall",
        display_name: "Start Call",
        description: "Entry point.",
        category: "call_node",
        icon: "Play",
        version: "1.0.0",
        properties: [
            {
                name: "name",
                type: "string",
                display_name: "Name",
                description: "n",
                required: true,
                default: "Start Call",
            },
            {
                name: "prompt",
                type: "mention_textarea",
                display_name: "Prompt",
                description: "p",
                required: true,
            },
            {
                name: "allow_interrupt",
                type: "boolean",
                display_name: "Allow Interrupt",
                description: "a",
                default: false,
            },
            {
                name: "greeting_type",
                type: "options",
                display_name: "Greeting Type",
                description: "g",
                default: "text",
                options: [
                    { value: "text", label: "Text" },
                    { value: "audio", label: "Audio" },
                ],
            },
        ],
    },
    agentNode: {
        name: "agentNode",
        display_name: "Agent",
        description: "Mid-call step.",
        category: "call_node",
        icon: "Headset",
        version: "1.0.0",
        properties: [
            {
                name: "name",
                type: "string",
                display_name: "Name",
                description: "n",
                required: true,
            },
            {
                name: "prompt",
                type: "mention_textarea",
                display_name: "Prompt",
                description: "p",
                required: true,
            },
            {
                name: "allow_interrupt",
                type: "boolean",
                display_name: "Allow",
                description: "a",
                default: true,
            },
            {
                name: "tool_uuids",
                type: "tool_refs",
                display_name: "Tools",
                description: "Tools the agent can invoke.",
                llm_hint: "List of tool UUIDs from `list_tools`.",
            },
        ],
    },
    endCall: {
        name: "endCall",
        display_name: "End",
        description: "Terminal.",
        category: "call_node",
        icon: "OctagonX",
        version: "1.0.0",
        properties: [
            {
                name: "name",
                type: "string",
                display_name: "Name",
                description: "n",
                required: true,
            },
            {
                name: "prompt",
                type: "mention_textarea",
                display_name: "Prompt",
                description: "p",
                required: true,
            },
        ],
    },
};

class StubClient implements SpecProvider {
    async getNodeType(name: string): Promise<NodeSpec> {
        const spec = SPECS[name];
        if (!spec) throw new SpecMismatchError(`Unknown spec: ${name}`);
        return spec;
    }
}

const client = new StubClient();

// ─── Builder + toJson round-trip ──────────────────────────────────────────

describe("Workflow builder", () => {
    it("builds a minimal workflow and serializes the wire shape", async () => {
        const wf = new Workflow({ client, name: "minimal" });
        const start = await wf.add({
            type: "startCall",
            name: "greeting",
            prompt: "Say hi.",
        });
        const end = await wf.add({
            type: "endCall",
            name: "close",
            prompt: "Thank them.",
        });
        wf.edge(start, end, { label: "done", condition: "All greeted." });

        const payload = wf.toJson();
        assert.equal(payload.nodes.length, 2);
        assert.deepEqual(
            payload.nodes.map((n) => n.type).sort(),
            ["endCall", "startCall"],
        );
        assert.equal(payload.edges.length, 1);
        const edge = payload.edges[0]!;
        assert.equal(edge.source, start.id);
        assert.equal(edge.target, end.id);
    });

    it("applies spec defaults when fields are omitted", async () => {
        const wf = new Workflow({ client });
        const start = await wf.add({
            type: "startCall",
            name: "g",
            prompt: "hi",
        });
        const data = wf.toJson().nodes[0]!.data;
        assert.equal(data.allow_interrupt, false);
        assert.equal(data.greeting_type, "text");
        assert.ok(start.id);
    });
});

// ─── Validation errors ────────────────────────────────────────────────────

describe("validation", () => {
    it("catches unknown field names", async () => {
        const wf = new Workflow({ client });
        await assert.rejects(
            () =>
                wf.add({
                    type: "startCall",
                    name: "g",
                    prompt: "hi",
                    promt: "typo",
                }),
            (err: unknown) => {
                assert.ok(err instanceof ValidationError);
                assert.match(err.message, /unknown field/);
                return true;
            },
        );
    });

    it("catches missing required fields", async () => {
        const wf = new Workflow({ client });
        await assert.rejects(
            () => wf.add({ type: "startCall", name: "g" }),
            (err: unknown) => {
                assert.ok(err instanceof ValidationError);
                assert.match(err.message, /required field missing: prompt/);
                return true;
            },
        );
    });

    it("catches wrong scalar types", async () => {
        const wf = new Workflow({ client });
        await assert.rejects(
            () =>
                wf.add({
                    type: "agentNode",
                    name: "x",
                    prompt: "y",
                    allow_interrupt: "yes",
                }),
            (err: unknown) => {
                assert.ok(err instanceof ValidationError);
                assert.match(err.message, /expected boolean/);
                return true;
            },
        );
    });

    it("catches invalid options values", async () => {
        const wf = new Workflow({ client });
        await assert.rejects(
            () =>
                wf.add({
                    type: "startCall",
                    name: "g",
                    prompt: "hi",
                    greeting_type: "video",
                }),
            (err: unknown) => {
                assert.ok(err instanceof ValidationError);
                assert.match(err.message, /not in allowed/);
                return true;
            },
        );
    });

    it("surfaces llm_hint in error messages when the spec has one", async () => {
        const wf = new Workflow({ client });
        await assert.rejects(
            () =>
                wf.add({
                    type: "agentNode",
                    name: "x",
                    prompt: "y",
                    tool_uuids: "single-uuid-not-a-list",
                }),
            (err: unknown) => {
                assert.ok(err instanceof ValidationError);
                assert.match(err.message, /tool_uuids/);
                assert.match(err.message, /Hint:/);
                assert.match(err.message, /list_tools/);
                return true;
            },
        );
    });

    it("does not add 'Hint:' when a spec has no llm_hint", async () => {
        const wf = new Workflow({ client });
        await assert.rejects(
            () =>
                wf.add({
                    type: "agentNode",
                    name: "x",
                    prompt: "y",
                    allow_interrupt: "yes",
                }),
            (err: unknown) => {
                assert.ok(err instanceof ValidationError);
                assert.ok(!err.message.includes("Hint:"));
                return true;
            },
        );
    });

    it("rejects edges without label or condition", async () => {
        const wf = new Workflow({ client });
        const a = await wf.add({ type: "startCall", name: "a", prompt: "hi" });
        const b = await wf.add({ type: "endCall", name: "b", prompt: "bye" });
        assert.throws(() => wf.edge(a, b, { label: "", condition: "x" }), ValidationError);
        assert.throws(() => wf.edge(a, b, { label: "x", condition: "" }), ValidationError);
    });
});

// ─── Round-trip fromJson → edit → toJson ──────────────────────────────────

describe("round-trip", () => {
    it("fromJson preserves IDs and subsequent add() does not collide", async () => {
        const wf0 = new Workflow({ client });
        const start = await wf0.add({ type: "startCall", name: "g", prompt: "hi" });
        const end = await wf0.add({ type: "endCall", name: "e", prompt: "bye" });
        wf0.edge(start, end, { label: "done", condition: "done" });

        const payload = wf0.toJson();
        const wf1 = await Workflow.fromJson(payload, { client });

        assert.deepEqual(
            wf1.toJson().nodes.map((n) => n.id),
            [start.id, end.id],
        );
        const fresh = await wf1.add({
            type: "agentNode",
            name: "mid",
            prompt: "do stuff",
        });
        assert.notEqual(fresh.id, start.id);
        assert.notEqual(fresh.id, end.id);
        assert.ok(Number(fresh.id) > Math.max(Number(start.id), Number(end.id)));
    });

    it("fromJson validates data — unknown field raises", async () => {
        const bad = {
            nodes: [
                {
                    id: "1",
                    type: "startCall",
                    position: { x: 0, y: 0 },
                    data: { name: "g", prompt: "hi", bogus: 1 },
                },
            ],
            edges: [],
        };
        await assert.rejects(
            () => Workflow.fromJson(bad, { client }),
            (err: unknown) => {
                assert.ok(err instanceof ValidationError);
                assert.match(err.message, /unknown field/);
                return true;
            },
        );
    });
});

// ─── DograhClient HTTP plumbing (stubbed fetch) ───────────────────────────

describe("DograhClient", () => {
    it("sends the API key as X-API-Key", async () => {
        let capturedHeaders: Headers | undefined;
        const stubFetch: typeof fetch = async (_input, init) => {
            capturedHeaders = new Headers(init?.headers);
            return new Response(
                JSON.stringify({ spec_version: "1.0.0", node_types: [] }),
                { status: 200, headers: { "content-type": "application/json" } },
            );
        };
        const c = new DograhClient({
            baseUrl: "http://api.example",
            apiKey: "sk-test",
            fetch: stubFetch,
        });
        await c.listNodeTypes();
        assert.equal(capturedHeaders?.get("x-api-key"), "sk-test");
    });

    it("surfaces 4xx responses as ApiError", async () => {
        const stubFetch: typeof fetch = async () =>
            new Response(JSON.stringify({ detail: "Unknown node type: 'foo'" }), {
                status: 404,
                headers: { "content-type": "application/json" },
            });
        const c = new DograhClient({
            baseUrl: "http://api.example",
            apiKey: "k",
            fetch: stubFetch,
        });
        await assert.rejects(
            () => c.getNodeType("foo"),
            (err: unknown) => {
                assert.ok(err instanceof SpecMismatchError);
                return true;
            },
        );
    });

    it("caches specs per client so a second get_node_type is free", async () => {
        let calls = 0;
        const spec: NodeSpec = {
            name: "startCall",
            display_name: "Start",
            description: "d",
            category: "call_node",
            icon: "Play",
            version: "1.0.0",
            properties: [],
        };
        const stubFetch: typeof fetch = async () => {
            calls++;
            return new Response(JSON.stringify(spec), {
                status: 200,
                headers: { "content-type": "application/json" },
            });
        };
        const c = new DograhClient({
            baseUrl: "http://api.example",
            apiKey: "k",
            fetch: stubFetch,
        });
        await c.getNodeType("startCall");
        await c.getNodeType("startCall");
        assert.equal(calls, 1);
    });

    it("ApiError constructor stores statusCode and body", () => {
        const err = new ApiError(500, "boom", { detail: "oops" });
        assert.equal(err.statusCode, 500);
        assert.deepEqual(err.body, { detail: "oops" });
    });
});
