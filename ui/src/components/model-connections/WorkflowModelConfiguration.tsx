"use client";

import { Brain } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { previewModelConfiguration } from "@/client/sdk.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useUnsavedChanges } from "@/context/UnsavedChangesContext";
import { detailFromError } from "@/lib/apiError";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

import { configurationMode } from "./configuration";
import { ConfigurationSelect } from "./ConfigurationSelect";
import { MODE_LABELS, ROLE_LABELS } from "./types";
import { useModelConnections } from "./useModelConnections";

export function WorkflowModelConfiguration({ workflowConfigurations, workflowName, onSave }: {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
}) {
    const { catalog, connections, configurations, defaultUuid, loading, error: loadError, reload } = useModelConnections();
    const saved = workflowConfigurations.model_configuration_override;
    // Keep existing inline/legacy overrides intact until a replacement is
    // explicitly selected. Opening settings must not turn them into inheritance.
    const hasCustomSettings = saved != null
        ? Object.keys(saved).some(key => key !== "model_configuration_uuid")
        : Boolean(workflowConfigurations.model_configuration_v2_override || workflowConfigurations.model_overrides);
    const savedSelection = hasCustomSettings ? "existing" : saved?.model_configuration_uuid || "inherit";
    const [selection, setSelection] = useState(savedSelection);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        setSelection(savedSelection);
        setError(null);
    }, [savedSelection]);
    useUnsavedChanges("models", selection !== savedSelection);

    const activeConfigurations = configurations.filter(item => item.is_active);
    const selected = selection === "existing" ? undefined : activeConfigurations.find(item => item.uuid === (selection === "inherit" ? defaultUuid : selection));
    const configuration = selected?.configuration;
    const mode = configuration ? configurationMode(configuration, connections) : null;

    return <Card id="models">
        <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Brain className="h-4 w-4" />Model Configuration</CardTitle>
            <CardDescription>Use your organization default or select a named model configuration. Publish the agent after saving to apply changes.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
            {loading && !catalog && <p className="text-sm text-muted-foreground">Loading model configurations…</p>}
            {(error || loadError) && <div role="alert" className="space-y-2 rounded-md border border-destructive/40 p-3 text-sm text-destructive">{error || loadError}
                {loadError && <Button type="button" size="sm" variant="outline" onClick={() => void reload()}>Retry</Button>}
            </div>}
            {catalog && <form className="space-y-4" onSubmit={async event => {
                event.preventDefault();
                if (!selected || saving || loading || loadError) return;
                setSaving(true); setError(null);
                try {
                    const override = selection === "inherit" ? {} : { model_configuration_uuid: selected.uuid };
                    const preview = await previewModelConfiguration({ body: override });
                    if (preview.error) throw new Error(detailFromError(preview.error, "Model configuration is invalid"));
                    const next = { ...workflowConfigurations };
                    delete next.model_overrides;
                    delete next.model_configuration_v2_override;
                    // Empty explicitly selects catalog inheritance. The backend
                    // retains the retired payloads for audit without using them.
                    next.model_configuration_override = override;
                    await onSave(next, workflowName);
                    toast.success("Model configuration saved. Publish the agent to apply the changes.");
                } catch (cause) { setError(cause instanceof Error ? cause.message : "Failed to save model configuration"); }
                finally { setSaving(false); }
            }}>
                {hasCustomSettings && <p className="rounded-md border bg-muted/30 p-3 text-sm">This workflow has custom model settings. They remain in use until you select and save a named configuration or the organization default below.</p>}
                <div className="space-y-1.5">
                    <label htmlFor="workflow-model-configuration" className="text-sm font-medium">Model configuration</label>
                    <ConfigurationSelect id="workflow-model-configuration" value={selection} disabled={saving || loading} onValueChange={value => {
                        setSelection(value); setError(null);
                    }} options={[
                        { value: "inherit", label: "Follow organization default" },
                        ...(hasCustomSettings ? [{ value: "existing", label: "Existing workflow override" }] : []),
                        ...(selection !== "inherit" && selection !== "existing" && !selected ? [{ value: selection, label: "Unavailable configuration" }] : []),
                        ...activeConfigurations.map(item => ({ value: item.uuid, label: item.name })),
                    ]} />
                    {selection !== "existing" && <p className="text-xs text-muted-foreground">{selection === "inherit"
                        ? "Follows whichever configuration your organization sets as its default."
                        : "Keeps this configuration selected even when the organization default changes."}</p>}
                    <p className="text-xs text-muted-foreground">Create and edit configurations in <Link href="/model-configurations" className="underline">Models</Link>. You can override individual fields through the API trigger.</p>
                </div>
                {!selected && selection !== "existing" && <p className="rounded-md border p-3 text-sm text-muted-foreground">{selection !== "inherit" ? "The selected configuration is unavailable. Choose another configuration before saving." : "Set an organization default in Models or select a named model configuration."}</p>}
                {selected && configuration && <div className="space-y-1 rounded-md border bg-muted/20 p-3">
                    <p className="text-sm font-medium">{selected.name} · {mode && MODE_LABELS[mode]}</p>
                    {mode === "dograh" ? <p className="text-xs text-muted-foreground">{connections.find(item => item.uuid === configuration.llm.provider_connection_uuid)?.name || "Unavailable connection"}</p>
                        : (configuration.mode === "pipeline" ? ["llm", "stt", "tts"] as const : ["realtime", "llm"] as const).map(role => {
                            const service = configuration[role];
                            const connection = connections.find(item => item.uuid === service?.provider_connection_uuid);
                            return <p key={role} className="text-xs text-muted-foreground">{ROLE_LABELS[role]}: {connection?.name || "Unavailable connection"}{service?.settings.model ? ` / ${service.settings.model}` : ""}</p>;
                        })}
                </div>}
                <Button type="submit" disabled={saving || loading || Boolean(loadError) || !selected}>{saving ? "Saving…" : "Save Model Configuration"}</Button>
            </form>}
        </CardContent>
    </Card>;
}
