import { describe, expect, it } from "vitest";

import {
    buildSideBySideDiffRows,
    serializeWorkflowVersionJson,
} from "./workflowVersionDiff";

describe("serializeWorkflowVersionJson", () => {
    it("serializes only versioned workflow content with stable object keys", () => {
        const serialized = serializeWorkflowVersionJson({
            workflow_json: {
                nodes: [{ id: "1", data: { prompt: "Hello", name: "Start" } }],
                edges: [],
            },
            workflow_configurations: { z_setting: true, a_setting: false },
            template_context_variables: { customer_name: "Ada" },
        });

        expect(JSON.parse(serialized)).toEqual({
            template_context_variables: { customer_name: "Ada" },
            workflow_configurations: { a_setting: false, z_setting: true },
            workflow_json: {
                edges: [],
                nodes: [{ data: { name: "Start", prompt: "Hello" }, id: "1" }],
            },
        });
        expect(serialized.indexOf('"a_setting"')).toBeLessThan(serialized.indexOf('"z_setting"'));
    });

    it("sorts workflow entities by ID while preserving ordinary array ordering", () => {
        const serialized = serializeWorkflowVersionJson({
            workflow_json: {
                nodes: [{ id: "2" }, { id: "1" }],
                prompt_sections: ["second", "first"],
            },
        });
        const parsed = JSON.parse(serialized);

        expect(parsed.workflow_json.nodes.map((node: { id: string }) => node.id)).toEqual(["1", "2"]);
        expect(parsed.workflow_json.prompt_sections).toEqual(["second", "first"]);
    });

    it("removes React Flow visual and runtime metadata", () => {
        const serialized = serializeWorkflowVersionJson({
            workflow_json: {
                viewport: { x: 0, y: 0, zoom: 1 },
                nodes: [{
                    id: "node-1",
                    type: "agentNode",
                    position: { x: -325, y: 480 },
                    measured: { height: 219, width: 400 },
                    selected: true,
                    dragging: false,
                    data: {
                        name: "Agent",
                        prompt: "Call the customer",
                        invalid: true,
                        validationMessage: "Temporary validation state",
                        runtime_active: true,
                    },
                }],
                edges: [{
                    id: "node-1-node-2",
                    source: "node-1",
                    target: "node-2",
                    type: "custom",
                    animated: true,
                    selected: true,
                    data: {
                        condition: "always",
                        label: "Continue",
                        invalid: true,
                        validationMessage: "Temporary validation state",
                    },
                }],
            },
        });

        expect(JSON.parse(serialized).workflow_json).toEqual({
            edges: [{
                data: { condition: "always", label: "Continue" },
                id: "node-1-node-2",
                source: "node-1",
                target: "node-2",
            }],
            nodes: [{
                data: { name: "Agent", prompt: "Call the customer" },
                id: "node-1",
                type: "agentNode",
            }],
        });
    });
});

describe("buildSideBySideDiffRows", () => {
    it("aligns a replaced line between the two panes", () => {
        const rows = buildSideBySideDiffRows("first\nold value\nlast", "first\nnew value\nlast");

        expect(rows[0]).toEqual({
            left: { kind: "unchanged", lineNumber: 1, text: "first" },
            right: { kind: "unchanged", lineNumber: 1, text: "first" },
        });
        expect(rows[1]).toMatchObject({
            left: { kind: "removed", lineNumber: 2, text: "old value" },
            right: { kind: "added", lineNumber: 2, text: "new value" },
        });
        expect(rows[1].left?.segments).toEqual([
            { kind: "removed", text: "old" },
            { kind: "unchanged", text: " value" },
        ]);
        expect(rows[1].right?.segments).toEqual([
            { kind: "added", text: "new" },
            { kind: "unchanged", text: " value" },
        ]);
        expect(rows[2]).toEqual({
            left: { kind: "unchanged", lineNumber: 3, text: "last" },
            right: { kind: "unchanged", lineNumber: 3, text: "last" },
        });
    });

    it("adds an empty aligned cell for an inserted line", () => {
        const rows = buildSideBySideDiffRows("first\nlast", "first\ninserted\nlast");

        expect(rows[1]).toEqual({
            left: null,
            right: {
                kind: "added",
                lineNumber: 2,
                text: "inserted",
                segments: [{ kind: "added", text: "inserted" }],
            },
        });
        expect(rows[2].left?.lineNumber).toBe(2);
        expect(rows[2].right?.lineNumber).toBe(3);
    });

    it("returns paired unchanged rows for identical content", () => {
        const rows = buildSideBySideDiffRows("same\ncontent", "same\ncontent");

        expect(rows).toHaveLength(2);
        expect(rows.every((row) => (
            row.left?.kind === "unchanged" && row.right?.kind === "unchanged"
        ))).toBe(true);
    });

    it("highlights only the changed words inside a long prompt line", () => {
        const previous = '      "prompt": "Call the customer today and confirm their appointment."';
        const selected = '      "prompt": "Call the customer tomorrow and confirm their appointment."';
        const [row] = buildSideBySideDiffRows(previous, selected);

        const removedText = row.left?.segments
            ?.filter((segment) => segment.kind === "removed")
            .map((segment) => segment.text)
            .join("");
        const addedText = row.right?.segments
            ?.filter((segment) => segment.kind === "added")
            .map((segment) => segment.text)
            .join("");

        expect(removedText).toBe("today");
        expect(addedText).toBe("tomorrow");
    });

    it("keeps a one-word edit surgical in a very large prompt", () => {
        const previousWords = Array.from({ length: 5_000 }, (_, index) => `word-${index}`);
        const selectedWords = [...previousWords];
        selectedWords[2_500] = "replacement";

        const [row] = buildSideBySideDiffRows(
            `      "prompt": "${previousWords.join(" ")}"`,
            `      "prompt": "${selectedWords.join(" ")}"`,
        );

        expect(row.left?.segments?.filter((segment) => segment.kind === "removed"))
            .toEqual([{ kind: "removed", text: "word-2500" }]);
        expect(row.right?.segments?.filter((segment) => segment.kind === "added"))
            .toEqual([{ kind: "added", text: "replacement" }]);
    });

    it("reconstructs large wholesale prompt rewrites with the bounded fallback", () => {
        const previous = Array.from({ length: 300 }, (_, index) => `old-${index}`).join(" ");
        const selected = Array.from({ length: 300 }, (_, index) => `new-${index}`).join(" ");
        const previousLine = `      "prompt": "${previous}"`;
        const selectedLine = `      "prompt": "${selected}"`;

        const [row] = buildSideBySideDiffRows(previousLine, selectedLine);

        expect(row.left?.segments?.map((segment) => segment.text).join(""))
            .toBe(previousLine);
        expect(row.right?.segments?.map((segment) => segment.text).join(""))
            .toBe(selectedLine);
    });

    it("can reconstruct both inputs across insertions, removals, and repeated lines", () => {
        const samples = [
            ["", "new"],
            ["old", ""],
            ["a\nb\na", "a\na"],
            ["a\nb\nc", "x\na\nc\ny"],
            ["{\n  \"enabled\": false\n}", "{\n  \"enabled\": true\n}"],
        ];

        for (const [previous, selected] of samples) {
            const rows = buildSideBySideDiffRows(previous, selected);
            const rebuiltPrevious = rows
                .flatMap((row) => row.left ? [row.left.text] : [])
                .join("\n");
            const rebuiltSelected = rows
                .flatMap((row) => row.right ? [row.right.text] : [])
                .join("\n");

            expect(rebuiltPrevious).toBe(previous);
            expect(rebuiltSelected).toBe(selected);
        }
    });
});
