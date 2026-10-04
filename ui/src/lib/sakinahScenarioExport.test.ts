import JSZip from "jszip";
import { describe, expect, it } from "vitest";

import { scenarioArchive } from "./sakinahScenarioExport";
import { createScenario, EMPTY_SCENARIO_DRAFT, parseScenarioImport } from "./sakinahScenarios";

describe("scenario ZIP export", () => {
    it("keeps structured and freestyle scenarios importable, including Arabic", async () => {
        const structured = createScenario({ ...EMPTY_SCENARIO_DRAFT, title: "قلق", persona: "Person", behaviour: "Talk" }, []);
        const freestyle = createScenario({ ...EMPTY_SCENARIO_DRAFT, mode: "freestyle", title: "Freestyle", freestylePrompt: "Speak freely" }, [structured]);
        const bytes = await scenarioArchive([structured, freestyle]).generateAsync({ type: "uint8array" });
        const zip = await JSZip.loadAsync(bytes);
        const entries = Object.values(zip.files).filter((file) => !file.dir);
        expect(entries).toHaveLength(2);
        const restored = (await Promise.all(entries.map(async (file) => parseScenarioImport(await file.async("string"))))).flat();
        expect(restored.map((item) => item.title)).toEqual(["قلق", "Freestyle"]);
        expect(restored[1].freestylePrompt).toBe("Speak freely");
    });
    it("does not let IDs create folders or overwrite another scenario", () => {
        const scenario = createScenario({ ...EMPTY_SCENARIO_DRAFT, title: "Test", persona: "Person", behaviour: "Talk" }, []);
        const files = scenarioArchive([{ ...scenario, id: "../../unsafe" }]).files;
        expect(Object.keys(files)).toHaveLength(1);
        expect(Object.keys(files)[0]).not.toContain("/");
    });
});
