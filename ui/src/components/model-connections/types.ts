// Catalog field metadata comes from the backend provider registry. It describes
// rendering only; the server remains authoritative for semantic validation.
export interface FieldSchema {
    type?: string;
    title?: string;
    default?: unknown;
    description?: string;
    docs_url?: string;
    docs_label?: string;
    anyOf?: FieldSchema[];
    items?: FieldSchema;
    enum?: string[];
    examples?: string[];
    minimum?: number;
    maximum?: number;
    readOnly?: boolean;
    format?: string;
    multiline?: boolean;
    allow_custom_input?: boolean;
    model_options?: Record<string, string[]>;
    visible_for_models?: string[];
    hidden_for_models?: string[];
    supported?: boolean;
    model_constraints?: (FieldSchema & { pattern: string })[];
    custom_endpoint?: { field: string; default_hostname: string; maximum: number; description: string };
    $ref?: string;
    properties?: Record<string, FieldSchema>;
    required?: string[];
    $defs?: Record<string, FieldSchema>;
}

export const ROLES = ["llm", "stt", "tts", "realtime", "embeddings"] as const;
export type ServiceRole = typeof ROLES[number];
export const ROLE_LABELS: Record<ServiceRole, string> = {
    llm: "LLM", stt: "STT", tts: "TTS", realtime: "Realtime", embeddings: "Embedding",
};

export interface ProviderCatalogEntry {
    title?: string;
    credential_fields: Record<string, FieldSchema>;
    connection_fields: Record<string, FieldSchema>;
    credential_required?: string[];
    connection_required?: string[];
    settings_schema: FieldSchema;
}

export interface ModelConnectionCatalog {
    services: Partial<Record<ServiceRole, Record<string, ProviderCatalogEntry>>>;
}

export interface ProviderConnection {
    uuid: string;
    name: string;
    provider: string;
    connection_settings: Record<string, unknown>;
    configured_credentials: string[];
    revision: number;
    is_active: boolean;
}

export interface ServiceSelection {
    provider_connection_uuid: string;
    settings: Record<string, unknown>;
}

export type FallbackCondition = { type: "no_output"; after_ms: number } | { type: "error" };
export interface FallbackRule {
    condition: FallbackCondition;
    target: ServiceSelection;
}
export interface FallbackPolicy {
    version: 1;
    rules: FallbackRule[];
}

export type ConfigurationSpec = {
    version: 3;
    mode: "pipeline" | "realtime";
    llm: ServiceSelection;
    llm_fallback?: FallbackPolicy | null;
    stt?: ServiceSelection | null;
    tts?: ServiceSelection | null;
    realtime?: ServiceSelection | null;
    embeddings?: ServiceSelection | null;
};


export type ConfigurationEditorMode = "dograh" | "cascade" | "realtime";
export const MODE_LABELS: Record<ConfigurationEditorMode, string> = {
    dograh: "Dograh", cascade: "Cascade", realtime: "Realtime",
};

export interface NamedModelConfiguration {
    uuid: string;
    name: string;
    configuration: ConfigurationSpec;
    revision: number;
    is_active: boolean;
}
