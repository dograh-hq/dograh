// Create a voice agent with the Workflow SDK, validate it, and publish.
//
// createWorkflow stores version 1 as published. Later edits go through
// saveWorkflow, which writes a draft. validateWorkflow checks that draft,
// and publishWorkflow promotes it so runtime uses the new graph.
//
// Campaigns dial a CSV that is already stored in Dograh. Leave
// CAMPAIGN_SOURCE_ID empty to stop after publish. Set it to also create
// and start the campaign.
//
// Requirements:
//   npm install @dograh/sdk
//
// Environment variables:
//   DOGRAH_API_ENDPOINT  - Dograh API base URL (e.g. http://localhost:8000)
//   DOGRAH_API_TOKEN     - API token sent as X-API-Key
//
// Run:
//   npx tsx publish_agent_and_campaign.ts

import { DograhClient, Workflow, ApiError } from "@dograh/sdk";

const WORKFLOW_NAME = "SDK publish example";
const CAMPAIGN_NAME = "SDK campaign example";

// CSV object key from the Dograh campaign uploader, for example
// "campaigns/<org_id>/<uuid>_contacts.csv". The file needs a phone_number
// column. Leave empty to stop after the agent is published.
const CAMPAIGN_SOURCE_ID = "";

const GREETING_PROMPT = [
    "# Goal",
    "You are a helpful agent having a conversation over voice with a human. Greet the caller and ask whether they would like to continue.",
].join("\n");

// Applied on the draft so publish promotes a change, not a no-op rewrite.
const GREETING_PROMPT_DRAFT = `${GREETING_PROMPT}\n\nKeep every reply to one or two sentences.`;

async function buildAgent(client: DograhClient, greetingPrompt: string): Promise<Workflow> {
    const wf = new Workflow({ client, name: WORKFLOW_NAME });
    const greeting = await wf.add({
        type: "startCall",
        name: "greeting",
        prompt: greetingPrompt,
    });
    const qualify = await wf.add({
        type: "agentNode",
        name: "qualify",
        prompt: "Ask what they need help with, then confirm you heard them correctly before ending the call.",
    });
    const done = await wf.add({
        type: "endCall",
        name: "done",
        prompt: "Thank the caller and say goodbye.",
    });
    wf.edge(greeting, qualify, {
        label: "interested",
        condition: "Caller wants to continue the conversation.",
    });
    wf.edge(greeting, done, {
        label: "declined",
        condition: "Caller does not want to continue.",
    });
    wf.edge(qualify, done, {
        label: "done",
        condition: "The caller's request has been confirmed.",
    });
    return wf;
}

async function main(): Promise<void> {
    const apiEndpoint = process.env.DOGRAH_API_ENDPOINT ?? "http://localhost:8000";
    const apiToken = process.env.DOGRAH_API_TOKEN;
    if (!apiToken) throw new Error("DOGRAH_API_TOKEN is required");

    const client = new DograhClient({
        baseUrl: apiEndpoint,
        apiKey: apiToken,
    });

    const createdDefinition = await buildAgent(client, GREETING_PROMPT);
    const created = await client.createWorkflow({
        body: {
            name: WORKFLOW_NAME,
            workflow_definition: createdDefinition.toJson() as unknown as Record<string, unknown>,
        },
    });
    console.log(`Created workflow ${created.id}: ${JSON.stringify(created.name)} (status=${created.status})`);

    const draft = await buildAgent(client, GREETING_PROMPT_DRAFT);
    const saved = await client.saveWorkflow(created.id, draft);
    console.log(
        `Saved draft ${saved.id}: version=${saved.version_number} status=${saved.version_status}`,
    );

    try {
        const validated = await client.validateWorkflow(created.id);
        console.log(`Validated workflow ${created.id}: is_valid=${validated.is_valid}`);
    } catch (err) {
        if (err instanceof ApiError) {
            throw new Error(`Validation failed: ${err.message}`);
        }
        throw err;
    }

    const published = await client.publishWorkflow(created.id);
    console.log(
        `Published workflow ${created.id}: version=${published.version_number} status=${published.status}`,
    );

    const runs = await client.listWorkflowRuns(created.id, { limit: 5 });
    console.log(`Workflow runs so far: ${runs.total_count}`);
    // Fetch one run with client.getWorkflowRun(created.id, runId)
    // once a call or campaign has produced a run id.

    if (!CAMPAIGN_SOURCE_ID) {
        console.log(
            "No CAMPAIGN_SOURCE_ID set. Upload a CSV in Dograh, paste the source id at the top of this file, and re-run to call createCampaign and startCampaign. pauseCampaign, resumeCampaign, and getCampaignProgress take the same id. Starting a campaign also requires a telephony configuration on the organization.",
        );
        return;
    }

    const campaign = await client.createCampaign({
        body: {
            name: CAMPAIGN_NAME,
            workflow_id: created.id,
            source_type: "csv",
            source_id: CAMPAIGN_SOURCE_ID,
        },
    });
    console.log(`Created campaign ${campaign.id}: state=${campaign.state}`);

    const started = await client.startCampaign(campaign.id);
    const progress = await client.getCampaignProgress(started.id);
    console.log(
        `Started campaign ${started.id}: state=${progress.state} processed=${progress.processed_rows}/${progress.total_rows}`,
    );
}

main().catch((err) => {
    console.error(err);
    process.exit(1);
});
