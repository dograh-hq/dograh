import type { ConfigurationEditorMode, ConfigurationSpec, FieldSchema, ModelConnectionCatalog, ProviderCatalogEntry, ProviderConnection, ServiceRole, ServiceSelection } from "./types";
import { ROLES } from "./types";

export function connectionProviders(catalog: ModelConnectionCatalog): Record<string, ProviderCatalogEntry> {
    const providers: Record<string, ProviderCatalogEntry> = {};
    for (const role of ROLES) {
        for (const [provider, entry] of Object.entries(catalog.services[role] || {})) {
            const previous = providers[provider];
            providers[provider] = previous ? {
                ...previous,
                credential_fields: { ...entry.credential_fields, ...previous.credential_fields },
                connection_fields: { ...entry.connection_fields, ...previous.connection_fields },
                // Some providers accept different authentication shapes across
                // services. Show every connection field without requiring the
                // credentials of every service just to add a provider account.
                credential_required: previous.credential_required?.filter(field => entry.credential_required?.includes(field)),
                connection_required: previous.connection_required?.filter(field => entry.connection_required?.includes(field)),
            } : { ...entry };
        }
    }
    return providers;
}

export function resolveFieldSchema(schema: FieldSchema, root?: FieldSchema): FieldSchema {
    if (!schema.$ref) return schema;
    const name = schema.$ref.split("/").pop() || "";
    return { ...root?.$defs?.[name], ...schema };
}

export function fieldForModel(schema: FieldSchema, values: Record<string, unknown>): FieldSchema {
    let result = schema;
    if (schema.custom_endpoint) {
        const endpoint = schema.custom_endpoint;
        let isCustom = false;
        try {
            isCustom = Boolean(values[endpoint.field]) && new URL(String(values[endpoint.field])).hostname !== endpoint.default_hostname;
        } catch { /* Incomplete URL: retain standard constraints until valid. */ }
        result = { ...result, maximum: isCustom ? undefined : endpoint.maximum,
            anyOf: result.anyOf?.map(option => option.type === "number" ? { ...option, maximum: isCustom ? undefined : endpoint.maximum } : option) };
        if (isCustom) result.description = endpoint.description;
    }
    const model = String(values.model || "");
    const rule = schema.model_constraints?.find(item => new RegExp(item.pattern).test(model));
    if (rule) result = { ...result, ...rule };
    return result;
}

export function isFieldVisible(schema: FieldSchema, model?: unknown): boolean {
    return schema.supported !== false
        && (!schema.visible_for_models || schema.visible_for_models.includes(String(model || "")))
        && (!schema.hidden_for_models || !schema.hidden_for_models.includes(String(model || "")))
        && !schema.readOnly;
}

export function schemaDefaults(schema?: FieldSchema): Record<string, unknown> {
    return Object.fromEntries(Object.entries(schema?.properties || {})
        .filter(([, field]) => !field.readOnly && field.default !== undefined)
        .map(([name, field]) => [name, field.default]));
}

export function compatibleConnections(catalog: ModelConnectionCatalog, connections: ProviderConnection[], role: ServiceRole) {
    return connections.filter(connection => connection.is_active && connection.provider !== "dograh" && Boolean(catalog.services[role]?.[connection.provider]));
}

export function configurationMode(configuration: ConfigurationSpec, connections: ProviderConnection[]): ConfigurationEditorMode {
    if (configuration.mode === "realtime") return "realtime";
    return connections.find(connection => connection.uuid === configuration.llm.provider_connection_uuid)?.provider === "dograh" ? "dograh" : "cascade";
}

export function dograhConfiguration(catalog: ModelConnectionCatalog, connection?: ProviderConnection, previous?: ConfigurationSpec, connections: ProviderConnection[] = []): ConfigurationSpec {
    const result: ConfigurationSpec = { version: 3, mode: "pipeline", llm: { provider_connection_uuid: "", settings: {} } };
    for (const role of ["llm", "stt", "tts", "embeddings"] as const) {
        result[role] = connection
            ? selectionForConnection(catalog, role, connection, previous?.[role], connections)
            : { provider_connection_uuid: "", settings: {} };
    }
    return result;
}

export function selectionForConnection(catalog: ModelConnectionCatalog, role: ServiceRole, connection: ProviderConnection, previous?: ServiceSelection | null, connections: ProviderConnection[] = []): ServiceSelection {
    const oldConnection = connections.find(item => item.uuid === previous?.provider_connection_uuid);
    return {
        provider_connection_uuid: connection.uuid,
        settings: oldConnection?.provider === connection.provider
            ? { ...previous?.settings }
            : schemaDefaults(catalog.services[role]?.[connection.provider]?.settings_schema),
    };
}

export function emptyConfiguration(catalog: ModelConnectionCatalog, connections: ProviderConnection[], mode?: ConfigurationEditorMode): ConfigurationSpec {
    const dograh = connections.find(connection => connection.is_active && connection.provider === "dograh");
    const selectedMode = mode || (dograh ? "dograh" : "cascade");
    if (selectedMode === "dograh") return dograhConfiguration(catalog, dograh);
    const result: ConfigurationSpec = { version: 3, mode: selectedMode === "realtime" ? "realtime" : "pipeline", llm: { provider_connection_uuid: "", settings: {} }, embeddings: null };
    for (const role of selectedMode === "realtime" ? ["llm", "realtime"] as const : ["llm", "stt", "tts"] as const) {
        const connection = compatibleConnections(catalog, connections, role)[0];
        result[role] = connection ? selectionForConnection(catalog, role, connection) : { provider_connection_uuid: "", settings: {} };
    }
    return result;
}

export function configurationForMode(mode: ConfigurationEditorMode, configuration: ConfigurationSpec, catalog: ModelConnectionCatalog, connections: ProviderConnection[]): ConfigurationSpec {
    if (mode === "dograh") {
        const dograh = connections.find(connection => connection.is_active && connection.provider === "dograh" && connection.uuid === configuration.llm.provider_connection_uuid)
            || connections.find(connection => connection.is_active && connection.provider === "dograh");
        return dograhConfiguration(catalog, dograh, configuration, connections);
    }
    const result = emptyConfiguration(catalog, connections, mode);
    for (const role of mode === "realtime" ? ["llm", "realtime", "embeddings"] as const : ["llm", "stt", "tts", "embeddings"] as const) {
        const previous = configuration[role];
        if (previous && compatibleConnections(catalog, connections, role).some(connection => connection.uuid === previous.provider_connection_uuid)) result[role] = previous;
    }
    return result;
}

export function cleanSettings(settings: Record<string, unknown>, schema?: FieldSchema, connectionSettings: Record<string, unknown> = {}) {
    const values = { ...schemaDefaults(schema), ...connectionSettings, ...settings };
    return Object.fromEntries(Object.entries(settings).filter(([name]) => {
        const field = schema?.properties?.[name];
        return Boolean(field && isFieldVisible(fieldForModel(field, values), values.model));
    }));
}

export function cleanConfiguration(configuration: ConfigurationSpec, catalog: ModelConnectionCatalog, connections: ProviderConnection[]): ConfigurationSpec {
    const normalized = configurationMode(configuration, connections) === "dograh"
        ? dograhConfiguration(catalog, connections.find(connection => connection.uuid === configuration.llm.provider_connection_uuid), configuration, connections)
        : configuration;
    const result = { ...normalized };
    // Do not send inactive mode roles; stale settings must never authorize or execute.
    const roles: ServiceRole[] = configuration.mode === "pipeline" ? ["llm", "stt", "tts", "embeddings"] : ["llm", "realtime", "embeddings"];
    for (const role of ROLES) {
        if (!roles.includes(role)) { delete result[role]; continue; }
        const selection = result[role];
        if (!selection) continue;
        const connection = connections.find(item => item.uuid === selection.provider_connection_uuid);
        result[role] = { ...selection, settings: cleanSettings(selection.settings, connection ? catalog.services[role]?.[connection.provider]?.settings_schema : undefined, connection?.connection_settings) };
    }
    return result;
}
