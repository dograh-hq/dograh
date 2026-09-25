"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { listModelProviderProfilesApiV1OrganizationsModelConfigurationsV2ProfilesGet } from "@/client/sdk.gen";
import type { ModelProviderProfileResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type ProfileService = ModelProviderProfileResponse["service"];

/** Saved providers chosen for a workflow: `{ llm: { profile: "openai-fast" } }`. */
export type WorkflowProfileSelection = Partial<Record<ProfileService, { profile: string } & Record<string, unknown>>>;

const SERVICES: { key: ProfileService; label: string }[] = [
    { key: "llm", label: "LLM" },
    { key: "tts", label: "Voice" },
    { key: "stt", label: "Transcriber" },
    { key: "realtime", label: "Speech to Speech" },
];

const USE_DEFAULT = "__default__";

interface WorkflowSavedProvidersProps {
    selection: WorkflowProfileSelection | undefined;
    onSave: (selection: WorkflowProfileSelection) => Promise<void>;
}

/**
 * Pick saved providers (Models page > Saved providers) for one workflow. Only
 * names are stored; keys are read from the saved provider when a run starts.
 */
export function WorkflowSavedProviders({ selection, onSave }: WorkflowSavedProvidersProps) {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [profiles, setProfiles] = useState<ModelProviderProfileResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [draft, setDraft] = useState<Record<string, string>>({});
    const [isSaving, setIsSaving] = useState(false);

    // Reset the dropdowns whenever the saved selection changes.
    useEffect(() => {
        const next: Record<string, string> = {};
        for (const { key } of SERVICES) {
            next[key] = selection?.[key]?.profile ?? USE_DEFAULT;
        }
        setDraft(next);
    }, [selection]);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        const load = async () => {
            const result = await listModelProviderProfilesApiV1OrganizationsModelConfigurationsV2ProfilesGet();
            if (result.error || !result.data) {
                setError(detailFromError(result.error, "Failed to load saved providers"));
            } else {
                setProfiles(result.data.profiles);
            }
            setLoading(false);
        };
        void load();
    }, [authLoading, user]);

    const dirty = useMemo(
        () => SERVICES.some(({ key }) => draft[key] !== (selection?.[key]?.profile ?? USE_DEFAULT)),
        [draft, selection],
    );

    const save = async () => {
        const next: WorkflowProfileSelection = {};
        for (const { key } of SERVICES) {
            const chosen = draft[key];
            if (chosen && chosen !== USE_DEFAULT) {
                // Keep field tweaks made through the API for an unchanged profile.
                const existing = selection?.[key];
                next[key] = existing?.profile === chosen ? existing : { profile: chosen };
            }
        }
        setIsSaving(true);
        setError(null);
        try {
            await onSave(next);
        } catch (saveError) {
            setError(saveError instanceof Error ? saveError.message : "Failed to save saved providers");
        } finally {
            setIsSaving(false);
        }
    };

    if (loading) return null;

    return (
        <div className="space-y-4 rounded-md border p-4">
            <div className="space-y-1">
                <h3 className="text-sm font-medium">Saved providers</h3>
                <p className="text-xs text-muted-foreground">
                    Choose a saved provider for individual services. Anything left on &quot;Use default&quot; keeps the
                    organization (or override) configuration. Manage saved providers on the AI Models Configuration page.
                </p>
            </div>

            {profiles.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                    No saved providers yet. Add some under Model Configurations, then choose them here.
                </p>
            ) : (
                <div className="grid gap-4 sm:grid-cols-2">
                    {SERVICES.map(({ key, label }) => {
                        const options = profiles.filter((profile) => profile.service === key);
                        const current = draft[key] ?? USE_DEFAULT;
                        const missing =
                            current !== USE_DEFAULT && !options.some((profile) => profile.name === current);
                        return (
                            <div key={key} className="space-y-2">
                                <Label htmlFor={`saved-provider-${key}`}>{label}</Label>
                                <Select
                                    value={current}
                                    onValueChange={(value) => setDraft((previous) => ({ ...previous, [key]: value }))}
                                >
                                    <SelectTrigger id={`saved-provider-${key}`} className="w-full">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={USE_DEFAULT}>Use default</SelectItem>
                                        {missing && (
                                            <SelectItem value={current}>{current} (deleted)</SelectItem>
                                        )}
                                        {options.map((profile) => (
                                            <SelectItem key={profile.name} value={profile.name}>
                                                {profile.name}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                {missing && (
                                    <p className="text-xs text-destructive">
                                        This saved provider no longer exists. Calls will fail until you choose another.
                                    </p>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {error && <p className="text-sm text-destructive">{error}</p>}

            {profiles.length > 0 || selection ? (
                <Button type="button" onClick={save} disabled={!dirty || isSaving}>
                    {isSaving ? "Saving..." : "Save Saved Providers"}
                </Button>
            ) : null}
        </div>
    );
}
