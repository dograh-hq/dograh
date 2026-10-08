"use client";

import Link from "next/link";
import { type ReactNode, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { compatibleConnections, configurationForMode, configurationMode, dograhConfiguration, selectionForConnection } from "./configuration";
import { ConfigurationSelect } from "./ConfigurationSelect";
import { SchemaFields } from "./SchemaFields";
import type { ConfigurationEditorMode, ConfigurationSpec, FieldSchema, ModelConnectionCatalog, ProviderConnection, ServiceRole, ServiceSelection } from "./types";
import { ROLE_LABELS } from "./types";

export type ConfigurationFieldsDensity = "full" | "compact";

// Dograh manages its services; only these settings are the user's to change.
const DOGRAH_FIELDS: Partial<Record<ServiceRole, string[]>> = { llm: ["temperature"], tts: ["voice", "speed"], stt: ["language"] };
const MODE_OPTIONS = [{ value: "dograh", label: "Dograh" }, { value: "cascade", label: "Cascade" }, { value: "realtime", label: "Realtime" }];
const TAB_COLUMNS: Record<number, string> = { 1: "grid-cols-1", 2: "grid-cols-2", 3: "grid-cols-3" };

function restrictSchema(schema: FieldSchema | undefined, allowed?: string[]): FieldSchema | undefined {
    if (!schema || !allowed) return schema;
    return { ...schema, properties: Object.fromEntries(Object.entries(schema.properties || {}).filter(([name]) => allowed.includes(name))) };
}

function hasFields(schema: FieldSchema | undefined): schema is FieldSchema {
    return Boolean(schema && Object.keys(schema.properties || {}).length);
}

/**
 * The one editor for a model configuration spec, at two densities.
 *
 * "full" is the Models page: every service as a section, every setting,
 * fallbacks and embeddings. "compact" is the agent editor's Model view: the
 * same mode switch and account selects, one tab per service laid out
 * horizontally, the settings named in `fields` (all of them when omitted),
 * and no embeddings or fallback actions. Both produce the same
 * ConfigurationSpec.
 */
export function ConfigurationFields({ configuration, catalog, connections, onChange, llmActions, density = "full", fields }: {
    configuration: ConfigurationSpec;
    catalog: ModelConnectionCatalog;
    connections: ProviderConnection[];
    onChange: (configuration: ConfigurationSpec) => void;
    llmActions?: ReactNode;
    density?: ConfigurationFieldsDensity;
    /** Setting names to show for each service; everything else stays as it is. */
    fields?: string[];
}) {
    const compact = density === "compact";
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
    const changeMode = (value: string) => {
        const nextMode = value as ConfigurationEditorMode;
        setUnconfiguredMode(nextMode);
        onChange(configurationForMode(nextMode, configuration, catalog, connections));
    };
    const changeDograhConnection = (value: string) => {
        const connection = dograhConnections.find(item => item.uuid === value);
        if (connection) onChange(dograhConfiguration(catalog, connection, configuration, connections));
    };
    const dograhConnectionValue = dograhConnections.some(item => item.uuid === configuration.llm.provider_connection_uuid) ? configuration.llm.provider_connection_uuid : "";
    const allRoles: ServiceRole[] = configuration.mode === "realtime" ? ["realtime", "llm", "embeddings"] : ["llm", "stt", "tts", "embeddings"];

    // Per-service account and settings, shared by both densities.
    const service = (role: ServiceRole) => {
        const selection = configuration[role];
        const options = compatibleConnections(catalog, connections, role);
        // Existing mixed configurations may include a managed service.
        // Keep that selection visible without replacing it during edits.
        const selected = connections.find(item => item.is_active && item.uuid === selection?.provider_connection_uuid && item.provider === "dograh");
        if (selected) options.push(selected);
        const connection = options.find(item => item.uuid === selection?.provider_connection_uuid);
        const provider = connection ? catalog.services[role]?.[connection.provider] : undefined;
        const schema = restrictSchema(provider?.settings_schema, fields);
        const account = <ConfigurationSelect id={`connection-${role}`} required value={connection?.uuid || ""} onValueChange={value => {
            const next = connections.find(item => item.uuid === value);
            if (!next) return;
            changeSelection(role, selectionForConnection(catalog, role, next, selection, connections));
        }} placeholder="Select a connection" options={options.map(item => ({ value: item.uuid, label: `${item.name} · ${catalog.services[role]?.[item.provider]?.title || item.provider}` }))} />;
        const settings = schema && selection && <SchemaFields key={connection?.provider} provider={connection?.provider} role={role} schema={schema} values={selection.settings}
            context={connection?.connection_settings} onChange={(name, value) => changeSetting(role, name, value)} />;
        return { selection, options, account, settings };
    };

    if (compact) {
        const roles = mode === "dograh" ? (["llm", "tts", "stt"] as ServiceRole[]) : allRoles.filter(role => role !== "embeddings");
        return <div className="space-y-3">
            <div className="flex items-center gap-2">
                <label htmlFor="model-configuration-mode" className="sr-only">Mode</label>
                <div className="w-32 shrink-0"><ConfigurationSelect id="model-configuration-mode" value={mode} onValueChange={changeMode} options={MODE_OPTIONS} /></div>
                {mode === "dograh" && <div className="min-w-0 flex-1">
                    <label htmlFor="dograh-provider-connection" className="sr-only">Account</label>
                    <ConfigurationSelect id="dograh-provider-connection" required value={dograhConnectionValue} onValueChange={changeDograhConnection}
                        placeholder="Select a Dograh connection" options={dograhConnections.map(connection => ({ value: connection.uuid, label: connection.name }))} />
                </div>}
            </div>
            <Tabs key={`${mode}-${configuration.mode}`} defaultValue={roles[0]}>
                <TabsList className={`grid w-full ${TAB_COLUMNS[roles.length] || "grid-cols-3"}`}>
                    {roles.map(role => <TabsTrigger key={role} value={role}>{ROLE_LABELS[role]}</TabsTrigger>)}
                </TabsList>
                {roles.map(role => <TabsContent key={role} value={role} className="mt-3 space-y-3">
                    {mode === "dograh" ? (() => {
                        const allowed = (DOGRAH_FIELDS[role] || []).filter(name => !fields || fields.includes(name));
                        const schema = restrictSchema(catalog.services[role]?.dograh?.settings_schema, allowed);
                        return hasFields(schema)
                            ? <SchemaFields provider="dograh" role={role} schema={schema} values={configuration[role]?.settings || {}} onChange={(name, value) => changeSetting(role, name, value)} />
                            : <p className="text-xs text-muted-foreground">Managed by Dograh.</p>;
                    })() : (() => {
                        const { account, settings } = service(role);
                        return <>
                            <div><label htmlFor={`connection-${role}`} className="sr-only">Account</label>{account}</div>
                            {settings}
                        </>;
                    })()}
                </TabsContent>)}
            </Tabs>
        </div>;
    }

    return <div className="space-y-5">
        <div className="space-y-2">
            <label htmlFor="model-configuration-mode" className="text-sm font-medium">Mode</label>
            <ConfigurationSelect id="model-configuration-mode" value={mode} onValueChange={changeMode} options={MODE_OPTIONS} />
        </div>
        {mode === "dograh" ? <div className="space-y-5 rounded-lg border p-4">
            <div className="space-y-1.5">
                <label htmlFor="dograh-provider-connection" className="text-sm font-medium">Provider connection</label>
                <ConfigurationSelect id="dograh-provider-connection" required value={dograhConnectionValue} onValueChange={changeDograhConnection}
                    placeholder="Select a Dograh connection" options={dograhConnections.map(connection => ({ value: connection.uuid, label: connection.name }))} />
                <p className="text-xs text-muted-foreground">Add or manage connections in <Link href="/provider-connections" className="underline">Providers</Link>.</p>
                <p className="text-xs text-muted-foreground">Dograh manages speech recognition, language models, voice synthesis, and embeddings through this connection.</p>
            </div>
            {(["llm", "tts", "stt"] as const).map(role => {
                const allowed = (DOGRAH_FIELDS[role] || []).filter(name => !fields || fields.includes(name));
                const schema = restrictSchema(catalog.services[role]?.dograh?.settings_schema, allowed);
                if (!hasFields(schema)) return null;
                return <SchemaFields key={role} provider="dograh" role={role} schema={schema}
                    values={configuration[role]?.settings || {}}
                    onChange={(name, value) => changeSetting(role, name, value)} />;
            })}
        </div> : <>
            {configuration.mode === "realtime" && <p className="text-sm text-muted-foreground">Realtime also uses a separate text LLM for extraction and background tasks.</p>}
            {allRoles.map(role => {
                const { selection, options, account, settings } = service(role);
                const isEmbedding = role === "embeddings";
                return <section key={role} aria-labelledby={`service-${role}`} className="space-y-4 rounded-lg border p-4">
                    <div className="flex items-center justify-between gap-3"><h3 id={`service-${role}`} className="text-sm font-semibold">{ROLE_LABELS[role]}</h3>{role === "llm" && connections.find(item => item.uuid === selection?.provider_connection_uuid)?.provider !== "dograh" && llmActions}</div>
                    {isEmbedding && <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" checked={Boolean(selection)} onChange={event => changeSelection(role,
                            event.target.checked ? (options[0] ? selectionForConnection(catalog, role, options[0]) : { provider_connection_uuid: "", settings: {} }) : null)} />
                        Enable embeddings
                    </label>}
                    {(!isEmbedding || selection) && <>
                        <div className="space-y-1.5">
                            <label htmlFor={`connection-${role}`} className="text-sm font-medium">Provider connection</label>
                            {account}
                            <p className="text-xs text-muted-foreground">Add or manage connections in <Link href="/provider-connections" className="underline">Providers</Link>.</p>
                        </div>
                        {settings}
                    </>}
                    {isEmbedding && <p className="text-xs text-muted-foreground">Use the embedding model that indexed this workflow’s knowledge base. Changing it does not reindex documents.</p>}
                </section>;
            })}
        </>}
    </div>;
}
