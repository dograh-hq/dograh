const DOCS_BASE = "https://docs.dograh.com";

export const NODE_DOCUMENTATION_URLS: Record<string, string> = {
    startCall: `${DOCS_BASE}/voice-agent/start-call`,
    endCall: `${DOCS_BASE}/voice-agent/end-call`,
    agent: `${DOCS_BASE}/voice-agent/agent`,
    global: `${DOCS_BASE}/voice-agent/global`,
    apiTrigger: `${DOCS_BASE}/voice-agent/api-trigger`,
    webhook: `${DOCS_BASE}/voice-agent/webhook`,
    qaAnalysis: `${DOCS_BASE}/getting-started`,
};

export const CONTEXT_VARIABLES_DOC_URL = `${DOCS_BASE}/core-concepts/context-and-variables`;

export const TOOLS_INTRODUCTION_DOC_URL = `${DOCS_BASE}/voice-agent/tools/introduction`;

export const KNOWLEDGE_BASE_DOC_URL = `${DOCS_BASE}/voice-agent/knowledge-base`;

export const PRE_CALL_DATA_FETCH_DOC_URL = `${DOCS_BASE}/voice-agent/pre-call-data-fetch`;

export const SETTINGS_DOCUMENTATION_URLS: Record<string, string> = {
    general: `${DOCS_BASE}/voice-agent/editing-a-workflow`,
    modelOverrides: `${DOCS_BASE}/configurations/inference-providers`,
    templateVariables: `${DOCS_BASE}/voice-agent/template-variables`,

    recordings: `${DOCS_BASE}/voice-agent/pre-recorded-audio`,
    deployment: `${DOCS_BASE}/deployment/web-widget`,
};

export const TOOL_DOCUMENTATION_URLS: Record<string, string> = {
    http_api: `${DOCS_BASE}/voice-agent/tools/http-api`,
    end_call: `${DOCS_BASE}/voice-agent/tools/end-call`,
    transfer_call: `${DOCS_BASE}/voice-agent/tools/call-transfer`,
};
