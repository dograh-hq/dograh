// Load an existing workflow, edit a node prompt, and save it as a draft.
//
// Requirements:
//   npm install @dograh/sdk
//
// Environment variables:
//   DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
//   DOGRAH_API_TOKEN     - API token sent as X-API-Key
//
// Run:
//   npx tsx load_and_edit_workflow.ts

import { DograhClient } from "@dograh/sdk";

// Replace with the numeric ID of an existing agent in your Dograh account.
const WORKFLOW_ID = 0;

// Sentence appended to the startCall node's prompt when the script runs.
const PROMPT_SUFFIX = " Please be concise — keep all responses under two sentences.";

async function main(): Promise<void> {
    const apiEndpoint = process.env.DOGRAH_API_ENDPOINT ?? "http://localhost:8000";
    const apiToken = process.env.DOGRAH_API_TOKEN;

    if (!apiToken) throw new Error("DOGRAH_API_TOKEN is required");
    if (WORKFLOW_ID === 0) throw new Error("Set WORKFLOW_ID at the top of this file to an existing workflow ID");

    const client = new DograhClient({
        baseUrl: apiEndpoint,
        apiKey: apiToken,
    });

    const existing = await client.getWorkflow(WORKFLOW_ID);
    console.log(`Loaded workflow ${existing.id}: ${JSON.stringify(existing.name)} (status=${existing.status})`);

    const definition = structuredClone(existing.workflow_definition) as {
        nodes?: Array<{ type?: string; data?: Record<string, unknown> }>;
    };

    for (const node of definition.nodes ?? []) {
        if (node.type === "startCall") {
            node.data = node.data ?? {};
            node.data.prompt = ((node.data.prompt as string) ?? "") + PROMPT_SUFFIX;
            break;
        }
    }

    const result = await client.updateWorkflow(WORKFLOW_ID, {
        body: {
            name: existing.name,
            workflow_definition: definition as Record<string, unknown>,
        },
    });
    console.log(
        `Saved draft for workflow ${result.id}: ${JSON.stringify(result.name)} ` +
        `(version=${result.version_number}, status=${result.version_status})`,
    );
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
