"use client";

import { KeyRound, Save } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { OrganizationAiModelConfigurationV2 } from "@/client/types.gen";
import {
    type ProviderSchema,
    type ServiceConfigurationDefaults,
    ServiceConfigurationForm,
    type ServiceSegment,
} from "@/components/ServiceConfigurationForm";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";

type ModelMode = "dograh" | "byok";

interface DograhDefaults {
    voices: string[];
    speeds: number[];
    languages: string[];
    defaults: {
        voice: string;
        speed: number;
        language: string;
    };
}

export interface ModelConfigurationDefaultsV2 {
    dograh: DograhDefaults;
    byok: {
        pipeline: ServiceConfigurationDefaults;
        realtime: {
            realtime: Record<string, ProviderSchema>;
            llm: Record<string, ProviderSchema>;
            embeddings: Record<string, ProviderSchema>;
            default_providers: ServiceConfigurationDefaults["default_providers"];
        };
    };
}

interface DograhFormState {
    api_key: string;
    voice: string;
    speed: number;
    language: string;
}

interface AIModelConfigurationV2EditorProps {
    defaults: ModelConfigurationDefaultsV2;
    configuration?: OrganizationAiModelConfigurationV2 | Record<string, unknown> | null;
    effectiveConfiguration?: Record<string, unknown> | null;
    onSave: (configuration: OrganizationAiModelConfigurationV2) => Promise<void>;
    submitLabel?: string;
}

function firstApiKey(value: unknown): string {
    if (Array.isArray(value)) return String(value[0] || "");
    return typeof value === "string" ? value : "";
}

function asRecord(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : null;
}

function isDograhEffectiveConfig(config: Record<string, unknown> | null | undefined): boolean {
    if (!config || config.is_realtime) return false;
    const llm = asRecord(config.llm);
    const tts = asRecord(config.tts);
    const stt = asRecord(config.stt);
    return llm?.provider === "dograh" && tts?.provider === "dograh" && stt?.provider === "dograh";
}

function byokDefaults(defaults: ModelConfigurationDefaultsV2): ServiceConfigurationDefaults {
    return {
        llm: defaults.byok.pipeline.llm,
        tts: defaults.byok.pipeline.tts,
        stt: defaults.byok.pipeline.stt,
        embeddings: defaults.byok.pipeline.embeddings,
        realtime: defaults.byok.realtime.realtime,
        default_providers: defaults.byok.pipeline.default_providers,
    };
}

function byokConfigToLegacyShape(config: Record<string, unknown> | null): Record<string, unknown> | null {
    if (!config || config.mode !== "byok") return null;
    const byok = asRecord(config.byok);
    if (!byok) return null;

    if (byok.mode === "realtime") {
        const realtime = asRecord(byok.realtime);
        return {
            is_realtime: true,
            realtime: realtime?.realtime,
            llm: realtime?.llm,
            embeddings: realtime?.embeddings,
        };
    }

    const pipeline = asRecord(byok.pipeline);
    return {
        is_realtime: false,
        llm: pipeline?.llm,
        tts: pipeline?.tts,
        stt: pipeline?.stt,
        embeddings: pipeline?.embeddings,
    };
}

function effectiveConfigToLegacyShape(config: Record<string, unknown> | null): Record<string, unknown> | null {
    if (!config) return null;
    return {
        is_realtime: Boolean(config.is_realtime),
        llm: config.llm,
        tts: config.tts,
        stt: config.stt,
        realtime: config.realtime,
        embeddings: config.embeddings,
    };
}

function emptyByokInitialConfig(): Record<string, unknown> {
    return {
        is_realtime: false,
    };
}

function getByokInitialConfig(
    configuration: Record<string, unknown> | null,
    effectiveConfiguration: Record<string, unknown> | null,
): Record<string, unknown> {
    const byokConfiguration = byokConfigToLegacyShape(configuration);
    if (byokConfiguration) return byokConfiguration;

    if (configuration?.mode === "dograh" || isDograhEffectiveConfig(effectiveConfiguration)) {
        return emptyByokInitialConfig();
    }

    return effectiveConfigToLegacyShape(effectiveConfiguration) || emptyByokInitialConfig();
}

function buildDograhState(
    defaults: ModelConfigurationDefaultsV2,
    configuration: Record<string, unknown> | null,
    effectiveConfiguration: Record<string, unknown> | null,
): DograhFormState {
    const fallback = defaults.dograh.defaults;
    const configuredDograh = configuration?.mode === "dograh" ? asRecord(configuration.dograh) : null;
    if (configuredDograh) {
        return {
            api_key: String(configuredDograh.api_key || ""),
            voice: String(configuredDograh.voice || fallback.voice),
            speed: Number(configuredDograh.speed || fallback.speed),
            language: String(configuredDograh.language || fallback.language),
        };
    }

    if (isDograhEffectiveConfig(effectiveConfiguration)) {
        const llm = asRecord(effectiveConfiguration?.llm);
        const tts = asRecord(effectiveConfiguration?.tts);
        const stt = asRecord(effectiveConfiguration?.stt);
        return {
            api_key: firstApiKey(llm?.api_key || tts?.api_key || stt?.api_key),
            voice: String(tts?.voice || fallback.voice),
            speed: Number(tts?.speed || fallback.speed),
            language: String(stt?.language || fallback.language),
        };
    }

    return {
        api_key: "",
        voice: fallback.voice,
        speed: fallback.speed,
        language: fallback.language,
    };
}

function preferredMode(
    configuration: Record<string, unknown> | null,
    effectiveConfiguration: Record<string, unknown> | null,
): ModelMode {
    if (configuration?.mode === "dograh" || configuration?.mode === "byok") {
        return configuration.mode;
    }
    return isDograhEffectiveConfig(effectiveConfiguration) ? "dograh" : "byok";
}

function hasRequiredApiKey(
    service: ServiceSegment,
    serviceConfiguration: Record<string, unknown>,
    defaults: ServiceConfigurationDefaults,
): boolean {
    const provider = serviceConfiguration.provider as string | undefined;
    if (!provider) return false;
    const providerSchema = service === "realtime"
        ? defaults.realtime?.[provider]
        : defaults[service as "llm" | "tts" | "stt" | "embeddings"]?.[provider];
    const requiresApiKey = providerSchema?.required?.includes("api_key") ?? false;
    if (!requiresApiKey) return true;

    const apiKey = serviceConfiguration.api_key;
    if (Array.isArray(apiKey)) {
        return apiKey.some((key) => typeof key === "string" && key.trim().length > 0);
    }
    return typeof apiKey === "string" && apiKey.trim().length > 0;
}

function requireByokService(
    config: Record<string, unknown>,
    service: ServiceSegment,
    defaults: ServiceConfigurationDefaults,
): Record<string, unknown> {
    const serviceConfiguration = asRecord(config[service]);
    if (
        !serviceConfiguration
        || !serviceConfiguration.provider
        || serviceConfiguration.provider === "dograh"
        || !hasRequiredApiKey(service, serviceConfiguration, defaults)
    ) {
        throw new Error(`${service} configuration is required`);
    }
    return serviceConfiguration;
}

function optionalByokService(config: Record<string, unknown>, service: ServiceSegment): Record<string, unknown> | undefined {
    const serviceConfiguration = asRecord(config[service]);
    if (!serviceConfiguration?.provider || serviceConfiguration.provider === "dograh") return undefined;
    return serviceConfiguration;
}

export function AIModelConfigurationV2Editor({
    defaults,
    configuration,
    effectiveConfiguration,
    onSave,
    submitLabel = "Save Configuration",
}: AIModelConfigurationV2EditorProps) {
    const defaultsForByok = useMemo(() => byokDefaults(defaults), [defaults]);
    const [mode, setMode] = useState<ModelMode>("dograh");
    const [dograh, setDograh] = useState<DograhFormState>(() => ({
        api_key: "",
        voice: defaults.dograh.defaults.voice,
        speed: defaults.dograh.defaults.speed,
        language: defaults.dograh.defaults.language,
    }));
    const [byokInitialConfig, setByokInitialConfig] = useState<Record<string, unknown> | null>(null);
    const [isSavingDograh, setIsSavingDograh] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        const rawConfiguration = asRecord(configuration);
        const rawEffectiveConfiguration = asRecord(effectiveConfiguration);
        setMode(preferredMode(rawConfiguration, rawEffectiveConfiguration));
        setDograh(buildDograhState(defaults, rawConfiguration, rawEffectiveConfiguration));
        setByokInitialConfig(getByokInitialConfig(rawConfiguration, rawEffectiveConfiguration));
    }, [configuration, defaults, effectiveConfiguration]);

    const saveDograhConfiguration = async () => {
        setIsSavingDograh(true);
        setError(null);
        try {
            await onSave({
                version: 2,
                mode: "dograh",
                dograh: {
                    api_key: dograh.api_key.trim(),
                    voice: dograh.voice,
                    speed: dograh.speed,
                    language: dograh.language,
                },
            });
        } catch (err) {
            setError(err instanceof Error ? err.message : "Failed to save configuration");
        } finally {
            setIsSavingDograh(false);
        }
    };

    const saveByokConfiguration = async (config: Record<string, unknown>) => {
        setError(null);
        const isRealtime = Boolean(config.is_realtime);
        const llm = requireByokService(config, "llm", defaultsForByok);
        const embeddings = optionalByokService(config, "embeddings");
        const body: OrganizationAiModelConfigurationV2 = {
            version: 2,
            mode: "byok",
            byok: isRealtime
                ? {
                    mode: "realtime",
                    realtime: {
                        realtime: requireByokService(config, "realtime", defaultsForByok) as never,
                        llm: llm as never,
                        ...(embeddings ? { embeddings: embeddings as never } : {}),
                    },
                }
                : {
                    mode: "pipeline",
                    pipeline: {
                        llm: llm as never,
                        tts: requireByokService(config, "tts", defaultsForByok) as never,
                        stt: requireByokService(config, "stt", defaultsForByok) as never,
                        ...(embeddings ? { embeddings: embeddings as never } : {}),
                    },
                },
        };

        await onSave(body);
    };

    return (
        <div className="space-y-6">
            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}

            <Tabs value={mode} onValueChange={(value) => setMode(value as ModelMode)} className="space-y-6">
                <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="dograh">Dograh</TabsTrigger>
                    <TabsTrigger value="byok">BYOK</TabsTrigger>
                </TabsList>

                <TabsContent value="dograh" className="mt-0">
                    <div className="rounded-lg border p-5">
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2 sm:col-span-2">
                                <Label htmlFor="dograh-api-key">API Key</Label>
                                <div className="relative">
                                    <KeyRound className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                                    <Input
                                        id="dograh-api-key"
                                        className="pl-9"
                                        value={dograh.api_key}
                                        onChange={(event) => setDograh({ ...dograh, api_key: event.target.value })}
                                        placeholder="Enter API key"
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label>Voice</Label>
                                <Select value={dograh.voice} onValueChange={(voice) => setDograh({ ...dograh, voice })}>
                                    <SelectTrigger className="w-full">
                                        <SelectValue placeholder="Select voice" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {defaults.dograh.voices.map((voice) => (
                                            <SelectItem key={voice} value={voice}>
                                                {voice}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="space-y-2">
                                <Label>Speed</Label>
                                <Select
                                    value={String(dograh.speed)}
                                    onValueChange={(speed) => setDograh({ ...dograh, speed: Number(speed) })}
                                >
                                    <SelectTrigger className="w-full">
                                        <SelectValue placeholder="Select speed" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {defaults.dograh.speeds.map((speed) => (
                                            <SelectItem key={speed} value={String(speed)}>
                                                {speed}x
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            <div className="space-y-2 sm:col-span-2">
                                <Label>Language</Label>
                                <Select value={dograh.language} onValueChange={(language) => setDograh({ ...dograh, language })}>
                                    <SelectTrigger className="w-full">
                                        <SelectValue placeholder="Select language" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {defaults.dograh.languages.map((language) => (
                                            <SelectItem key={language} value={language}>
                                                {LANGUAGE_DISPLAY_NAMES[language] || language}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        </div>

                        <Button type="button" className="mt-6 w-full" onClick={saveDograhConfiguration} disabled={isSavingDograh}>
                            <Save className="mr-2 h-4 w-4" />
                            {isSavingDograh ? "Saving..." : submitLabel}
                        </Button>
                    </div>
                </TabsContent>

                <TabsContent value="byok" className="mt-0">
                    <ServiceConfigurationForm
                        key={JSON.stringify(byokInitialConfig)}
                        mode="global"
                        configurationDefaults={defaultsForByok}
                        initialConfig={byokInitialConfig}
                        submitLabel={submitLabel}
                        onSave={saveByokConfiguration}
                    />
                </TabsContent>
            </Tabs>
        </div>
    );
}
