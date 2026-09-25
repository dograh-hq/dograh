"use client";

import { Pencil, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
    createModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesPost,
    deleteModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesServiceNameDelete,
    listModelProviderProfilesApiV1OrganizationsModelConfigurationsV2ProfilesGet,
    updateModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesServiceNamePut,
} from "@/client/sdk.gen";
import type { ModelProviderProfileResponse } from "@/client/types.gen";
import type { ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";
import {
    type ServiceConfigurationDefaults,
    ServiceConfigurationForm,
    type ServiceSegment,
} from "@/components/ServiceConfigurationForm";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type ProfileService = ModelProviderProfileResponse["service"];

const SERVICES: { key: ProfileService; label: string }[] = [
    { key: "llm", label: "LLM" },
    { key: "tts", label: "Voice" },
    { key: "stt", label: "Transcriber" },
    { key: "realtime", label: "Speech to Speech" },
];

const PROFILE_NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,47}$/;

type DialogState =
    | { kind: "create" }
    | { kind: "edit"; profile: ModelProviderProfileResponse }
    | { kind: "delete"; profile: ModelProviderProfileResponse }
    | null;

function summarize(profile: ModelProviderProfileResponse): string {
    const config = profile.config as Record<string, unknown>;
    return [config.provider, config.model, config.voice]
        .filter((part): part is string => typeof part === "string" && part.length > 0)
        .join(" / ");
}

interface SavedProvidersSectionProps {
    defaults: ModelConfigurationDefaultsV2;
}

/**
 * Named provider configs saved next to the default model configuration.
 * Several profiles may share a provider (for example two OpenAI accounts).
 * API callers reference a profile by name instead of sending credentials.
 */
export function SavedProvidersSection({ defaults }: SavedProvidersSectionProps) {
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    const [profiles, setProfiles] = useState<ModelProviderProfileResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [dialog, setDialog] = useState<DialogState>(null);

    const [name, setName] = useState("");
    const [service, setService] = useState<ProfileService>("llm");
    const [nameError, setNameError] = useState<string | null>(null);
    const [isDeleting, setIsDeleting] = useState(false);

    const formDefaults = useMemo<ServiceConfigurationDefaults>(
        () => ({
            llm: defaults.byok.pipeline.llm,
            tts: defaults.byok.pipeline.tts,
            stt: defaults.byok.pipeline.stt,
            embeddings: defaults.byok.pipeline.embeddings,
            realtime: defaults.byok.realtime.realtime,
            default_providers: defaults.byok.pipeline.default_providers,
        }),
        [defaults],
    );

    const load = useCallback(async () => {
        setLoading(true);
        const result = await listModelProviderProfilesApiV1OrganizationsModelConfigurationsV2ProfilesGet();
        if (result.error || !result.data) {
            setError(detailFromError(result.error, "Failed to load saved providers"));
        } else {
            setError(null);
            setProfiles(result.data.profiles);
        }
        setLoading(false);
    }, []);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
    }, [authLoading, user, load]);

    const editing = dialog?.kind === "edit" ? dialog.profile : null;
    const formService: ProfileService = editing ? editing.service : service;

    // Must stay referentially stable: the form re-initialises when it changes.
    const initialConfig = useMemo<Record<string, unknown>>(
        () => (editing ? { [editing.service]: editing.config } : {}),
        [editing],
    );

    const openCreate = () => {
        setName("");
        setService("llm");
        setNameError(null);
        setError(null);
        setDialog({ kind: "create" });
    };

    const saveProfile = async (config: Record<string, unknown>) => {
        const serviceConfig = config[formService];
        if (editing) {
            const result = await updateModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesServiceNamePut({
                path: { service: editing.service, name: editing.name },
                body: { config: serviceConfig as Record<string, unknown> },
            });
            if (result.error) {
                throw new Error(detailFromError(result.error, "Failed to update provider"));
            }
        } else {
            if (!PROFILE_NAME_PATTERN.test(name)) {
                const message = "Use 1-48 lowercase letters, digits, '-' or '_', starting with a letter or digit";
                setNameError(message);
                throw new Error(message);
            }
            setNameError(null);
            const result = await createModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesPost({
                body: { name, service: formService, config: serviceConfig as Record<string, unknown> },
            });
            if (result.error) {
                throw new Error(detailFromError(result.error, "Failed to save provider"));
            }
        }
        setDialog(null);
        await load();
    };

    const confirmDelete = async () => {
        if (dialog?.kind !== "delete") return;
        setIsDeleting(true);
        const result = await deleteModelProviderProfileApiV1OrganizationsModelConfigurationsV2ProfilesServiceNameDelete({
            path: { service: dialog.profile.service, name: dialog.profile.name },
        });
        setIsDeleting(false);
        if (result.error) {
            setError(detailFromError(result.error, "Failed to delete provider"));
        } else {
            setDialog(null);
            await load();
        }
    };

    return (
        <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
                <div className="space-y-1">
                    <CardTitle>Saved providers</CardTitle>
                    <p className="text-sm text-muted-foreground">
                        Save extra provider configurations with their own keys. API calls can pick one by name
                        without sending credentials. Several can use the same provider, for example two OpenAI accounts.
                    </p>
                </div>
                <Button type="button" size="sm" onClick={openCreate}>
                    <Plus className="mr-1 h-4 w-4" /> Add provider
                </Button>
            </CardHeader>
            <CardContent className="space-y-6">
                {error && (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {error}
                    </div>
                )}

                {!loading && profiles.length === 0 && !error && (
                    <p className="text-sm text-muted-foreground">No saved providers yet.</p>
                )}

                {SERVICES.map(({ key, label }) => {
                    const items = profiles.filter((profile) => profile.service === key);
                    if (items.length === 0) return null;
                    return (
                        <div key={key} className="space-y-2">
                            <h3 className="text-sm font-medium">{label}</h3>
                            <ul className="divide-y rounded-md border">
                                {items.map((profile) => (
                                    <li key={`${profile.service}-${profile.name}`} className="flex items-center justify-between gap-3 px-3 py-2">
                                        <div className="min-w-0">
                                            <p className="truncate font-mono text-sm">{profile.name}</p>
                                            <p className="truncate text-xs text-muted-foreground">{summarize(profile)}</p>
                                        </div>
                                        <div className="flex shrink-0 gap-1">
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="icon"
                                                aria-label={`Edit ${profile.name}`}
                                                onClick={() => {
                                                    setError(null);
                                                    setDialog({ kind: "edit", profile });
                                                }}
                                            >
                                                <Pencil className="h-4 w-4" />
                                            </Button>
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="icon"
                                                aria-label={`Delete ${profile.name}`}
                                                onClick={() => setDialog({ kind: "delete", profile })}
                                            >
                                                <Trash2 className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    );
                })}
            </CardContent>

            <Dialog
                open={dialog?.kind === "create" || dialog?.kind === "edit"}
                onOpenChange={(open) => !open && setDialog(null)}
            >
                <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>{editing ? `Edit ${editing.name}` : "Add saved provider"}</DialogTitle>
                        <DialogDescription>
                            {editing
                                ? "Leave the API key as it is to keep the stored key."
                                : "The credentials are checked with the provider before saving."}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="profile-name">Name</Label>
                            <Input
                                id="profile-name"
                                value={editing ? editing.name : name}
                                disabled={!!editing}
                                placeholder="e.g. openai-fast"
                                onChange={(event) => setName(event.target.value)}
                            />
                            {nameError && <p className="text-xs text-destructive">{nameError}</p>}
                        </div>
                        <div className="space-y-2">
                            <Label>Service</Label>
                            <Select
                                value={formService}
                                disabled={!!editing}
                                onValueChange={(value) => setService(value as ProfileService)}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {SERVICES.map(({ key, label }) => (
                                        <SelectItem key={key} value={key}>
                                            {label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    {dialog && dialog.kind !== "delete" && (
                        <ServiceConfigurationForm
                            key={`${editing ? `edit-${editing.service}-${editing.name}` : "create"}-${formService}`}
                            mode="global"
                            onlyService={formService as ServiceSegment}
                            forceRealtime={formService === "realtime"}
                            configurationDefaults={formDefaults}
                            initialConfig={initialConfig}
                            submitLabel={editing ? "Save changes" : "Save provider"}
                            onSave={saveProfile}
                        />
                    )}
                </DialogContent>
            </Dialog>

            <Dialog open={dialog?.kind === "delete"} onOpenChange={(open) => !open && setDialog(null)}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>Delete saved provider</DialogTitle>
                        <DialogDescription>
                            {dialog?.kind === "delete"
                                ? `"${dialog.profile.name}" will be removed. API calls that reference it will be rejected.`
                                : ""}
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button type="button" variant="outline" onClick={() => setDialog(null)}>
                            Cancel
                        </Button>
                        <Button type="button" variant="destructive" onClick={confirmDelete} disabled={isDeleting}>
                            {isDeleting ? "Deleting..." : "Delete"}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </Card>
    );
}
