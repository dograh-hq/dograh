"use client";

import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";

import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

import { compatibleConnections, configurationForMode, configurationMode, dograhConfiguration, selectionForConnection } from "./configuration";
import { AddProviderDialog, type ProviderOption, ProviderSelect } from "./ProviderSelect";
import { SchemaFields } from "./SchemaFields";
import type { ConfigurationEditorMode, ConfigurationSpec, FieldSchema, ModelConnectionCatalog, ProviderConnection, ServiceRole, ServiceSelection } from "./types";
import { MODE_LABELS, ROLE_LABELS } from "./types";

export type ConfigurationFieldsDensity = "full" | "compact";

// Dograh manages its services; only these settings are the user's to change,
// listed in the order they are shown.
const DOGRAH_FIELDS: [ServiceRole, string[]][] = [["tts", ["voice", "speed"]], ["stt", ["language"]], ["llm", ["temperature"]]];
const MODES: { value: ConfigurationEditorMode; description: string }[] = [
    { value: "realtime", description: "One speech-to-speech model listens and replies." },
    { value: "dograh", description: "Dograh runs the transcriber, LLM and voice for you." },
    { value: "cascade", description: "Pick your own LLM, STT and TTS providers." },
];
const SERVICE_TAB = "-mb-px h-auto min-w-0 flex-1 flex-col items-start gap-0.5 rounded-none border-0 border-b-2 border-transparent px-3 py-2.5 text-left sm:px-4 text-muted-foreground data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:shadow-none dark:data-[state=active]:border-primary dark:data-[state=active]:bg-transparent";

// Read aloud, "LLM", "STT" and "Embedding" take "an".
const ROLE_ARTICLES: Record<ServiceRole, string> = { llm: "an", stt: "an", tts: "a", realtime: "a", embeddings: "an" };

type AddTarget = ServiceRole | "dograh";

function restrictSchema(schema: FieldSchema | undefined, allowed?: string[]): FieldSchema | undefined {
    if (!schema || !allowed) return schema;
    return { ...schema, properties: Object.fromEntries(Object.entries(schema.properties || {}).filter(([name]) => allowed.includes(name))) };
}

function hasFields(schema: FieldSchema | undefined): schema is FieldSchema {
    return Boolean(schema && Object.keys(schema.properties || {}).length);
}

function listNames(names: string[]): string | undefined {
    if (!names.length) return undefined;
    return names.length > 3 ? `${names.slice(0, 3).join(", ")} and ${names.length - 3} more` : names.join(", ");
}

function ModeChoice({ value, onChange }: { value: ConfigurationEditorMode; onChange: (value: string) => void }) {
    const id = useId();
    return <RadioGroupPrimitive.Root aria-label="Mode" value={value} onValueChange={onChange} className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {MODES.map(option => <RadioGroupPrimitive.Item key={option.value} value={option.value} aria-labelledby={`${id}-${option.value}`} aria-describedby={`${id}-${option.value}-description`}
            className="group flex items-start gap-3 rounded-lg border bg-card p-3 text-left outline-none transition-[color,border-color,box-shadow] hover:border-foreground/25 focus-visible:ring-[3px] focus-visible:ring-ring/50 data-[state=checked]:border-primary data-[state=checked]:ring-1 data-[state=checked]:ring-primary">
            <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border border-input group-data-[state=checked]:border-primary">
                <RadioGroupPrimitive.Indicator className="size-2 rounded-full bg-primary" />
            </span>
            <span className="min-w-0 space-y-0.5">
                <span id={`${id}-${option.value}`} className="block text-sm font-medium">{MODE_LABELS[option.value]}</span>
                <span id={`${id}-${option.value}-description`} className="block text-xs leading-snug text-muted-foreground">{option.description}</span>
            </span>
        </RadioGroupPrimitive.Item>)}
    </RadioGroupPrimitive.Root>;
}

/**
 * The one editor for a model configuration spec.
 *
 * The mode comes first: Realtime, Dograh or BYOK. Dograh shows the few
 * settings Dograh leaves to the user. Realtime and BYOK show one tab per
 * service, each naming its provider so the whole setup reads at a glance,
 * with the provider and its settings inside. Every provider list ends with
 * a way to add a provider in place.
 *
 * "full" is the Models page: every setting, embeddings and fallbacks.
 * "compact" is the agent editor's Model view: the settings named in
 * `fields` (all of them when omitted), and no embeddings or fallback
 * actions. Both produce the same ConfigurationSpec.
 */
export function ConfigurationFields({ configuration, catalog, connections, onChange, onConnectionAdded, llmActions, density = "full", fields }: {
    configuration: ConfigurationSpec;
    catalog: ModelConnectionCatalog;
    connections: ProviderConnection[];
    onChange: (configuration: ConfigurationSpec) => void;
    /** Add a connection created from a provider list to `connections`. */
    onConnectionAdded?: (connection: ProviderConnection) => void;
    llmActions?: ReactNode;
    density?: ConfigurationFieldsDensity;
    /** Setting names to show for each service; everything else stays as it is. */
    fields?: string[];
}) {
    const compact = density === "compact";
    const [unconfiguredMode, setUnconfiguredMode] = useState<ConfigurationEditorMode>("cascade");
    // The tab the user opened; until then, the mode's first service.
    const [activeRole, setActiveRole] = useState<ServiceRole | null>(null);
    const [adding, setAdding] = useState<AddTarget | null>(null);
    const root = useRef<HTMLDivElement>(null);
    const id = useId();
    const mode = configuration.mode === "realtime" || configuration.llm.provider_connection_uuid
        ? configurationMode(configuration, connections) : unconfiguredMode;
    const dograhConnections = connections.filter(connection => connection.is_active && connection.provider === "dograh");
    const roles = (mode === "realtime" ? ["realtime", "llm", "embeddings"] as ServiceRole[] : ["llm", "stt", "tts", "embeddings"] as ServiceRole[])
        .filter(role => !compact || role !== "embeddings");
    const currentRole = activeRole && roles.includes(activeRole) ? activeRole : roles[0];

    // Every service tab stays mounted so the form still validates the hidden
    // ones. When a hidden service is missing its provider, open its tab.
    useEffect(() => {
        const node = root.current;
        if (!node) return;
        let handled = false;
        const reveal = (event: Event) => {
            if (handled) return;
            handled = true;
            queueMicrotask(() => { handled = false; });
            const panel = (event.target as Element).closest<HTMLElement>("[data-service-role]");
            if (!panel?.hidden) return;
            setActiveRole(panel.dataset.serviceRole as ServiceRole);
            // The browser could not point at a control it could not see; ask again once it shows.
            const form = (event.target as HTMLInputElement).form;
            requestAnimationFrame(() => form?.reportValidity());
        };
        node.addEventListener("invalid", reveal, true);
        return () => node.removeEventListener("invalid", reveal, true);
    }, []);

    const providerTitle = (role: ServiceRole, provider: string) => catalog.services[role]?.[provider]?.title || provider;
    const providerOption = (role: ServiceRole, connection: ProviderConnection): ProviderOption => {
        const label = providerTitle(role, connection.provider);
        const name = connection.name.trim();
        return { value: connection.uuid, label, detail: name.toLowerCase() === label.toLowerCase() ? undefined : name };
    };
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
        setActiveRole(null);
        onChange(configurationForMode(nextMode, configuration, catalog, connections));
    };
    const changeDograhConnection = (value: string) => {
        const connection = dograhConnections.find(item => item.uuid === value);
        if (connection) onChange(dograhConfiguration(catalog, connection, configuration, connections));
    };
    const connected = new Set(connections.filter(item => item.is_active).map(item => item.provider));
    // Providers a service could add, in catalog order (the most used first),
    // the ones without a connection ahead of the rest.
    const addProviderKeys = (target: AddTarget) => target === "dograh" ? ["dograh"]
        : Object.keys(catalog.services[target] || {}).filter(provider => provider !== "dograh")
            .sort((a, b) => Number(connected.has(a)) - Number(connected.has(b)));
    // Named under the add entry, so the list shows what else is available.
    const addHint = (role: ServiceRole) => listNames(addProviderKeys(role).filter(provider => !connected.has(provider)).map(provider => providerTitle(role, provider)));
    const connectionAdded = (connection: ProviderConnection) => {
        const target = adding;
        // Added to the list in the same update that chooses it, so the mode
        // is never read without it.
        onConnectionAdded?.(connection);
        setAdding(null);
        toast.success("Provider connection saved");
        if (target === "dograh") onChange(dograhConfiguration(catalog, connection, configuration, connections));
        else if (target) changeSelection(target, selectionForConnection(catalog, target, connection, configuration[target], connections));
    };
    // The connections a service's picker offers, which its tab summary also reads.
    const serviceOptions = (role: ServiceRole) => {
        const options = compatibleConnections(catalog, connections, role);
        // Existing mixed configurations may include a managed service.
        // Keep that selection visible without replacing it during edits.
        const managed = connections.find(item => item.is_active && item.uuid === configuration[role]?.provider_connection_uuid && item.provider === "dograh");
        if (managed) options.push(managed);
        return options;
    };

    const dograhValue = dograhConnections.some(item => item.uuid === configuration.llm.provider_connection_uuid) ? configuration.llm.provider_connection_uuid : "";
    const dograhPanel = <div className={cn("grid grid-cols-1 gap-4 rounded-lg border bg-card sm:grid-cols-2", compact ? "p-4" : "p-5")}>
        {DOGRAH_FIELDS.map(([role, names]) => {
            const schema = restrictSchema(catalog.services[role]?.dograh?.settings_schema, names.filter(name => !fields || fields.includes(name)));
            if (!hasFields(schema)) return null;
            return <SchemaFields key={role} className="contents" provider="dograh" role={role} schema={schema}
                values={configuration[role]?.settings || {}} onChange={(name, value) => changeSetting(role, name, value)} />;
        })}
        <div className="space-y-1.5">
            <Label htmlFor="dograh-provider-connection">Dograh account</Label>
            <ProviderSelect id="dograh-provider-connection" required value={dograhValue} onValueChange={changeDograhConnection}
                placeholder="Choose an account" addLabel="Add a Dograh account" onAdd={() => setAdding("dograh")}
                options={dograhConnections.map(connection => ({ value: connection.uuid, label: connection.name }))} />
        </div>
    </div>;

    const servicePanel = (role: ServiceRole) => {
        const selection = configuration[role];
        const options = serviceOptions(role);
        const connection = options.find(item => item.uuid === selection?.provider_connection_uuid);
        const schema = restrictSchema(connection ? catalog.services[role]?.[connection.provider]?.settings_schema : undefined, fields);
        const isEmbedding = role === "embeddings";
        return <div className="space-y-4">
            {role === "llm" && mode === "realtime" && <p className="text-xs text-muted-foreground">The realtime model holds the conversation. This text LLM handles variable extraction and other background tasks.</p>}
            {isEmbedding && <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={Boolean(selection)} onChange={event => changeSelection(role,
                    event.target.checked ? (options[0] ? selectionForConnection(catalog, role, options[0]) : { provider_connection_uuid: "", settings: {} }) : null)} />
                Enable embeddings
            </label>}
            {(!isEmbedding || selection) && <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                    <Label htmlFor={`connection-${role}`}>Provider</Label>
                    <ProviderSelect id={`connection-${role}`} required value={connection?.uuid || ""} placeholder="Choose a provider"
                        options={options.map(item => providerOption(role, item))} addLabel="Add a provider" addHint={addHint(role)} onAdd={() => setAdding(role)}
                        onValueChange={value => {
                            const next = connections.find(item => item.uuid === value);
                            if (next) changeSelection(role, selectionForConnection(catalog, role, next, selection, connections));
                        }} />
                </div>
                {schema && selection && <SchemaFields key={connection?.provider} className="contents" provider={connection?.provider} role={role} schema={schema}
                    values={selection.settings} context={connection?.connection_settings} onChange={(name, value) => changeSetting(role, name, value)} />}
            </div>}
            {isEmbedding && <p className="text-xs text-muted-foreground">Use the embedding model that indexed this workflow’s knowledge base. Changing it does not reindex documents.</p>}
            {role === "llm" && llmActions && connection?.provider !== "dograh" && <div className="flex justify-end border-t pt-4">{llmActions}</div>}
        </div>;
    };

    // Each tab names the provider its picker shows, or that it needs one.
    const tabSummary = (role: ServiceRole): { text: string; missing: boolean } => {
        const selection = configuration[role];
        if (role === "embeddings" && !selection) return { text: "Off", missing: false };
        const connection = serviceOptions(role).find(item => item.uuid === selection?.provider_connection_uuid);
        return connection ? { text: providerTitle(role, connection.provider), missing: false } : { text: "Choose a provider", missing: true };
    };

    return <div ref={root} className={compact ? "space-y-3" : "space-y-4"}>
        <ModeChoice value={mode} onChange={changeMode} />
        {mode === "dograh" ? dograhPanel : <Tabs value={currentRole} onValueChange={value => setActiveRole(value as ServiceRole)} className="gap-0 overflow-hidden rounded-lg border bg-card">
            <TabsList aria-label="Services" className="flex h-auto w-full justify-start rounded-none border-b bg-transparent p-0">
                {roles.map(role => {
                    const summary = tabSummary(role);
                    return <TabsTrigger key={role} value={role} className={SERVICE_TAB} aria-labelledby={`${id}-${role}`} aria-describedby={`${id}-${role}-summary`}>
                        <span id={`${id}-${role}`} className="text-sm font-medium">{ROLE_LABELS[role]}</span>{" "}
                        <span id={`${id}-${role}-summary`} className={cn("max-w-full truncate text-xs font-normal", summary.missing ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground")}>{summary.text}</span>
                    </TabsTrigger>;
                })}
            </TabsList>
            {roles.map(role => <TabsContent key={role} value={role} forceMount hidden={role !== currentRole} data-service-role={role} className={compact ? "p-4" : "p-5"}>
                {servicePanel(role)}
            </TabsContent>)}
        </Tabs>}
        {adding && <AddProviderDialog open onOpenChange={open => { if (!open) setAdding(null); }} catalog={catalog}
            title={adding === "dograh" ? "Add a Dograh account" : `Add ${ROLE_ARTICLES[adding]} ${ROLE_LABELS[adding]} provider`}
            providerKeys={addProviderKeys(adding)} role={adding === "dograh" ? undefined : adding} onSaved={connectionAdded} />}
    </div>;
}
