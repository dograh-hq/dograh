"use client";

import Link from "next/link";
import { type ReactNode, useState } from "react";

import { compatibleConnections, configurationForMode, configurationMode, dograhConfiguration, selectionForConnection } from "./configuration";
import { ConfigurationSelect } from "./ConfigurationSelect";
import { SchemaFields } from "./SchemaFields";
import type { ConfigurationEditorMode, ConfigurationSpec, ModelConnectionCatalog, ProviderConnection, ServiceRole, ServiceSelection } from "./types";
import { ROLE_LABELS } from "./types";

export function ConfigurationFields({ configuration, catalog, connections, onChange, llmActions }: {
    configuration: ConfigurationSpec;
    catalog: ModelConnectionCatalog;
    connections: ProviderConnection[];
    onChange: (configuration: ConfigurationSpec) => void;
    llmActions?: ReactNode;
}) {
    const [unconfiguredMode, setUnconfiguredMode] = useState<ConfigurationEditorMode>("cascade");
    const mode = configuration.mode === "realtime" || configuration.llm.provider_connection_uuid
        ? configurationMode(configuration, connections) : unconfiguredMode;
    const dograhConnections = connections.filter(connection => connection.is_active && connection.provider === "dograh");
    const changeSelection = (role: ServiceRole, selection: ServiceSelection | null) => {
        onChange({ ...configuration, [role]: selection });
    };
    const changeSetting = (role: ServiceRole, field: string, value: unknown) => {
        const selection = configuration[role];
        if (!selection) return;
        const settings = { ...selection.settings, [field]: value };
        if (value === undefined) delete settings[field];
        onChange({ ...configuration, [role]: { ...selection, settings } });
    };
    const roles: ServiceRole[] = configuration.mode === "realtime" ? ["realtime", "llm", "embeddings"] : ["llm", "stt", "tts", "embeddings"];
    return <div className="space-y-5">
        <div className="space-y-2">
            <label htmlFor="model-configuration-mode" className="text-sm font-medium">Mode</label>
            <ConfigurationSelect id="model-configuration-mode" value={mode} onValueChange={value => {
                const nextMode = value as ConfigurationEditorMode;
                setUnconfiguredMode(nextMode);
                onChange(configurationForMode(nextMode, configuration, catalog, connections));
            }} options={[{ value: "dograh", label: "Dograh" }, { value: "cascade", label: "Cascade" }, { value: "realtime", label: "Realtime" }]} />
        </div>
        {mode === "dograh" ? <div className="space-y-5 rounded-lg border p-4">
            <div className="space-y-1.5">
                <label htmlFor="dograh-provider-connection" className="text-sm font-medium">Provider connection</label>
                <ConfigurationSelect id="dograh-provider-connection" required value={dograhConnections.some(item => item.uuid === configuration.llm.provider_connection_uuid) ? configuration.llm.provider_connection_uuid : ""} onValueChange={value => {
                    const connection = dograhConnections.find(item => item.uuid === value);
                    if (!connection) return;
                    onChange(dograhConfiguration(catalog, connection, configuration, connections));
                }} placeholder="Select a Dograh connection" options={dograhConnections.map(connection => ({ value: connection.uuid, label: connection.name }))} />
                <p className="text-xs text-muted-foreground">Add or manage connections in <Link href="/provider-connections" className="underline">Providers</Link>.</p>
                <p className="text-xs text-muted-foreground">Dograh manages speech recognition, language models, voice synthesis, and embeddings through this connection.</p>
            </div>
            {(["llm", "tts", "stt"] as const).map(role => {
                const schema = catalog.services[role]?.dograh?.settings_schema;
                const fields = role === "llm" ? ["temperature"] : role === "tts" ? ["voice", "speed"] : ["language"];
                if (!schema) return null;
                return <SchemaFields key={role} provider="dograh" role={role} schema={{ ...schema, properties: Object.fromEntries(Object.entries(schema.properties || {}).filter(([name]) => fields.includes(name))) }}
                    values={configuration[role]?.settings || {}}
                    onChange={(name, value) => changeSetting(role, name, value)} />;
            })}
        </div> : <>
            {configuration.mode === "realtime" && <p className="text-sm text-muted-foreground">Realtime also uses a separate text LLM for extraction and background tasks.</p>}
            {roles.map(role => {
                const selection = configuration[role];
                const options = compatibleConnections(catalog, connections, role);
                // Existing mixed configurations may include a managed service.
                // Keep that selection visible without replacing it during edits.
                const selected = connections.find(item => item.is_active && item.uuid === selection?.provider_connection_uuid && item.provider === "dograh");
                if (selected) options.push(selected);
                const connection = options.find(item => item.uuid === selection?.provider_connection_uuid);
                const provider = connection ? catalog.services[role]?.[connection.provider] : undefined;
                const isEmbedding = role === "embeddings";
                return <section key={role} aria-labelledby={`service-${role}`} className="space-y-4 rounded-lg border p-4">
                    <div className="flex items-center justify-between gap-3"><h3 id={`service-${role}`} className="text-sm font-semibold">{ROLE_LABELS[role]}</h3>{role === "llm" && connection?.provider !== "dograh" && llmActions}</div>
                    {isEmbedding && <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={Boolean(selection)} onChange={event => changeSelection(role,
                            event.target.checked ? (options[0] ? selectionForConnection(catalog, role, options[0]) : { provider_connection_uuid: "", settings: {} }) : null)} />
                        Enable embeddings
                    </label>}
                    {(!isEmbedding || selection) && <>
                        <div className="space-y-1.5">
                            <label htmlFor={`connection-${role}`} className="text-sm font-medium">Provider connection</label>
                            <ConfigurationSelect id={`connection-${role}`} required value={connection?.uuid || ""} onValueChange={value => {
                                const next = connections.find(item => item.uuid === value);
                                if (!next) return;
                                changeSelection(role, selectionForConnection(catalog, role, next, selection, connections));
                            }} placeholder="Select a connection" options={options.map(item => ({ value: item.uuid, label: `${item.name} · ${catalog.services[role]?.[item.provider]?.title || item.provider}` }))} />
                            <p className="text-xs text-muted-foreground">Add or manage connections in <Link href="/provider-connections" className="underline">Providers</Link>.</p>
                        </div>
                        {provider && selection && <SchemaFields key={connection?.provider} provider={connection?.provider} role={role} schema={provider.settings_schema} values={selection.settings}
                            context={connection?.connection_settings}
                            onChange={(name, value) => changeSetting(role, name, value)} />}
                </>}
                {isEmbedding && <p className="text-xs text-muted-foreground">Use the embedding model that indexed this workflow’s knowledge base. Changing it does not reindex documents.</p>}
            </section>;
        })}
        </>}
    </div>;
}
