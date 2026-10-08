"use client";

import { ExternalLink, Loader2 } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { ConfigurationFields, type ConfigurationFieldsDensity } from "./ConfigurationFields";
import { ConfigurationSelect } from "./ConfigurationSelect";
import type { WorkflowModelOverride } from "./useWorkflowModelOverride";

const INHERIT = "__inherit__";
const LEGACY = "__legacy__";
const UNAVAILABLE = "__unavailable__";

/** Settings the compact canvas picker shows for each service. */
export const QUICK_FIELDS = ["model", "voice"];

function status(model: WorkflowModelOverride): { text: string; tone: "muted" | "changed" | "warn" } {
    const { binding, view, incomplete } = model;
    if (view === "custom") {
        if (incomplete) return { text: "Choose an account for each service. The settings save once every service has one.", tone: "warn" };
        return { text: "Custom settings for this agent. Test calls use them now; publish to apply them to live calls. Switching to a configuration drops them.", tone: "changed" };
    }
    switch (binding.kind) {
        case "none": return { text: "No configuration chosen yet. Pick one below, or set an organization default in Models.", tone: "warn" };
        case "legacy": return { text: "This agent has custom model settings saved before named configurations existed. They stay in use until you choose a configuration or switch to Custom.", tone: "warn" };
        case "unavailable": return { text: "The selected configuration is unavailable. Choose another one below.", tone: "warn" };
        case "existing": return { text: `Following ${binding.base.name}${binding.isDefault ? ", the organization default" : ""}.`, tone: "muted" };
        default: return { text: "", tone: "muted" };
    }
}

const TONE_CLASS = {
    muted: "border-border bg-muted/30 text-muted-foreground",
    changed: "border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    warn: "border-border bg-muted/30 text-foreground",
};

/**
 * The workflow's model settings: follow an existing configuration, or build
 * custom settings from scratch with the configuration fields at the given
 * density. The canvas shows it compact with the quick fields; the settings
 * page shows it in full.
 */
export function WorkflowModelPicker({ model, density = "full", fields }: {
    model: WorkflowModelOverride;
    density?: ConfigurationFieldsDensity;
    fields?: string[];
}) {
    const { binding, view, configuration, shared, catalog, connections, defaultUuid, loading, loadError, reload, error, saving } = model;
    const compact = density === "compact";
    if (loading && !catalog) return <p className="text-sm text-muted-foreground">Loading model configurations…</p>;
    if (loadError || !catalog) return <div role="alert" className="space-y-2 rounded-md border border-destructive/40 p-3 text-sm text-destructive">
        {loadError || "Failed to load model configurations"}
        <div><Button type="button" size="sm" variant="outline" onClick={() => void reload()}>Retry</Button></div>
    </div>;
    const line = status(model);
    const defaultName = shared.find(item => item.uuid === defaultUuid)?.name;
    const selected = binding.kind === "existing" ? (binding.isDefault ? INHERIT : binding.base.uuid)
        : binding.kind === "legacy" ? LEGACY : binding.kind === "unavailable" ? UNAVAILABLE : INHERIT;
    // Leaving custom returns to the configuration the override was layered on, when it named one.
    const currentUuid = binding.kind === "existing" ? (binding.isDefault ? null : binding.base.uuid) : binding.kind === "custom" ? binding.baseUuid : null;
    return <div className={compact ? "space-y-3" : "space-y-4"}>
        <Tabs value={view} onValueChange={next => { if (next === "custom") model.startCustom(); else void model.useExisting(currentUuid); }}>
            <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="existing" disabled={saving}>Preset configuration</TabsTrigger>
                <TabsTrigger value="custom" disabled={saving}>Custom</TabsTrigger>
            </TabsList>
        </Tabs>
        {/* On the canvas the select already names the configuration; only warnings and custom state need a line. */}
        {line.text && !(compact && line.tone === "muted") && <p className={`rounded-md border px-3 py-2 text-xs leading-relaxed ${TONE_CLASS[line.tone]}`} role="status">{line.text}</p>}
        {view === "existing" && <div className={compact ? "space-y-1" : "space-y-1.5"}>
            <label htmlFor="workflow-model-base" className={compact ? "text-xs font-medium text-muted-foreground" : "text-sm font-medium"}>Configuration</label>
            <ConfigurationSelect id="workflow-model-base" value={selected} disabled={saving} placeholder="Choose a configuration" onValueChange={next => {
                if (next === LEGACY || next === UNAVAILABLE) return;
                void model.useExisting(next === INHERIT ? null : next);
            }} options={[
                { value: INHERIT, label: defaultName ? `Organization default (${defaultName})` : "Organization default" },
                ...shared.filter(item => item.uuid !== defaultUuid).map(item => ({ value: item.uuid, label: item.name })),
                ...(binding.kind === "legacy" ? [{ value: LEGACY, label: "Existing workflow override" }] : []),
                ...(binding.kind === "unavailable" ? [{ value: UNAVAILABLE, label: "Unavailable configuration" }] : []),
            ]} />
            {binding.kind === "existing" && <Link href={`/model-configurations/${binding.base.uuid}`} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs text-muted-foreground underline underline-offset-2">
                Open {binding.base.name} in Models<ExternalLink className="h-3 w-3" />
            </Link>}
        </div>}
        {view === "custom" && configuration && <ConfigurationFields configuration={configuration} catalog={catalog} connections={connections} onChange={model.edit} density={density} fields={fields} />}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        {(saving || !compact) && <p className="inline-flex items-center gap-2 text-xs text-muted-foreground">
            {saving ? <><Loader2 className="h-3 w-3 animate-spin" />Saving to draft…</>
                : <span>Manage configurations in <Link href="/model-configurations" className="inline-flex items-center gap-0.5 underline">Models<ExternalLink className="h-3 w-3" /></Link>.</span>}
        </p>}
    </div>;
}
