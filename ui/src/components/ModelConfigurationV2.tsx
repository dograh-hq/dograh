"use client";

import { ExternalLink, RefreshCw, Save } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import TimezoneSelect, { type ITimezoneOption } from "react-timezone-select";

import {
    getModelConfigurationPreferencesApiV1OrganizationsModelConfigurationsPreferencesGet,
    getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get,
    getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet,
    migrateModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2MigratePost,
    saveModelConfigurationPreferencesApiV1OrganizationsModelConfigurationsPreferencesPut,
    saveModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Put,
} from "@/client/sdk.gen";
import type {
    OrganizationAiModelConfigurationPreferences,
    OrganizationAiModelConfigurationResponse,
    OrganizationAiModelConfigurationV2,
} from "@/client/types.gen";
import { AIModelConfigurationV2Editor, type ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";
import { ServiceConfigurationForm } from "@/components/ServiceConfigurationForm";
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useUserConfig } from "@/context/UserConfigContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const emptyPreferences: OrganizationAiModelConfigurationPreferences = {
    test_phone_number: "",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
};

const timezoneSelectStyles = {
    control: (base: Record<string, unknown>, state: { isFocused: boolean }) => ({
        ...base,
        minHeight: "36px",
        fontSize: "14px",
        backgroundColor: "var(--background)",
        borderColor: state.isFocused ? "var(--ring)" : "var(--border)",
        boxShadow: state.isFocused ? "0 0 0 2px color-mix(in srgb, var(--ring) 20%, transparent)" : "none",
        "&:hover": { borderColor: "var(--border)" },
    }),
    menu: (base: Record<string, unknown>) => ({
        ...base,
        zIndex: 9999,
        backgroundColor: "var(--popover)",
        border: "1px solid var(--border)",
        boxShadow: "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
    }),
    menuList: (base: Record<string, unknown>) => ({
        ...base,
        backgroundColor: "var(--popover)",
        padding: 0,
    }),
    option: (base: Record<string, unknown>, state: { isSelected: boolean; isFocused: boolean }) => ({
        ...base,
        backgroundColor: state.isSelected ? "var(--accent)" : state.isFocused ? "var(--accent)" : "var(--popover)",
        color: "var(--foreground)",
        cursor: "pointer",
        "&:active": { backgroundColor: "var(--accent)" },
    }),
    singleValue: (base: Record<string, unknown>) => ({ ...base, color: "var(--foreground)" }),
    input: (base: Record<string, unknown>) => ({ ...base, color: "var(--foreground)" }),
    placeholder: (base: Record<string, unknown>) => ({ ...base, color: "var(--muted-foreground)" }),
    indicatorSeparator: (base: Record<string, unknown>) => ({ ...base, backgroundColor: "var(--border)" }),
    dropdownIndicator: (base: Record<string, unknown>) => ({
        ...base,
        color: "var(--muted-foreground)",
        "&:hover": { color: "var(--foreground)" },
    }),
};

function getTimezoneValue(tz: ITimezoneOption | string): string {
    return typeof tz === "string" ? tz : tz.value;
}

export default function ModelConfigurationV2({
    docsUrl,
    initialAction,
}: {
    docsUrl?: string;
    initialAction?: string;
}) {
    const auth = useAuth();
    const { refreshConfig, saveUserConfig } = useUserConfig();
    const timezoneSelectId = useId();
    const hasFetched = useRef(false);
    const hasAppliedInitialMigrationAction = useRef(false);

    const [defaults, setDefaults] = useState<ModelConfigurationDefaultsV2 | null>(null);
    const [response, setResponse] = useState<OrganizationAiModelConfigurationResponse | null>(null);
    const [preferences, setPreferences] = useState<OrganizationAiModelConfigurationPreferences>(emptyPreferences);
    const [timezone, setTimezone] = useState<ITimezoneOption | string>(emptyPreferences.timezone || "UTC");
    const [loading, setLoading] = useState(true);
    const [savingPreferences, setSavingPreferences] = useState(false);
    const [migrating, setMigrating] = useState(false);
    const [migrationDialogOpen, setMigrationDialogOpen] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    const applyResponse = (nextResponse: OrganizationAiModelConfigurationResponse) => {
        setResponse(nextResponse);
    };

    useEffect(() => {
        if (auth.loading || !auth.user || hasFetched.current) return;
        hasFetched.current = true;

        const load = async () => {
            setLoading(true);
            setError(null);
            const [defaultsResult, configResult, preferencesResult] = await Promise.all([
                getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet(),
                getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get(),
                getModelConfigurationPreferencesApiV1OrganizationsModelConfigurationsPreferencesGet(),
            ]);

            if (defaultsResult.error) {
                setError(detailFromError(defaultsResult.error, "Failed to load model configuration defaults"));
                setLoading(false);
                return;
            }
            if (configResult.error) {
                setError(detailFromError(configResult.error, "Failed to load model configuration"));
                setLoading(false);
                return;
            }
            if (preferencesResult.error) {
                setError(detailFromError(preferencesResult.error, "Failed to load model configuration preferences"));
                setLoading(false);
                return;
            }

            const nextDefaults = defaultsResult.data as ModelConfigurationDefaultsV2;
            if (!nextDefaults || !configResult.data) {
                setError("Failed to load model configuration");
                setLoading(false);
                return;
            }
            setDefaults(nextDefaults);
            applyResponse(configResult.data);

            const nextPreferences = preferencesResult.data || emptyPreferences;
            setPreferences({
                test_phone_number: nextPreferences.test_phone_number || "",
                timezone: nextPreferences.timezone || emptyPreferences.timezone,
            });
            setTimezone(nextPreferences.timezone || emptyPreferences.timezone || "UTC");
            setLoading(false);
        };

        load();

    }, [auth.loading, auth.user]);

    useEffect(() => {
        if (hasAppliedInitialMigrationAction.current) return;
        if (initialAction !== "migrate_to_v2") return;
        if (loading || response?.source !== "legacy_user_v1") return;
        hasAppliedInitialMigrationAction.current = true;
        setMigrationDialogOpen(true);
    }, [initialAction, loading, response?.source]);

    const saveConfiguration = async (configuration: OrganizationAiModelConfigurationV2) => {
        if (!defaults) return;
        setError(null);
        setNotice(null);

        const result = await saveModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Put({
            body: configuration,
        });

        if (result.error) {
            throw new Error(detailFromError(result.error, "Failed to save model configuration"));
        }
        if (!result.data) {
            throw new Error("Failed to save model configuration");
        }

        applyResponse(result.data);
        await refreshConfig();
        setNotice("Model configuration saved");
    };

    const savePreferences = async () => {
        setSavingPreferences(true);
        setError(null);
        setNotice(null);

        const result = await saveModelConfigurationPreferencesApiV1OrganizationsModelConfigurationsPreferencesPut({
            body: {
                test_phone_number: preferences.test_phone_number || null,
                timezone: getTimezoneValue(timezone),
            },
        });

        if (result.error) {
            setError(detailFromError(result.error, "Failed to save preferences"));
        } else if (!result.data) {
            setError("Failed to save preferences");
        } else {
            setPreferences(result.data);
            await refreshConfig();
            setNotice("Preferences saved");
        }
        setSavingPreferences(false);
    };

    const migrateConfiguration = async () => {
        if (!defaults) return;
        setMigrating(true);
        setError(null);
        setNotice(null);

        const result = await migrateModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2MigratePost();
        if (result.error) {
            setError(detailFromError(result.error, "Failed to migrate model configuration"));
        } else if (!result.data) {
            setError("Failed to migrate model configuration");
        } else {
            applyResponse(result.data);
            await refreshConfig();
            setNotice("Configuration migrated to v2");
            setMigrationDialogOpen(false);
        }
        setMigrating(false);
    };

    const migrationWarningDialog = (
        <AlertDialog open={migrationDialogOpen} onOpenChange={setMigrationDialogOpen}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>Migrate model configuration to v2?</AlertDialogTitle>
                    <AlertDialogDescription>
                        Your configurations will be migrated to v2. After migration, check your global configuration and workflow model overrides, then run a test call to make sure everything is working.
                    </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel disabled={migrating}>Cancel</AlertDialogCancel>
                    <Button type="button" onClick={migrateConfiguration} disabled={migrating}>
                        {migrating ? "Migrating..." : "Migrate to v2"}
                    </Button>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );

    if (loading) {
        return (
            <div className="w-full max-w-4xl mx-auto space-y-6">
                <Skeleton className="h-10 w-80" />
                <Skeleton className="h-28 w-full" />
                <Skeleton className="h-96 w-full" />
            </div>
        );
    }

    const source = response?.source || "empty";

    if (source !== "organization_v2") {
        return (
            <div className="w-full max-w-4xl mx-auto space-y-6">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                        <div className="flex items-center gap-2">
                            <h1 className="text-3xl font-bold">AI Models Configuration</h1>
                            <Badge variant="outline">
                                {source === "legacy_user_v1" ? "legacy" : "v1"}
                            </Badge>
                        </div>
                        <p className="mt-2 text-sm text-muted-foreground">
                            Configure your AI model, voice, and transcription services.{" "}
                            {docsUrl && (
                                <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                                    Learn more <ExternalLink className="h-3 w-3" />
                                </a>
                            )}
                        </p>
                    </div>
                    {source === "legacy_user_v1" && (
                        <Button type="button" variant="outline" onClick={() => setMigrationDialogOpen(true)} disabled={migrating}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {migrating ? "Migrating..." : "Migrate to v2"}
                        </Button>
                    )}
                </div>

                {error && (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {error}
                    </div>
                )}
                {notice && (
                    <div className="rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-300">
                        {notice}
                    </div>
                )}

                <ServiceConfigurationForm
                    mode="global"
                    onSave={async (config) => {
                        setError(null);
                        setNotice(null);
                        await saveUserConfig(config as Parameters<typeof saveUserConfig>[0]);
                        await refreshConfig();
                        if (defaults) {
                            const configResult = await getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get();
                            if (configResult.data) {
                                applyResponse(configResult.data);
                            }
                        }
                        setNotice("Configuration saved");
                    }}
                />
                {migrationWarningDialog}
            </div>
        );
    }

    return (
        <div className="w-full max-w-4xl mx-auto space-y-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <h1 className="text-3xl font-bold">AI Models Configuration</h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Organization-scoped model settings.{" "}
                        {docsUrl && (
                            <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                                Learn more <ExternalLink className="h-3 w-3" />
                            </a>
                        )}
                    </p>
                </div>
            </div>

            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}
            {notice && (
                <div className="rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-300">
                    {notice}
                </div>
            )}

            {defaults && response && (
                <AIModelConfigurationV2Editor
                    defaults={defaults}
                    configuration={response.configuration}
                    effectiveConfiguration={response.effective_configuration}
                    onSave={saveConfiguration}
                />
            )}

            <div className="rounded-lg border p-5">
                <div className="mb-4">
                    <h2 className="text-base font-semibold">Preferences</h2>
                </div>
                <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                        <Label htmlFor="test-phone-number">Test Phone Number</Label>
                        <Input
                            id="test-phone-number"
                            value={preferences.test_phone_number || ""}
                            onChange={(event) => setPreferences({ ...preferences, test_phone_number: event.target.value })}
                            placeholder="+15551234567"
                        />
                    </div>
                    <div className="space-y-2">
                        <Label>Timezone</Label>
                        <TimezoneSelect
                            instanceId={timezoneSelectId}
                            value={timezone}
                            onChange={setTimezone}
                            styles={timezoneSelectStyles}
                        />
                    </div>
                </div>
                <Button type="button" variant="outline" className="mt-5" onClick={savePreferences} disabled={savingPreferences}>
                    <Save className="mr-2 h-4 w-4" />
                    {savingPreferences ? "Saving..." : "Save Preferences"}
                </Button>
            </div>
            {migrationWarningDialog}
        </div>
    );
}
