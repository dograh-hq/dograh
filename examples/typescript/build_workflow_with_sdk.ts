// Build a multi-node voice agent using the Workflow SDK and save it as a draft.
//
// Requirements:
//   npm install @dograh/sdk
//
// Environment variables:
//   DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
//   DOGRAH_API_TOKEN     - API token sent as X-API-Key
//
// Run:
//   npx tsx build_workflow_with_sdk.ts

import { DograhClient, Workflow } from "@dograh/sdk";

// Replace with the numeric ID of an existing agent in your Dograh account.
// Create one via the UI or with create_workflow.ts if you don't have one yet.
const WORKFLOW_ID = 0;

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
    // Preserve the live workflow name; saveWorkflow sends name with the draft update.
    const wf = new Workflow({ client, name: existing.name });

    const greeting = await wf.add({
        type: "startCall",
        name: "greeting",
        prompt: [
            "# Goal",
            "You are a helpful agent having a conversation over voice with a human. This is a voice conversation, so transcripts can be error prone.",
            "",
            "## Flow",
            "Greet the caller warmly and ask whether they would like to continue.",
        ].join("\n"),
    });
    const qualify = await wf.add({
        type: "agentNode",
        name: "qualify",
        prompt: [
            "# Goal",
            "Qualify the lead by asking about their needs, budget, and timeline.",
            "",
            "## Rules",
            "- Keep responses short — 2-3 sentences max",
            "- Confirm all three answers before moving on",
        ].join("\n"),
    });
    const done = await wf.add({
        type: "endCall",
        name: "done",
        prompt: "Thank the caller for their time and let them know the team will follow up shortly.",
    });

    wf.edge(greeting, qualify, {
        label: "interested",
        condition: "Caller confirms they want to continue.",
    });
    wf.edge(qualify, done, {
        label: "qualified",
        condition: "All qualification questions have been answered.",
    });

    const result = await client.saveWorkflow(WORKFLOW_ID, wf);
    const nodeCount = ((result.workflow_definition?.nodes as unknown[]) ?? []).length;
    console.log(
        `Saved workflow ${result.id}: ${JSON.stringify(result.name)} ` +
        `(version=${result.version_number}, status=${result.version_status}, nodes=${nodeCount})`,
    );
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
