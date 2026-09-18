export const DOCS_BASE = "/docs";

/**
 * Rewrite any docs.dograh.com URLs from the backend to the internal /docs route.
 */
export function rewriteDocsUrl(_url: string): string {
    return DOCS_BASE;
}

/**
 * Rewrite "Dograh" mentions in backend-provided descriptions to "Vani Studio".
 */
export function rewriteBrandText(text: string): string {
    return text.replace(/\bDograh\b/g, "Vani Studio");
}

export const NODE_DOCUMENTATION_URLS: Record<string, string> = {
    startCall: DOCS_BASE,
    endCall: DOCS_BASE,
    agent: DOCS_BASE,
    global: DOCS_BASE,
    apiTrigger: DOCS_BASE,
    webhook: DOCS_BASE,
    qaAnalysis: DOCS_BASE,
};

export const CONTEXT_VARIABLES_DOC_URL = DOCS_BASE;

export const TOOLS_INTRODUCTION_DOC_URL = DOCS_BASE;

export const KNOWLEDGE_BASE_DOC_URL = DOCS_BASE;

export const PRE_CALL_DATA_FETCH_DOC_URL = DOCS_BASE;

export const SETTINGS_DOCUMENTATION_URLS: Record<string, string> = {
    general: DOCS_BASE,
    modelOverrides: DOCS_BASE,
    templateVariables: DOCS_BASE,
    recordings: DOCS_BASE,
    deployment: DOCS_BASE,
};

export const WIDGET_CONTEXT_DOC_URL = DOCS_BASE;

export const WIDGET_MODE_DOCUMENTATION_URLS: Record<"floating" | "inline" | "headless", string> = {
    floating: DOCS_BASE,
    inline: DOCS_BASE,
    headless: DOCS_BASE,
};

export const TOOL_DOCUMENTATION_URLS: Record<string, string> = {
    http_api: DOCS_BASE,
    end_call: DOCS_BASE,
    transfer_call: DOCS_BASE,
};
