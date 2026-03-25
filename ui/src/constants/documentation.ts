const DOCS_BASE = "https://docs.zoren-voice.ashtra.ai";

export const NODE_DOCUMENTATION_URLS: Record<string, string> = {
    startCall: `${DOCS_BASE}/voice-agent/start-call`,
    endCall: `${DOCS_BASE}/voice-agent/end-call`,
    agent: `${DOCS_BASE}/voice-agent/agent`,
    global: `${DOCS_BASE}/voice-agent/global`,
    apiTrigger: `${DOCS_BASE}/voice-agent/api-trigger`,
    webhook: `${DOCS_BASE}/voice-agent/webhook`,
    qaAnalysis: `${DOCS_BASE}/getting-started`,
};

export const TOOL_DOCUMENTATION_URLS: Record<string, string> = {
    http_api: `${DOCS_BASE}/voice-agent/tools/http-api`,
    end_call: `${DOCS_BASE}/voice-agent/tools/end-call`,
    transfer_call: `${DOCS_BASE}/voice-agent/tools/call-transfer`,
};
