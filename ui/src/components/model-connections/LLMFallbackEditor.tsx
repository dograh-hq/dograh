"use client";

import Link from "next/link";
import { useState } from "react";

import { updateNamedModelConfiguration } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";

import { cleanFallbackPolicy, compatibleConnections, selectionForConnection } from "./configuration";
import { ConfigurationSelect } from "./ConfigurationSelect";
import { SchemaFields } from "./SchemaFields";
import type { FallbackCondition, FallbackRule, ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";

const CONDITIONS: { type: FallbackCondition["type"]; title: string; description: string }[] = [
    { type: "no_output", title: "Slow response", description: "Start another LLM if the primary has not produced answer text or a tool call within your time limit." },
    { type: "error", title: "Request error", description: "Try another LLM on any error before output, including rate limits, billing errors, connection failures, and empty responses." },
];

export function LLMFallbackEditor({ saved, catalog, connections, onSaved }: {
    saved: NamedModelConfiguration;
    catalog: ModelConnectionCatalog;
    connections: ProviderConnection[];
    onSaved: () => Promise<void>;
}) {
    const [rules, setRules] = useState<FallbackRule[]>(saved.configuration.llm_fallback?.rules || []);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const available = compatibleConnections(catalog, connections, "llm");
    const primary = connections.find(connection => connection.uuid === saved.configuration.llm.provider_connection_uuid);
    const primaryModel = saved.configuration.llm.settings.model || (primary && catalog.services.llm?.[primary.provider]?.settings_schema.properties?.model?.default);
    const update = (type: FallbackCondition["type"], rule: FallbackRule) => setRules(current => current.map(item => item.condition.type === type ? rule : item));

    return <form className="space-y-6" onSubmit={async event => {
        event.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const configuration = { ...saved.configuration, llm_fallback: cleanFallbackPolicy({ version: 1, rules }, catalog, connections) };
            const result = await updateNamedModelConfiguration({ path: { configuration_uuid: saved.uuid }, body: { configuration, revision: saved.revision } });
            if (result.error) throw new Error(detailFromError(result.error, "Failed to save LLM fallbacks"));
            if (!result.data) throw new Error("Failed to save LLM fallbacks");
            await onSaved();
        } catch (cause) { setError(cause instanceof Error ? cause.message : "Failed to save LLM fallbacks"); }
        finally { setSaving(false); }
    }}>
        <div className="space-y-2">
            <p className="text-sm text-muted-foreground">{saved.name}</p>
            <h1 className="text-2xl font-bold">LLM fallbacks</h1>
            <p className="max-w-2xl text-sm text-muted-foreground">Keep conversations moving when an LLM is slow or unavailable. Each response starts with your primary LLM; the first answer from an eligible request serves that response.</p>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-muted/30 p-4">
            <div><p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Primary LLM</p><p className="mt-1 font-medium">{primary?.name || "Unavailable connection"} <span className="font-normal text-muted-foreground">· {String(primaryModel || "Provider default")}</span></p></div>
            <Button asChild variant="ghost" size="sm"><Link href={`/model-configurations/${saved.uuid}`}>Edit primary</Link></Button>
        </div>
        {saved.configuration.mode === "realtime" && <p className="text-sm text-muted-foreground">These rules apply to the separate text LLM. They do not switch the realtime audio model.</p>}
        {error && <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm text-destructive">{error}</p>}
        <fieldset disabled={saving} className="space-y-4">
            {CONDITIONS.map(({ type, title, description }) => {
                const rule = rules.find(item => item.condition.type === type);
                const connection = available.find(item => item.uuid === rule?.target.provider_connection_uuid);
                const entry = connection && catalog.services.llm?.[connection.provider];
                return <section key={type} className="rounded-lg border bg-card">
                    <div className="flex items-start justify-between gap-4 p-5">
                        <div className="space-y-1"><Label htmlFor={`fallback-${type}`} className="text-base font-semibold">{title}</Label><p className="max-w-2xl text-sm text-muted-foreground">{description}</p></div>
                        <Switch id={`fallback-${type}`} checked={Boolean(rule)} onCheckedChange={enabled => {
                            if (!enabled) { setRules(current => current.filter(item => item.condition.type !== type)); return; }
                            const target = available.find(item => item.uuid !== saved.configuration.llm.provider_connection_uuid) || available[0];
                            setRules(current => [...current, {
                                condition: type === "no_output" ? { type, after_ms: 1500 } : { type },
                                target: target ? selectionForConnection(catalog, "llm", target) : { provider_connection_uuid: "", settings: {} },
                            }]);
                        }} />
                    </div>
                    {rule && <div className="space-y-5 border-t p-5">
                        {rule.condition.type === "no_output" && <div className="space-y-1.5">
                            <Label htmlFor="fallback-delay">Wait before starting fallback (ms)</Label>
                            <Input id="fallback-delay" type="number" required min={100} max={10000} step={1} className="max-w-52" value={rule.condition.after_ms || ""}
                                onChange={event => update(type, { ...rule, condition: { type: "no_output", after_ms: Number(event.target.value) } })} />
                            <p className="text-xs text-muted-foreground">The primary keeps running until one request produces output. Allowed: 100–10,000 ms.</p>
                        </div>}
                        <div className="space-y-1.5">
                            <Label htmlFor={`fallback-connection-${type}`}>Provider connection</Label>
                            <ConfigurationSelect id={`fallback-connection-${type}`} required value={connection?.uuid || ""} placeholder="Select an LLM connection"
                                options={available.map(item => ({ value: item.uuid, label: `${item.name} · ${catalog.services.llm?.[item.provider]?.title || item.provider}` }))}
                                onValueChange={uuid => {
                                    const next = available.find(item => item.uuid === uuid);
                                    if (next) update(type, { ...rule, target: selectionForConnection(catalog, "llm", next, rule.target, connections) });
                                }} />
                            <p className="text-xs text-muted-foreground">Use a different connection or model from the primary. Add or manage connections in <Link href="/provider-connections" className="underline">Providers</Link>.</p>
                        </div>
                        {entry && <SchemaFields key={connection.provider} provider={connection.provider} role="llm" schema={entry.settings_schema} values={rule.target.settings} context={connection.connection_settings}
                            onChange={(name, value) => {
                                const settings = { ...rule.target.settings, [name]: value };
                                if (value === undefined) delete settings[name];
                                update(type, { ...rule, target: { ...rule.target, settings } });
                            }} />}
                    </div>}
                </section>;
            })}
        </fieldset>
        <div className="flex flex-wrap items-start justify-between gap-4 border-t pt-5">
            <p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">Fallbacks apply only before answer output or tool execution begins. Backup requests can add cost and send conversation data to their configured provider and region. Changes apply to future runs.</p>
            <Button type="submit" disabled={saving || rules.some(rule => !available.some(connection => connection.uuid === rule.target.provider_connection_uuid))}>{saving ? "Saving…" : "Save fallbacks"}</Button>
        </div>
    </form>;
}
