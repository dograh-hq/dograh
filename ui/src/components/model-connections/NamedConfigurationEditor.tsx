"use client";

import { useState } from "react";

import { createNamedModelConfiguration, updateNamedModelConfiguration } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromError } from "@/lib/apiError";

import { cleanConfiguration, emptyConfiguration } from "./configuration";
import { ConfigurationFields } from "./ConfigurationFields";
import type { ModelConnectionCatalog, NamedModelConfiguration, ProviderConnection } from "./types";

export function NamedConfigurationEditor({ catalog, connections, saved, duplicate = false, onSaved }: {
    catalog: ModelConnectionCatalog;
    connections: ProviderConnection[];
    saved?: NamedModelConfiguration;
    duplicate?: boolean;
    onSaved: (uuid: string, editFallbacks?: boolean) => Promise<void>;
}) {
    const [name, setName] = useState(saved ? `${saved.name}${duplicate ? " (copy)" : ""}` : "");
    const [configuration, setConfiguration] = useState(saved?.configuration || emptyConfiguration(catalog, connections));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const save = async (editFallbacks = false) => {
        setSaving(true);
        setError(null);
        try {
            const body = { name: name.trim(), configuration: cleanConfiguration(configuration, catalog, connections) };
            const result = saved && !duplicate
                ? await updateNamedModelConfiguration({ path: { configuration_uuid: saved.uuid }, body: { ...body, revision: saved.revision } })
                : await createNamedModelConfiguration({ body });
            if (result.error) throw new Error(detailFromError(result.error, "Failed to save model configuration"));
            if (!result.data) throw new Error("Failed to save model configuration");
            await onSaved(result.data.uuid, editFallbacks);
        } catch (cause) { setError(cause instanceof Error ? cause.message : "Failed to save model configuration"); }
        finally { setSaving(false); }
    };
    return <form className="space-y-5" onSubmit={event => {
        event.preventDefault();
        void save();
    }}>
        <div className="space-y-2">
            <h1 className="text-2xl font-bold">{saved && !duplicate ? "Edit Model Configuration" : "Add Model Configuration"}</h1>
            <p className="text-sm text-muted-foreground">{saved && !duplicate
                ? "Edits apply to future runs of every workflow using this configuration. Existing runs keep their settings."
                : "Save a reusable model setup for workflows and API triggers."}</p>
        </div>
        {error && <p role="alert" className="rounded-md border border-destructive/40 p-3 text-sm text-destructive">{error}</p>}
        <div className="space-y-1.5"><Label htmlFor="configuration-name">Configuration name</Label>
            <Input id="configuration-name" required maxLength={128} placeholder="Sales assistant" value={name} onChange={event => setName(event.target.value)} /></div>
        <ConfigurationFields catalog={catalog} connections={connections} configuration={configuration} onChange={setConfiguration}
            llmActions={<Button type="button" name="intent" value="fallbacks" variant="outline" size="sm" disabled={saving || !name.trim()} onClick={event => {
                if (event.currentTarget.form?.reportValidity()) void save(true);
            }}>Configure fallbacks</Button>} />
        <div className="flex justify-end"><Button type="submit" disabled={saving || !name.trim()}>{saving ? "Saving…" : "Save Configuration"}</Button></div>
    </form>;
}
