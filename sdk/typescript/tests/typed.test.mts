// Tests for the typed SDK (`@dograh/sdk/typed`). Mirrors
// api/tests/test_dograh_sdk_typed.py — checks that generated factories
// produce objects consumable by `workflow.addTyped()`.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
    agentNode,
    endCall,
    startCall,
    type AgentNode,
    type EndCall,
    type StartCall,
    type Trigger,
    type TypedNode,
} from "../dist/typed/index.js";
import { Workflow, type NodeSpec } from "../dist/index.js";
import type { SpecProvider } from "../dist/workflow.js";

// Minimal spec stub matching the shape `getNodeType` returns — we just
// need `properties` for the validator to do its job.
const MINIMAL_SPECS: Record<string, NodeSpec> = {
    startCall: {
        name: "startCall",
        display_name: "Start Call",
        description: "entry",
        category: "call_node",
        icon: "Play",
        version: "1.0.0",
        properties: [
            { name: "name", type: "string", display_name: "N", description: "d", required: true, default: "Start Call" },
            { name: "prompt", type: "mention_textarea", display_name: "P", description: "d", required: true },
        ],
    },
    agentNode: {
        name: "agentNode",
        display_name: "Agent",
        description: "step",
        category: "call_node",
        icon: "Headset",
        version: "1.0.0",
        properties: [
            { name: "name", type: "string", display_name: "N", description: "d", required: true },
            { name: "prompt", type: "mention_textarea", display_name: "P", description: "d", required: true },
        ],
    },
    endCall: {
        name: "endCall",
        display_name: "End",
        description: "terminal",
        category: "call_node",
        icon: "OctagonX",
        version: "1.0.0",
        properties: [
            { name: "name", type: "string", display_name: "N", description: "d", required: true },
            { name: "prompt", type: "mention_textarea", display_name: "P", description: "d", required: true },
        ],
    },
};

class StubClient implements SpecProvider {
    async getNodeType(name: string): Promise<NodeSpec> {
        const s = MINIMAL_SPECS[name];
        if (!s) throw new Error(`Unknown spec: ${name}`);
        return s;
    }
}

// ─── Factories stamp the `type` discriminator ─────────────────────────────

describe("typed factories", () => {
    it("startCall() fills in the type discriminator", () => {
        const node = startCall({ name: "g", prompt: "hi" });
        assert.equal(node.type, "startCall");
        assert.equal(node.name, "g");
        assert.equal(node.prompt, "hi");
    });

    it("agentNode() fills in the type discriminator", () => {
        const node = agentNode({ name: "a", prompt: "ask" });
        assert.equal(node.type, "agentNode");
    });

    it("endCall() fills in the type discriminator", () => {
        const node = endCall({ name: "e", prompt: "bye" });
        assert.equal(node.type, "endCall");
    });
});

// ─── Workflow.addTyped integrates with the generic builder ────────────────

describe("Workflow.addTyped", () => {
    it("accepts a typed factory result and round-trips through toJson", async () => {
        const wf = new Workflow({ client: new StubClient(), name: "typed-e2e" });
        const start = await wf.addTyped(startCall({ name: "g", prompt: "hi" }));
        const end = await wf.addTyped(endCall({ name: "e", prompt: "bye" }));
        wf.edge(start, end, { label: "done", condition: "done" });

        const payload = wf.toJson();
        assert.equal(payload.nodes.length, 2);
        assert.equal(payload.nodes[0]!.type, "startCall");
        assert.equal(payload.nodes[1]!.type, "endCall");
        assert.equal(payload.edges.length, 1);
    });

    it("addTyped and add produce identical node data for equivalent inputs", async () => {
        const typedWf = new Workflow({ client: new StubClient() });
        await typedWf.addTyped(agentNode({ name: "q", prompt: "ask" }));

        const genericWf = new Workflow({ client: new StubClient() });
        await genericWf.add({ type: "agentNode", name: "q", prompt: "ask" });

        assert.deepEqual(
            typedWf.toJson().nodes[0]!.data,
            genericWf.toJson().nodes[0]!.data,
        );
    });

    it("TypedNode union narrows correctly on `type`", async () => {
        // Compile-time check — TS narrows on the literal discriminator.
        const node: TypedNode = startCall({ name: "g", prompt: "hi" });
        if (node.type === "startCall") {
            // `node` is narrowed to StartCall here; the following access
            // compiles without a cast.
            assert.equal(node.prompt, "hi");
        } else {
            assert.fail("expected StartCall narrowing");
        }
    });
});
