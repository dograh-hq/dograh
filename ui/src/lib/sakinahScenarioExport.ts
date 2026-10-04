import JSZip from "jszip";

import { formatScenarioNumber, type Scenario } from "./sakinahScenarios";

export function scenarioArchive(scenarios: readonly Scenario[]): JSZip {
    const zip = new JSZip();
    for (const scenario of scenarios) {
        const id = scenario.id.replace(/[^a-zA-Z0-9_-]/g, "_");
        zip.file(`${formatScenarioNumber(scenario.sequence)}-${id}.json`, JSON.stringify(scenario, null, 2));
    }
    return zip;
}
