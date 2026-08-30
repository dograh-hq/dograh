import { describe, expect, it, vi } from "vitest";

import {
    compileScenarioPrompt,
    createScenario,
    duplicateScenario,
    EMPTY_SCENARIO_DRAFT,
    nextScenarioSequence,
    parseScenarioImport,
    parseStoredScenarios,
} from "./sakinahScenarios";

describe("Sakinah scenario helpers", () => {
    it("allocates a permanent sequence after the highest existing number", () => {
        const first = createScenario({ ...EMPTY_SCENARIO_DRAFT, title: "One" }, [], "2026-01-01T00:00:00.000Z");
        const third = { ...first, id: "third", sequence: 3 };
        expect(nextScenarioSequence([first, third])).toBe(4);
    });

    it("compiles populated structured fields and omits empty fields", () => {
        const scenario = createScenario({
            ...EMPTY_SCENARIO_DRAFT,
            title: "Guarded service user",
            persona: "Initially cautious.",
            language: "Arabic",
            behaviour: "Open up after an empathic response.",
        }, [], "2026-01-01T00:00:00.000Z");
        const prompt = compileScenarioPrompt(scenario);
        expect(prompt).toContain("Scenario: Guarded service user");
        expect(prompt).toContain("Language: Arabic");
        expect(prompt).toContain("Behaviour:\nOpen up after an empathic response.");
        expect(prompt).not.toContain("Age:");
    });

    it("handles malformed stored data safely", () => {
        expect(parseStoredScenarios("not-json")).toEqual([]);
        expect(parseStoredScenarios('{"unexpected":true}')).toEqual([]);
        expect(parseStoredScenarios('[{"id":"incomplete"}]')).toEqual([]);
    });

    it("duplicates with a new identity, sequence, and timestamps", () => {
        vi.stubGlobal("crypto", { randomUUID: vi.fn().mockReturnValueOnce("original").mockReturnValueOnce("copy") });
        const original = createScenario({ ...EMPTY_SCENARIO_DRAFT, title: "Original" }, [], "2026-01-01T00:00:00.000Z");
        const copy = duplicateScenario(original, [original], "2026-02-01T00:00:00.000Z");
        expect(copy).toMatchObject({ id: "copy", sequence: 2, title: "Copy of Original", createdAt: "2026-02-01T00:00:00.000Z", updatedAt: "2026-02-01T00:00:00.000Z" });
        vi.unstubAllGlobals();
    });

    it("imports fenced JSON arrays and assigns new sequences", () => {
        const imported = parseScenarioImport(`\`\`\`json
[{"title":"Imported one","persona":"A cautious person.","behaviour":"Opens up slowly."},{"mode":"freestyle","title":"Imported two","prompt":"Roleplay a worried service user."}]
\`\`\``, [], "2026-03-01T00:00:00.000Z");
        expect(imported).toHaveLength(2);
        expect(imported.map((scenario) => scenario.sequence)).toEqual([1, 2]);
        expect(imported[0].mode).toBe("structured");
        expect(imported[1].freestylePrompt).toBe("Roleplay a worried service user.");
    });

    it("imports a wrapped scenario collection and rejects invalid scenarios", () => {
        const imported = parseScenarioImport(JSON.stringify({ scenarios: [{ title: "One", prompt: "A single scenario." }] }));
        expect(imported[0].title).toBe("One");
        expect(() => parseScenarioImport(JSON.stringify([{ title: "Missing behaviour", persona: "Person" }]))).toThrow("title, persona, and behaviour");
    });
});
