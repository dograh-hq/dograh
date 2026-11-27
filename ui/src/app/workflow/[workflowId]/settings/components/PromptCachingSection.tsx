"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useUnsavedChanges } from "@/context/UnsavedChangesContext";
import { useAuth } from "@/lib/auth";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

export function PromptCachingSection({ workflowConfigurations, onSave }: {
    workflowConfigurations: WorkflowConfigurations;
    onSave: (value: WorkflowConfigurations) => Promise<void>;
}) {
    const { user, loading: authLoading } = useAuth();
    const [enabled, setEnabled] = useState(workflowConfigurations.prompt_variables?.mode === "call_context");
    const [error, setError] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const dirty = enabled !== (workflowConfigurations.prompt_variables?.mode === "call_context");
    useUnsavedChanges("prompt-caching", dirty);

    const save = async () => {
        setSaving(true);
        setError(null);
        try {
            const configurations: WorkflowConfigurations = {
                ...workflowConfigurations,
                prompt_variables: {
                    ...workflowConfigurations.prompt_variables,
                    mode: enabled ? "call_context" : "inline",
                },
            };
            delete configurations.prompt_caching;
            await onSave(configurations);
            toast.success("Prompt settings saved. Publish the agent to apply them to new calls.");
        } catch { setError("Could not save prompt settings"); }
        finally { setSaving(false); }
    };

    return <Card id="prompt-caching">
        <CardHeader>
            <CardTitle className="text-base">Prompt caching</CardTitle>
            <CardDescription>Keep shared instructions stable across calls. Dograh manages caching automatically for your provider and model.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
            <div className="flex items-center justify-between gap-4">
                <Label htmlFor="optimize-prompt-caching">Optimize prompt caching</Label>
                <Switch id="optimize-prompt-caching" checked={enabled} onCheckedChange={setEnabled} disabled={authLoading || !user || saving} />
            </div>
            {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        </CardContent>
        <CardFooter><Button onClick={save} disabled={!dirty || saving || authLoading || !user}>{saving ? "Saving…" : "Save Prompt Settings"}</Button></CardFooter>
    </Card>;
}
