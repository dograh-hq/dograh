// Create a new workflow using the TypeScript SDK.
//
// Requirements:
//   npm install @dograh/sdk
//
// Environment variables:
//   DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
//   DOGRAH_API_TOKEN     - API token sent as X-API-Key
//
// Run:
//   npx tsx create_workflow.ts

import { DograhClient } from "@dograh/sdk";

const WORKFLOW_NAME = "My SDK-created agent";

// A minimal starter agent with a single `startCall` node that greets the user.
// Open the new agent in the Dograh UI to extend it, or edit this object and
// re-run to tweak the starting definition.
const WORKFLOW_DEFINITION = {
    nodes: [
        {
            id: "1",
            type: "startCall",
            position: { x: 271, y: 4 },
            data: {
                name: "start call",
                greeting_type: "text",
                prompt: [
                    "# Goal",
                    "You are a helpful agent having a conversation over voice with a human. This is a voice conversation, so transcripts can be error prone.",
                    "",
                    "## Rules",
                    "- Language: UK English",
                    "- Keep responses short — 2-3 sentences max",
                    "- If you have to repeat something you said in your previous two turns, rephrase while keeping the same meaning.",
                    "",
                    "## Speech Handling",
                    "- Accept variations: yes/yeah/yep/aye, no/nah/nope",
                    '- If user says "sorry?" or "pardon me" or "can you repeat", just repeat what you just said.',
                    "",
                    "### Flow",
                    'Start by saying "Hi". Be polite and courteous.',
                ].join("\n"),
                allow_interrupt: false,
                add_global_prompt: false,
                delayed_start: false,
                delayed_start_duration: 2,
                extraction_enabled: false,
                pre_call_fetch_enabled: false,
            },
        },
    ],
    edges: [],
    viewport: { x: 0, y: 0, zoom: 1 },
};

async function main(): Promise<void> {
    const apiEndpoint = process.env.DOGRAH_API_ENDPOINT ?? "http://localhost:8000";
    const apiToken = process.env.DOGRAH_API_TOKEN;

    if (!apiToken) throw new Error("DOGRAH_API_TOKEN is required");

    const client = new DograhClient({
        baseUrl: apiEndpoint,
        apiKey: apiToken,
    });

    const workflow = await client.createWorkflow({
        body: {
            name: WORKFLOW_NAME,
            workflow_definition: WORKFLOW_DEFINITION,
        },
    });
    console.log(
        `Created workflow ${workflow.id}: ${JSON.stringify(workflow.name)} (status=${workflow.status})`,
    );
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
