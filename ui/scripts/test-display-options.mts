// Golden-test parity: run every case in
// api/services/workflow/node_specs/display_options_fixtures.json through
// the TypeScript evaluator and assert the result matches `expected`.
//
// Run via `npm run test:display-options` from ui/, or `node
// ui/scripts/test-display-options.mts` directly (Node 24+ strips TS types
// natively).
//
// Mirrors `api/tests/test_display_options_evaluator.py`.

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { evaluateDisplayOptions } from "../src/components/flow/renderer/displayOptions.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_PATH = resolve(
    __dirname,
    "../../api/services/workflow/node_specs/display_options_fixtures.json",
);

interface Case {
    name: string;
    rules: Parameters<typeof evaluateDisplayOptions>[0];
    values: Record<string, unknown>;
    expected: boolean;
}

const data = JSON.parse(readFileSync(FIXTURES_PATH, "utf-8")) as { cases: Case[] };

let failed = 0;
for (const c of data.cases) {
    const actual = evaluateDisplayOptions(c.rules, c.values);
    if (actual !== c.expected) {
        console.error(
            `FAIL ${c.name}: expected ${c.expected}, got ${actual}\n` +
                `  rules=${JSON.stringify(c.rules)} values=${JSON.stringify(c.values)}`,
        );
        failed++;
    } else {
        console.log(`PASS ${c.name}`);
    }
}

if (failed > 0) {
    console.error(`\n${failed} of ${data.cases.length} cases failed`);
    process.exit(1);
}
console.log(`\nAll ${data.cases.length} cases passed`);
