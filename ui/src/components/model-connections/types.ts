import type {
    FallbackPolicyServiceSelection as ApiFallbackPolicy,
    FallbackRuleServiceSelection as ApiFallbackRule,
    ModelConfigurationSpec as ApiConfigurationSpec,
    NamedModelConfigurationResponse,
    ProviderConnectionResponse,
    ServiceSelection as ApiServiceSelection,
} from "@/client/types.gen";

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

export type ProviderConnection = ProviderConnectionResponse;

// The editor materializes schema defaults; derive the wire shape from the SDK.
export type ServiceSelection = Required<ApiServiceSelection>;
export type FallbackCondition = ApiFallbackRule["condition"];
export type FallbackRule = Omit<ApiFallbackRule, "target"> & { target: ServiceSelection };
export type FallbackPolicy = Omit<Required<ApiFallbackPolicy>, "rules"> & { rules: FallbackRule[] };
export type ConfigurationSpec = Omit<ApiConfigurationSpec, ServiceRole | "llm_fallback"> &
    Required<Pick<ApiConfigurationSpec, "version" | "mode">> & {
        llm: ServiceSelection;
        llm_fallback?: FallbackPolicy | null;
    } & Partial<Record<Exclude<ServiceRole, "llm">, ServiceSelection | null>>;


export type ConfigurationEditorMode = "dograh" | "cascade" | "realtime";
export const MODE_LABELS: Record<ConfigurationEditorMode, string> = {
    dograh: "Dograh", cascade: "BYOK", realtime: "Realtime",
};

export type NamedModelConfiguration = Omit<NamedModelConfigurationResponse, "configuration"> & {
    configuration: ConfigurationSpec;
};
