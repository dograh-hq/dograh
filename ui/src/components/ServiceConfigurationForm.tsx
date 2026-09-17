"use client";

import { ExternalLink, Plus, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";

import { getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet } from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { VoiceSelector } from "@/components/VoiceSelector";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";
import { useUserConfig } from "@/context/UserConfigContext";
import type { ModelOverrides } from "@/types/workflow-configurations";

export type ServiceSegment = "llm" | "tts" | "stt" | "embeddings" | "realtime";

interface SchemaProperty {
    type?: string;
    default?: string | number | boolean;
    anyOf?: SchemaProperty[];
    minimum?: number;
    maximum?: number;
    enum?: string[];
    examples?: string[];
    model_options?: Record<string, string[]>;
    visible_for_models?: string[];
    hidden_for_models?: string[];
    allow_custom_input?: boolean;
    $ref?: string;
    description?: string;
    format?: string;
    multiline?: boolean;
    docs_url?: string;
}

export interface ProviderSchema {
    title?: string;
    description?: string;
    provider_docs_url?: string;
    properties: Record<string, SchemaProperty>;
    required?: string[];
    $defs?: Record<string, SchemaProperty>;
    [key: string]: unknown;
}

interface FormValues {
    [key: string]: string | number | boolean;
}

export interface ServiceConfigurationDefaults {
    llm: Record<string, ProviderSchema>;
    tts: Record<string, ProviderSchema>;
    stt: Record<string, ProviderSchema>;
    embeddings: Record<string, ProviderSchema>;
    realtime?: Record<string, ProviderSchema>;
    default_providers: Partial<Record<ServiceSegment, string>>;
}

const STANDARD_TABS: { key: ServiceSegment; label: string }[] = [
    { key: "llm", label: "LLM" },
    { key: "tts", label: "Voice" },
    { key: "stt", label: "Transcriber" },
    { key: "embeddings", label: "Embedding" },
];

const REALTIME_TABS: { key: ServiceSegment; label: string }[] = [
    { key: "realtime", label: "Realtime Model" },
    { key: "llm", label: "LLM" },
    { key: "embeddings", label: "Embedding" },
];

const OVERRIDE_STANDARD_TABS: { key: ServiceSegment; label: string }[] = [
    { key: "llm", label: "LLM" },
    { key: "tts", label: "Voice" },
    { key: "stt", label: "Transcriber" },
];

const OVERRIDE_REALTIME_TABS: { key: ServiceSegment; label: string }[] = [
    { key: "realtime", label: "Realtime Model" },
    { key: "llm", label: "LLM" },
];

// Display names for Sarvam voices
const VOICE_DISPLAY_NAMES: Record<string, string> = {
    "anushka": "Anushka (Female)",
    "manisha": "Manisha (Female)",
    "vidya": "Vidya (Female)",
    "arya": "Arya (Female)",
    "abhilash": "Abhilash (Male)",
    "karun": "Karun (Male)",
    "hitesh": "Hitesh (Male)",
};

export interface ServiceConfigurationFormProps {
    mode: 'global' | 'override';
    currentOverrides?: ModelOverrides;
    onSave: (config: Record<string, unknown>) => Promise<void>;
    /** Text for the submit button. Defaults to "Save Configuration". */
    submitLabel?: string;
    configurationDefaults?: ServiceConfigurationDefaults | null;
    initialConfig?: Record<string, unknown> | null;
    /**
     * When set, locks the realtime/pipeline mode to this value and hides the
     * in-form toggle. The v2 editor uses this to surface realtime
     * ("Speech to Speech") and pipeline (BYOK) as separate top-level tabs.
     * Leave undefined to keep the user-controllable toggle (legacy + overrides).
     */
    forceRealtime?: boolean;
}

function getProviderDisplayName(
    provider: string | undefined,
    providerSchema: ProviderSchema | undefined,
): string | undefined {
    if (!provider) return provider;
    return providerSchema?.title || provider;
}

function getGlobalSummary(
    config: Record<string, unknown> | null | undefined,
    providerSchema: ProviderSchema | undefined,
): string {
    if (!config) return "Not configured";
    const provider = config.provider as string | undefined;
    const model = config.model as string | undefined;
    if (!provider) return "Not configured";
    const providerLabel = getProviderDisplayName(provider, providerSchema);
    return model ? `${providerLabel} / ${model}` : providerLabel || provider;
}

function getSchemaDropdownOptions(
    schema: SchemaProperty | undefined,
    modelValue?: string,
): string[] | undefined {
    let dropdownOptions = schema?.enum || schema?.examples;

    if (schema?.model_options && modelValue && schema.model_options[modelValue]) {
        dropdownOptions = schema.model_options[modelValue];
    }

    return dropdownOptions;
}

function getNumberSchema(schema: SchemaProperty | undefined): SchemaProperty | undefined {
    if (schema?.type === "number") return schema;
    return schema?.anyOf?.find(option => option.type === "number");
}

function isVisibleForModel(schema: SchemaProperty | undefined, model?: string): boolean {
    if (schema?.visible_for_models && !schema.visible_for_models.includes(model || "")) return false;
    return !schema?.hidden_for_models?.includes(model || "");
}

// Speech-to-Speech UI mode selector (UI state only, never persisted).
// "live" selects an explicit Live provider/model/voice set below; every
// other realtime configuration resolves to "realtime". The saved
// provider/model determines the initial mode, so existing configurations
// load correctly with no migration.
const LIVE_MODEL_ID = "gpt-live-1";
const LIVE_BACKEND_FIELDS = ["backend_model", "reasoning_effort", "web_search", "google_search"];
type RealtimeUiMode = "realtime" | "live";

interface LiveVoiceOption {
    value: string;
    label: string;
}

const LIVE_OPENAI_VOICES: LiveVoiceOption[] = [
    { value: "quartz", label: "Quartz — English · Australian · Feminine" },
    { value: "ripple", label: "Ripple — English · Australian · Masculine" },
    { value: "vesper", label: "Vesper — English · British · Masculine" },
    { value: "willow", label: "Willow — English · Irish · Feminine" },
    { value: "stone", label: "Stone — English · Irish · Masculine" },
    { value: "gleam", label: "Gleam — English · North American · Feminine" },
    { value: "meridian", label: "Meridian — English · North American · Masculine" },
    { value: "bossa", label: "Bossa — Portuguese · Brazilian · Feminine" },
    { value: "tempo", label: "Tempo — Portuguese · Brazilian · Masculine" },
    { value: "beacon", label: "Beacon — English · Filipino · Masculine" },
    { value: "delta", label: "Delta — English · Southern U.S. · Feminine" },
    { value: "cinder", label: "Cinder — English · Southern U.S. · Masculine" },
];

interface LiveProviderEntry {
    provider: string;
    label: string;
    // Explicit Live model options for this provider. models[0] is the
    // default for new Live configurations.
    models: string[];
}

// Live Gemini model. Only gemini-3.8-live is supported; Extended Thinking
// is deliberately out of scope for this PR. Saved 3.1 configurations open
// in the original Realtime tab; entering Live defaults to gemini-3.8-live.
const LIVE_GEMINI_38_MODEL = "gemini-3.8-live";

// Explicit saved-model -> initial-tab mapping (UI state only, never
// persisted). Live models open on Live; everything else — including the
// original gemini-3.1-flash-live-preview Realtime configuration — opens on
// Realtime.
const LIVE_MODEL_IDS: ReadonlySet<string> = new Set([
    LIVE_MODEL_ID,
    LIVE_GEMINI_38_MODEL,
]);

// Explicit Live catalog: only these provider/model combinations exist in
// Live mode. Voices for OpenAI and Gemini are the Live-only lists above;
// the shared schemas keep their original options for the Realtime tab.
const LIVE_PROVIDERS: LiveProviderEntry[] = [
    { provider: "openai_realtime", label: "OpenAI", models: [LIVE_MODEL_ID] },
    { provider: "google_realtime", label: "Google Gemini", models: [LIVE_GEMINI_38_MODEL] },
];

interface RealtimeModeSnapshot {
    provider: string;
    model: string;
    voice: string;
    // Whether the voice field was using "Enter Custom Value" free text.
    // Restored alongside the value so custom voices never collapse into a
    // dropdown that cannot represent them.
    customVoice: boolean;
    apiKeys: string[];
}

// Explicit OpenAI Live voice options with user-friendly labels. Saved API
// values are used verbatim. A previously saved voice outside this list is
// preserved as the current selection until deliberately changed.
function liveOpenAIVoices(currentVoice: string): LiveVoiceOption[] {
    if (currentVoice && !LIVE_OPENAI_VOICES.some((voice) => voice.value === currentVoice)) {
        return [
            { value: currentVoice, label: `${currentVoice} (current)` },
            ...LIVE_OPENAI_VOICES,
        ];
    }
    return LIVE_OPENAI_VOICES;
}

// Explicit Live-only voice list for Gemini, per Google's documented native
// voices. The shared google_realtime schema keeps its original 5 voices so
// the legacy Realtime tab is unchanged; only the Live branch uses this set.
const LIVE_GEMINI_VOICES: LiveVoiceOption[] = [
    { value: "Zephyr", label: "Zephyr — Bright" },
    { value: "Puck", label: "Puck — Upbeat" },
    { value: "Charon", label: "Charon — Informative" },
    { value: "Kore", label: "Kore — Firm" },
    { value: "Fenrir", label: "Fenrir — Excitable" },
    { value: "Leda", label: "Leda — Youthful" },
    { value: "Orus", label: "Orus — Firm" },
    { value: "Aoede", label: "Aoede — Breezy" },
    { value: "Callirrhoe", label: "Callirrhoe — Easy-going" },
    { value: "Autonoe", label: "Autonoe — Bright" },
    { value: "Enceladus", label: "Enceladus — Breathy" },
    { value: "Iapetus", label: "Iapetus — Clear" },
    { value: "Umbriel", label: "Umbriel — Easy-going" },
    { value: "Algieba", label: "Algieba — Smooth" },
    { value: "Despina", label: "Despina — Smooth" },
    { value: "Erinome", label: "Erinome — Clear" },
    { value: "Algenib", label: "Algenib — Gravelly" },
    { value: "Rasalgethi", label: "Rasalgethi — Informative" },
    { value: "Laomedeia", label: "Laomedeia — Upbeat" },
    { value: "Achernar", label: "Achernar — Soft" },
    { value: "Alnilam", label: "Alnilam — Firm" },
    { value: "Schedar", label: "Schedar — Even" },
    { value: "Gacrux", label: "Gacrux — Mature" },
    { value: "Pulcherrima", label: "Pulcherrima — Forward" },
    { value: "Achird", label: "Achird — Friendly" },
    { value: "Zubenelgenubi", label: "Zubenelgenubi — Casual" },
    { value: "Vindemiatrix", label: "Vindemiatrix — Gentle" },
    { value: "Sadachbia", label: "Sadachbia — Lively" },
    { value: "Sadaltager", label: "Sadaltager — Knowledgeable" },
    { value: "Sulafat", label: "Sulafat — Warm" },
];

const LIVE_GEMINI_VOICES_URL = "https://ai.google.dev/gemini-api/docs/speech-generation#voices";

// Same saved-value preservation as the OpenAI list: a previously saved
// voice outside the documented set stays selectable until changed.
function liveGeminiVoices(currentVoice: string): LiveVoiceOption[] {
    if (currentVoice && !LIVE_GEMINI_VOICES.some((voice) => voice.value === currentVoice)) {
        return [
            { value: currentVoice, label: `${currentVoice} (current)` },
            ...LIVE_GEMINI_VOICES,
        ];
    }
    return LIVE_GEMINI_VOICES;
}

export function ServiceConfigurationForm({
    mode,
    currentOverrides,
    onSave,
    submitLabel,
    configurationDefaults,
    initialConfig,
    forceRealtime,
}: ServiceConfigurationFormProps) {
    const [apiError, setApiError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const [isRealtime, setIsRealtime] = useState(forceRealtime ?? false);
    // Local-only Speech-to-Speech architecture selection. Initialized from
    // the saved model; afterwards only the toggle changes it. Never saved.
    const [realtimeUiMode, setRealtimeUiMode] = useState<RealtimeUiMode>("realtime");
    const userSelectedMode = useRef(false);
    // Snapshots preserve each mode's provider/model/voice/keys across
    // switches so toggling never erases user input.
    const realtimeSnapshot = useRef<RealtimeModeSnapshot | null>(null);
    const liveSnapshot = useRef<RealtimeModeSnapshot | null>(null);
    // Last Live voice per provider (plus whether it was a custom value), so
    // switching Live providers and back restores the previous selection
    // instead of resetting to the provider default.
    const liveVoiceMemory = useRef<Record<string, { voice: string; custom: boolean }>>({});
    const { userConfig } = useUserConfig();
    const [schemas, setSchemas] = useState<Record<ServiceSegment, Record<string, ProviderSchema>>>({
        llm: {},
        tts: {},
        stt: {},
        embeddings: {},
        realtime: {},
    });
    const [serviceProviders, setServiceProviders] = useState<Record<ServiceSegment, string>>({
        llm: "",
        tts: "",
        stt: "",
        embeddings: "",
        realtime: "",
    });
    const [apiKeys, setApiKeys] = useState<Record<ServiceSegment, string[]>>({
        llm: [""],
        tts: [""],
        stt: [""],
        embeddings: [""],
        realtime: [""],
    });
    const [isCustomInput, setIsCustomInput] = useState<Record<string, boolean>>({});

    // Override-specific state: which services have the override toggle enabled
    const [enabledOverrides, setEnabledOverrides] = useState<Record<string, boolean>>({
        llm: false,
        tts: false,
        stt: false,
        realtime: false,
    });

    const {
        register,
        handleSubmit,
        formState: { },
        reset,
        getValues,
        setValue,
        watch
    } = useForm();

    // Build effective config source: overlay overrides onto global config
    const configSource = useMemo(() => {
        const baseConfig = initialConfig ?? userConfig;
        if (mode === 'global' || !currentOverrides) return baseConfig;
        // Merge overrides onto global config for form initialization
        const merged = { ...baseConfig } as Record<string, unknown>;
        const overrideServices: (keyof ModelOverrides)[] = ["llm", "tts", "stt", "realtime"];
        for (const svc of overrideServices) {
            if (svc === "is_realtime") continue;
            const overrideVal = currentOverrides[svc];
            if (overrideVal && typeof overrideVal === "object") {
                const globalVal = (baseConfig as Record<string, unknown> | null)?.[svc] as Record<string, unknown> | undefined;
                merged[svc] = { ...globalVal, ...overrideVal };
            }
        }
        if (currentOverrides.is_realtime !== undefined) {
            merged.is_realtime = currentOverrides.is_realtime;
        }
        return merged as typeof userConfig;
    }, [mode, userConfig, currentOverrides, initialConfig]);

    // Sync the mode toggle from the saved model until the user toggles it
    // manually. Models in LIVE_MODEL_IDS resolve to Live, everything else
    // resolves to Realtime.
    useEffect(() => {
        if (userSelectedMode.current) return;
        const realtime = (configSource as Record<string, unknown> | null)?.realtime as Record<string, unknown> | undefined;
        const savedModel = realtime?.model;
        setRealtimeUiMode(typeof savedModel === "string" && LIVE_MODEL_IDS.has(savedModel) ? "live" : "realtime");
    }, [configSource]);

    useEffect(() => {
        const fetchConfigurations = async () => {
            let defaultsData = configurationDefaults;
            if (!defaultsData) {
                const response = await getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet();
                if (!response.data) {
                    console.error("Failed to fetch configurations");
                    return;
                }
                defaultsData = response.data as unknown as ServiceConfigurationDefaults;
            }

            const realtimeSchemas = (defaultsData.realtime || {}) as Record<string, ProviderSchema>;
            const pickDefaultProvider = (
                service: ServiceSegment,
                schemaMap: Record<string, ProviderSchema>,
            ) => {
                const preferred = defaultsData.default_providers?.[service];
                if (preferred && schemaMap[preferred]) return preferred;
                return Object.keys(schemaMap)[0] || "";
            };

            setSchemas({
                llm: defaultsData.llm,
                tts: defaultsData.tts,
                stt: defaultsData.stt,
                embeddings: defaultsData.embeddings,
                realtime: realtimeSchemas,
            });

            // Restore realtime toggle (skip when the parent locks the mode)
            const configData = configSource as Record<string, unknown> | null;
            if (forceRealtime === undefined && configData?.is_realtime) {
                setIsRealtime(true);
            }

            const defaultValues: Record<string, string | number | boolean> = {};
            const selectedProviders: Record<ServiceSegment, string> = {
                llm: pickDefaultProvider("llm", defaultsData.llm),
                tts: pickDefaultProvider("tts", defaultsData.tts),
                stt: pickDefaultProvider("stt", defaultsData.stt),
                embeddings: pickDefaultProvider("embeddings", defaultsData.embeddings),
                realtime: "",
            };

            const realtimeProviderKeys = Object.keys(realtimeSchemas);
            if (realtimeProviderKeys.length > 0) {
                selectedProviders.realtime = realtimeProviderKeys[0];
            }

            const loadedApiKeys: Record<ServiceSegment, string[]> = {
                llm: [""],
                tts: [""],
                stt: [""],
                embeddings: [""],
                realtime: [""],
            };

            const setServicePropertyValues = (service: ServiceSegment) => {
                const src = service === "realtime"
                    ? (configSource as Record<string, unknown> | null)?.realtime as Record<string, unknown> | undefined
                    : (configSource as Record<string, unknown> | null)?.[service] as Record<string, unknown> | undefined;

                const schemaSource = service === "realtime"
                    ? realtimeSchemas
                    : defaultsData[service as "llm" | "tts" | "stt" | "embeddings"] as Record<string, ProviderSchema> | undefined;

                if (src?.provider) {
                    Object.entries(src).forEach(([field, value]) => {
                        if (field === "api_key") {
                            if (mode === 'override') {
                                // In override mode, only load API keys from the override itself
                                const overrideVal = currentOverrides?.[service as keyof ModelOverrides];
                                const overrideApiKey = overrideVal && typeof overrideVal === "object"
                                    ? (overrideVal as Record<string, unknown>).api_key
                                    : undefined;
                                if (overrideApiKey) {
                                    loadedApiKeys[service] = Array.isArray(overrideApiKey)
                                        ? overrideApiKey as string[]
                                        : [overrideApiKey as string];
                                } else {
                                    loadedApiKeys[service] = [""];
                                }
                            } else {
                                if (Array.isArray(value)) {
                                    loadedApiKeys[service] = (value as string[]).length > 0 ? value as string[] : [""];
                                } else {
                                    loadedApiKeys[service] = value ? [value as string] : [""];
                                }
                            }
                        } else if (field !== "provider") {
                            defaultValues[`${service}_${field}`] = value as string | number | boolean;
                        }
                    });
                    selectedProviders[service] = src.provider as string;
                    const properties = schemaSource?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            const key = `${service}_${field}`;
                            if (field !== "provider" && field !== "api_key" && schema.default !== undefined && !(key in defaultValues)) {
                                defaultValues[key] = schema.default;
                            }
                        });
                    }
                } else {
                    const properties = schemaSource?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            if (field !== "provider" && schema.default !== undefined) {
                                defaultValues[`${service}_${field}`] = schema.default;
                            }
                        });
                    }
                }
            };

            setServicePropertyValues("llm");
            setServicePropertyValues("tts");
            setServicePropertyValues("stt");
            setServicePropertyValues("embeddings");
            setServicePropertyValues("realtime");

            // Detect custom inputs
            const detectedCustomInput: Record<string, boolean> = {};
            const allSchemas = { ...defaultsData, realtime: realtimeSchemas } as unknown as Record<string, Record<string, ProviderSchema>>;
            (["llm", "tts", "stt", "embeddings", "realtime"] as ServiceSegment[]).forEach(service => {
                const provider = selectedProviders[service];
                const providerSchema = allSchemas[service]?.[provider];
                if (!providerSchema) return;

                const src = service === "realtime"
                    ? (configSource as Record<string, unknown> | null)?.realtime as Record<string, unknown> | undefined
                    : (configSource as Record<string, unknown> | null)?.[service] as Record<string, unknown> | undefined;

                Object.entries(providerSchema.properties).forEach(([field, schema]) => {
                    const actualSchema = (schema as SchemaProperty).$ref && providerSchema.$defs
                        ? providerSchema.$defs[(schema as SchemaProperty).$ref!.split('/').pop() || '']
                        : schema as SchemaProperty;

                    if (!actualSchema?.allow_custom_input) return;

                    const savedValue = src?.[field] as string | undefined;
                    const modelValue = src?.model as string | undefined;
                    const dropdownOptions = getSchemaDropdownOptions(actualSchema, modelValue);
                    if (savedValue && dropdownOptions && !dropdownOptions.includes(savedValue)) {
                        detectedCustomInput[`${service}_${field}`] = true;
                    }
                });
            });

            // Initialize override toggles
            if (mode === 'override') {
                setEnabledOverrides({
                    llm: !!currentOverrides?.llm,
                    tts: !!currentOverrides?.tts,
                    stt: !!currentOverrides?.stt,
                    realtime: !!currentOverrides?.realtime,
                });
            }

            reset(defaultValues);
            setApiKeys(loadedApiKeys);
            setServiceProviders(selectedProviders);
            setIsCustomInput(detectedCustomInput);
        };
        fetchConfigurations();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [reset, configSource, configurationDefaults]);

    // Reset voice when TTS model changes if the provider has model-dependent voice options
    const ttsModel = watch("tts_model");
    useEffect(() => {
        const voiceSchema = schemas?.tts?.[serviceProviders.tts]?.properties?.voice;
        const modelOptions = voiceSchema?.model_options;
        if (!modelOptions || !ttsModel) return;

        const validVoices = modelOptions[ttsModel as string];
        const currentVoice = getValues("tts_voice") as string;
        const isCustomVoice = !!isCustomInput.tts_voice;
        if (validVoices && currentVoice && !validVoices.includes(currentVoice) && !isCustomVoice) {
            setValue("tts_voice", validVoices[0], { shouldDirty: true });
        }
    }, [ttsModel, serviceProviders.tts, setValue, getValues, schemas, isCustomInput.tts_voice]);

    const realtimeModel = watch("realtime_model");
    useEffect(() => {
        // The Live branch manages its own explicit voice list; the schema
        // model_options reset below only applies to the Realtime branch.
        if (realtimeUiMode === "live") return;
        const voiceSchema = schemas?.realtime?.[serviceProviders.realtime]?.properties?.voice;
        const voices = voiceSchema?.model_options?.[realtimeModel as string];
        if (!voices?.length) return;
        const currentVoice = getValues("realtime_voice") as string;
        if (!voices.includes(currentVoice)) {
            setValue("realtime_voice", voices[0], { shouldDirty: true });
            setIsCustomInput(previous => ({ ...previous, realtime_voice: false }));
        }
    }, [realtimeModel, serviceProviders.realtime, schemas, getValues, setValue, realtimeUiMode]);

    // Reset language when TTS model changes if the provider has model-dependent language options
    useEffect(() => {
        const languageSchema = schemas?.tts?.[serviceProviders.tts]?.properties?.language;
        const modelOptions = languageSchema?.model_options;
        if (!modelOptions || !ttsModel) return;

        const validLanguages = modelOptions[ttsModel as string];
        const currentLanguage = getValues("tts_language") as string;
        const isCustomLanguage = !!isCustomInput.tts_language;
        if (validLanguages && currentLanguage && !validLanguages.includes(currentLanguage) && !isCustomLanguage) {
            setValue("tts_language", validLanguages[0], { shouldDirty: true });
        }
    }, [ttsModel, serviceProviders.tts, setValue, getValues, schemas, isCustomInput.tts_language]);

    // Reset language when STT model changes if the provider has model-dependent language options
    const sttModel = watch("stt_model");
    useEffect(() => {
        const languageSchema = schemas?.stt?.[serviceProviders.stt]?.properties?.language;
        const modelOptions = languageSchema?.model_options;
        if (!modelOptions || !sttModel) return;

        const validLanguages = modelOptions[sttModel as string];
        const currentLanguage = getValues("stt_language") as string;
        if (validLanguages && currentLanguage && !validLanguages.includes(currentLanguage)) {
            setValue("stt_language", validLanguages[0], { shouldDirty: true });
        }
    }, [sttModel, serviceProviders.stt, setValue, getValues, schemas]);

    const handleProviderChange = (service: ServiceSegment, providerName: string) => {
        if (!providerName) return;

        const currentValues = getValues();
        const preservedValues: Record<string, string | number | boolean> = {};

        Object.keys(currentValues).forEach(key => {
            if (!key.startsWith(`${service}_`)) {
                preservedValues[key] = currentValues[key];
            }
        });

        if (schemas?.[service]?.[providerName]) {
            const providerSchema = schemas[service][providerName];
            Object.entries(providerSchema.properties).forEach(([field, schema]: [string, SchemaProperty]) => {
                if (field !== "provider" && schema.default !== undefined) {
                    preservedValues[`${service}_${field}`] = schema.default;
                }
            });
        }

        preservedValues[`${service}_provider`] = providerName;
        reset(preservedValues);
        setServiceProviders(prev => ({ ...prev, [service]: providerName }));
        setApiKeys(prev => ({ ...prev, [service]: [""] }));

        setIsCustomInput(prev => {
            const next = { ...prev };
            Object.keys(next).forEach(key => {
                if (key.startsWith(`${service}_`)) delete next[key];
            });
            return next;
        });
    };

    const buildServiceConfig = (service: ServiceSegment, data: FormValues) => {
        const config: Record<string, string | number | boolean | string[]> = {
            provider: serviceProviders[service],
        };
        const keys = apiKeys[service].map(k => k.trim()).filter(k => k.length > 0);
        if (keys.length > 0) {
            config.api_key = mode === 'override' ? keys[0] : keys;
        }
        Object.entries(data).forEach(([property, value]) => {
            if (!property.startsWith(`${service}_`)) return;
            const field = property.slice(service.length + 1);
            if (field === "api_key" || field === "provider") return;
            const fieldSchema = schemas?.[service]?.[serviceProviders[service]]?.properties[field];
            // In Live mode, drop values whose field no longer exists in the
            // current provider schema (e.g. OpenAI backend fields after
            // switching to Gemini). Values are kept in form state and
            // restored by the mode snapshots.
            if (service === "realtime" && realtimeUiMode === "live" && !fieldSchema) return;
            if (isLiveBackendFieldHidden(service, field)) {
                // Preserve a previously saved Live-only opt-in (e.g.
                // google_search) when saving from the Realtime tab so the
                // round-trip doesn't wipe it. All other hidden values stay
                // dropped, keeping Realtime saves behaviorally unchanged.
                if (value === true) {
                    config[field] = value;
                }
                return;
            }
            if (!isVisibleForModel(fieldSchema, data[`${service}_model`] as string)) return;
            config[field] = value as string | number;
        });
        return config;
    };

    const onSubmit = async (data: FormValues) => {
        setApiError(null);
        setIsSaving(true);

        try {
            if (mode === 'override') {
                // Build model_overrides for enabled services only
                const modelOverrides: Record<string, unknown> = {};
                const services = isRealtime ? ["realtime", "llm"] : ["llm", "tts", "stt"];
                for (const svc of services) {
                    if (enabledOverrides[svc]) {
                        modelOverrides[svc] = buildServiceConfig(svc as ServiceSegment, data);
                    }
                }
                // Include is_realtime if it differs from global
                const globalIsRealtime = !!(userConfig as Record<string, unknown> | null)?.is_realtime;
                if (isRealtime !== globalIsRealtime) {
                    modelOverrides.is_realtime = isRealtime;
                }
                await onSave({
                    model_overrides: Object.keys(modelOverrides).length > 0 ? modelOverrides : undefined,
                });
            } else {
                // Global mode: save all services
                const saveConfig: Record<string, unknown> = {
                    llm: buildServiceConfig("llm", data),
                    tts: buildServiceConfig("tts", data),
                    stt: buildServiceConfig("stt", data),
                    is_realtime: isRealtime,
                };
                if (serviceProviders.realtime) {
                    saveConfig.realtime = buildServiceConfig("realtime", data);
                }
                const embeddingsKeys = apiKeys.embeddings.map(k => k.trim()).filter(k => k.length > 0);
                if (embeddingsKeys.length > 0) {
                    saveConfig.embeddings = buildServiceConfig("embeddings", data);
                }
                await onSave(saveConfig);
            }
            setApiError(null);
        } catch (error: unknown) {
            if (error instanceof Error) {
                setApiError(error.message);
            } else {
                setApiError('An unknown error occurred');
            }
        } finally {
            setIsSaving(false);
        }
    };

    const getConfigFields = (service: ServiceSegment): string[] => {
        const currentProvider = serviceProviders[service];
        const providerSchema = schemas?.[service]?.[currentProvider];
        if (!providerSchema) return [];
        const model = watch(`${service}_model`) as string;
        return Object.keys(providerSchema.properties).filter(
            field => field !== "provider" && field !== "api_key"
                && !isLiveBackendFieldHidden(service, field)
                && isVisibleForModel(providerSchema.properties[field], model)
        );
    };

    // Backend-only fields stay hidden while the Realtime UI mode is active,
    // even if the selected model would otherwise show them. In Live mode the
    // existing per-model schema visibility applies unchanged.
    const isLiveBackendFieldHidden = (service: ServiceSegment, field: string): boolean => {
        return service === "realtime"
            && realtimeUiMode === "realtime"
            && LIVE_BACKEND_FIELDS.includes(field);
    };

    // Switch the realtime UI mode. Only provider/model/voice selection
    // changes; API keys and all other field values are preserved. Switching
    // snapshots the outgoing mode so returning restores it instead of
    // reconstructing defaults.
    const snapshotRealtimeMode = () => {
        realtimeSnapshot.current = {
            provider: serviceProviders.realtime,
            model: getValues("realtime_model") as string,
            voice: getValues("realtime_voice") as string,
            customVoice: !!isCustomInput.realtime_voice,
            apiKeys: [...apiKeys.realtime],
        };
    };

    const applyLiveProvider = (entry: LiveProviderEntry) => {
        // Remember the outgoing provider's voice before switching, so
        // switching back restores it (see liveVoiceMemory).
        const outgoingProvider = serviceProviders.realtime;
        const outgoingModel = getValues("realtime_model") as string;
        const outgoingVoice = getValues("realtime_voice") as string;
        if (outgoingProvider && outgoingVoice) {
            liveVoiceMemory.current[outgoingProvider] = {
                voice: outgoingVoice,
                custom: !!isCustomInput.realtime_voice,
            };
        }
        if (serviceProviders.realtime !== entry.provider) {
            setServiceProviders(prev => ({ ...prev, realtime: entry.provider }));
        }
        // New Live selections default to the entry's first model. The Live
        // Gemini list is strictly 3.8-only, so a saved 3.1 model is never
        // carried into Live; entering Live defaults to gemini-3.8-live.
        const currentLiveModel = getValues("realtime_model") as string;
        const validLiveModels = entry.models;
        const nextLiveModel = validLiveModels.includes(currentLiveModel)
            ? currentLiveModel
            : entry.models[0];
        setValue("realtime_model", nextLiveModel, { shouldDirty: true });
        setIsCustomInput(prev => ({ ...prev, realtime_model: false }));
        const voiceSchema = schemas?.realtime?.[entry.provider]?.properties?.voice;
        const schemaVoices = getSchemaDropdownOptions(voiceSchema, nextLiveModel) || [];
        // applyLiveProvider only runs in Live mode, so provider-specific
        // Live voice lists apply here; the shared schemas (and the Realtime
        // tab) are untouched.
        const liveVoices = entry.provider === "openai_realtime"
            ? LIVE_OPENAI_VOICES.map((voice) => voice.value)
            : entry.provider === "google_realtime"
                ? LIVE_GEMINI_VOICES.map((voice) => voice.value)
                : schemaVoices;
        const rememberedVoice = liveVoiceMemory.current[entry.provider];
        const currentVoice = rememberedVoice?.voice || getValues("realtime_voice") as string;
        // Default new Live selections to the provider default. A saved or
        // already-selected voice (including a custom value the user typed,
        // or a legacy value outside the outgoing provider's own options) is
        // never replaced here; only an empty or outgoing-listed-but-Live-
        // unknown non-custom voice falls back.
        const isCustomVoice = rememberedVoice ? rememberedVoice.custom : !!isCustomInput.realtime_voice;
        const outgoingVoiceOptions = getSchemaDropdownOptions(
            schemas?.realtime?.[outgoingProvider]?.properties?.voice,
            outgoingModel,
        ) || [];
        const carriedLegacyVoice = !!currentVoice
            && !isCustomVoice
            && !liveVoices.includes(currentVoice)
            && outgoingProvider === entry.provider
            && !outgoingVoiceOptions.includes(currentVoice);
        const fallbackVoice = entry.provider === "openai_realtime"
            ? "gleam"
            : (voiceSchema?.default as string | undefined) || liveVoices[0] || "";
        if ((!currentVoice || (!liveVoices.includes(currentVoice) && !isCustomVoice && !carriedLegacyVoice)) && fallbackVoice) {
            setValue("realtime_voice", fallbackVoice, { shouldDirty: true });
            setIsCustomInput(prev => ({ ...prev, realtime_voice: false }));
        } else if (rememberedVoice || carriedLegacyVoice) {
            // Restore the stashed per-provider selection (or a carried
            // legacy value) verbatim.
            setValue("realtime_voice", currentVoice, { shouldDirty: true });
            setIsCustomInput(prev => ({ ...prev, realtime_voice: rememberedVoice ? rememberedVoice.custom : false }));
        }
        // Ensure backend fields present in form state (schema defaults for
        // genuinely missing values only — existing values, including an
        // explicit false, are never overwritten), so a fresh Live selection
        // saves a complete valid configuration.
        const entrySchema = schemas?.realtime?.[entry.provider];
        for (const field of LIVE_BACKEND_FIELDS) {
            const fieldSchema = entrySchema?.properties?.[field];
            const key = `realtime_${field}`;
            const existing = getValues(key) as unknown;
            const missing = existing === undefined || existing === null || existing === "";
            if (fieldSchema && fieldSchema.default !== undefined && missing) {
                setValue(key, fieldSchema.default, { shouldDirty: true });
            }
        }
    };

    const handleArchitectureMode = (nextMode: RealtimeUiMode) => {
        userSelectedMode.current = true;
        if (nextMode === "live") {
            snapshotRealtimeMode();
            const remembered = liveSnapshot.current;
            // Prefer the remembered Live provider; otherwise stay on the
            // current provider when it is itself a Live provider (so e.g. a
            // saved Gemini voice is not routed through OpenAI defaults and
            // destroyed); otherwise default to OpenAI.
            const entry = LIVE_PROVIDERS.find((item) => item.provider === remembered?.provider)
                ?? LIVE_PROVIDERS.find((item) => item.provider === serviceProviders.realtime)
                ?? LIVE_PROVIDERS[0];
            setRealtimeUiMode("live");
            if (remembered && remembered.provider === entry.provider) {
                if (serviceProviders.realtime !== remembered.provider) {
                    setServiceProviders(prev => ({ ...prev, realtime: remembered.provider }));
                }
                setValue("realtime_model", remembered.model || entry.models[0], { shouldDirty: true });
                if (remembered.voice) {
                    setValue("realtime_voice", remembered.voice, { shouldDirty: true });
                    setIsCustomInput(prev => ({ ...prev, realtime_voice: !!remembered.customVoice }));
                }
                setApiKeys(prev => ({ ...prev, realtime: [...remembered.apiKeys] }));
            } else {
                applyLiveProvider(entry);
            }
        } else {
            const remembered = realtimeSnapshot.current;
            setRealtimeUiMode("realtime");
            if (remembered) {
                liveSnapshot.current = {
                    provider: serviceProviders.realtime,
                    model: getValues("realtime_model") as string,
                    voice: getValues("realtime_voice") as string,
                    customVoice: !!isCustomInput.realtime_voice,
                    apiKeys: [...apiKeys.realtime],
                };
                if (serviceProviders.realtime !== remembered.provider) {
                    setServiceProviders(prev => ({ ...prev, realtime: remembered.provider }));
                }
                setValue("realtime_model", remembered.model, { shouldDirty: true });
                setValue("realtime_voice", remembered.voice, { shouldDirty: true });
                setIsCustomInput(prev => ({ ...prev, realtime_model: false, realtime_voice: !!remembered.customVoice }));
                setApiKeys(prev => ({ ...prev, realtime: [...remembered.apiKeys] }));
            } else if ((getValues("realtime_model") as string) === LIVE_MODEL_ID) {
                const schema = schemas?.realtime?.[serviceProviders.realtime];
                const fallback = schema?.properties?.model?.default;
                if (typeof fallback === "string" && fallback !== LIVE_MODEL_ID) {
                    setValue("realtime_model", fallback, { shouldDirty: true });
                }
            }
        }
    };

    const renderServiceFields = (service: ServiceSegment) => {
        const currentProvider = serviceProviders[service];
        const providerSchema = schemas?.[service]?.[currentProvider];
        const availableProviders = schemas?.[service] ? Object.keys(schemas[service]) : [];
        const configFields = getConfigFields(service);

        const renderModeToggle = () => (
            <div className="space-y-2">
                <Tabs value={realtimeUiMode} onValueChange={(value) => handleArchitectureMode(value as RealtimeUiMode)}>
                    <TabsList className="grid w-full grid-cols-2">
                        <TabsTrigger value="realtime">Realtime</TabsTrigger>
                        <TabsTrigger value="live">Live</TabsTrigger>
                    </TabsList>
                </Tabs>
                <p className="text-xs text-muted-foreground">
                    {realtimeUiMode === "live"
                        ? "Use a Live speech-to-speech model."
                        : "Use the existing realtime speech-to-speech configuration."}
                </p>
            </div>
        );

        const renderApiKeysBlock = () => (
            <>
                {currentProvider && providerSchema && providerSchema.properties.api_key && (
                    <div className="space-y-2">
                        <Label>{mode === 'override' ? 'API Key (leave empty to use global)' : 'API Key(s)'}</Label>
                        {renderFieldDescription("api_key", providerSchema)}
                        {apiKeys[service].map((key, index) => (
                            <div key={index} className="flex gap-2">
                                <Input
                                    type="text"
                                    placeholder="Enter API key"
                                    value={key}
                                    onChange={(e) => {
                                        const newKeys = [...apiKeys[service]];
                                        newKeys[index] = e.target.value;
                                        setApiKeys(prev => ({ ...prev, [service]: newKeys }));
                                    }}
                                />
                                {apiKeys[service].length > 1 && (
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="icon"
                                        className="shrink-0"
                                        onClick={() => {
                                            setApiKeys(prev => ({
                                                ...prev,
                                                [service]: prev[service].filter((_, i) => i !== index),
                                            }));
                                        }}
                                    >
                                        <X className="h-4 w-4" />
                                    </Button>
                                )}
                            </div>
                        ))}
                        {mode !== 'override' && (
                            <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={() => {
                                    setApiKeys(prev => ({
                                        ...prev,
                                        [service]: [...prev[service], ""],
                                    }));
                                }}
                            >
                                <Plus className="h-4 w-4 mr-1" /> Add API Key
                            </Button>
                        )}
                    </div>
                )}
            </>
        );

        // Original Dograh Realtime renderer (3beecf23). Renders the
        // schema-driven form exactly as the baseline did; the only
        // difference from baseline is that the surrounding wrapper adds the
        // Realtime/Live mode toggle above it.
        const renderRealtimeBranch = () => (
            <>
                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <Label>Provider</Label>
                        <Select
                            value={currentProvider}
                            onValueChange={(providerName) => {
                                handleProviderChange(service, providerName);
                            }}
                        >
                            <SelectTrigger className="w-full">
                                <SelectValue placeholder="Select provider" />
                            </SelectTrigger>
                            <SelectContent>
                                {availableProviders.map((provider) => (
                                    <SelectItem key={provider} value={provider}>
                                        {getProviderDisplayName(provider, schemas?.[service]?.[provider])}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                        {(providerSchema?.description || providerSchema?.provider_docs_url) && (
                            <p className="text-xs text-muted-foreground">
                                {providerSchema?.description}{" "}
                                {providerSchema?.provider_docs_url && (
                                    <a
                                        href={providerSchema.provider_docs_url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="inline-flex items-center gap-0.5 underline"
                                    >
                                        Learn more <ExternalLink className="h-3 w-3" />
                                    </a>
                                )}
                            </p>
                        )}
                    </div>

                    {currentProvider && providerSchema && configFields[0] && (
                        <div className="space-y-2">
                            <Label className="capitalize">{configFields[0].replace(/_/g, ' ')}</Label>
                            {renderField(service, configFields[0], providerSchema)}
                        </div>
                    )}
                </div>

                {currentProvider && providerSchema && configFields.length > 1 && (
                    <div className="grid grid-cols-2 gap-4">
                        {configFields.slice(1).map((field) => {
                            const fieldSchema = providerSchema.properties[field];
                            const actualFieldSchema = fieldSchema?.$ref && providerSchema.$defs
                                ? providerSchema.$defs[fieldSchema.$ref.split('/').pop() || '']
                                : fieldSchema;
                            const fullWidth = actualFieldSchema?.multiline;
                            return (
                                <div key={field} className={`space-y-2 ${fullWidth ? "col-span-2" : ""}`}>
                                    <Label className="capitalize">{field.replace(/_/g, ' ')}</Label>
                                    {renderField(service, field, providerSchema)}
                                </div>
                            );
                        })}
                    </div>
                )}

                {renderApiKeysBlock()}
            </>
        );

        // Explicit Live voice selector shared by the Live providers: a
        // predefined dropdown plus "Enter Custom Value" free text (saved
        // verbatim into the normal `voice` field) and a provider docs link.
        const renderLiveVoiceSelect = (
            voiceOptions: LiveVoiceOption[],
            checkboxId: string,
            docsHref: string,
            docsLabel: string,
        ) => (
            <>
                {isCustomInput.realtime_voice ? (
                    <Input
                        type="text"
                        placeholder="Enter voice"
                        value={watch("realtime_voice") as string || ""}
                        onChange={(e) => {
                            setValue("realtime_voice", e.target.value, { shouldDirty: true });
                        }}
                    />
                ) : (
                    <Select
                        value={watch("realtime_voice") as string || ""}
                        onValueChange={(value) => {
                            if (!value) return;
                            setValue("realtime_voice", value, { shouldDirty: true });
                            setIsCustomInput(prev => ({ ...prev, realtime_voice: false }));
                        }}
                    >
                        <SelectTrigger className="w-full">
                            <SelectValue placeholder="Select voice" />
                        </SelectTrigger>
                        <SelectContent>
                            {voiceOptions.map((voice) => (
                                <SelectItem key={voice.value} value={voice.value}>
                                    {voice.label}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                )}
                <div className="flex items-center space-x-2">
                    <Checkbox
                        id={checkboxId}
                        checked={!!isCustomInput.realtime_voice}
                        onCheckedChange={(checked) => {
                            setIsCustomInput(prev => ({ ...prev, realtime_voice: checked as boolean }));
                        }}
                    />
                    <Label htmlFor={checkboxId} className="text-sm font-normal cursor-pointer">
                        Enter Custom Value
                    </Label>
                </div>
                <p className="text-xs text-muted-foreground">
                    <a
                        href={docsHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 underline"
                    >
                        {docsLabel} <ExternalLink className="h-3 w-3" />
                    </a>
                </p>
            </>
        );

        // Explicit Live branch: providers/models/voices are enumerated here,
        // never derived from or filtered out of the Realtime registry view.
        // All other fields render through the existing schema-driven path
        // against the real provider schema, so descriptions, validation, and
        // the save payload stay identical to a manual configuration.
        const renderLiveBranch = () => {
            const liveEntry = LIVE_PROVIDERS.find((item) => item.provider === currentProvider)
                ?? LIVE_PROVIDERS[0];
            // Explicit per-provider Live model options. Live Gemini is
            // gemini-3.8-live only.
            const liveModelOptions: string[] = liveEntry.models;
            const liveVoiceOptions: LiveVoiceOption[] = currentProvider === "openai_realtime"
                ? liveOpenAIVoices(watch("realtime_voice") as string || "")
                : currentProvider === "google_realtime"
                    ? liveGeminiVoices(watch("realtime_voice") as string || "")
                    : (getSchemaDropdownOptions(
                        providerSchema?.properties?.voice,
                        watch("realtime_model") as string || "",
                    ) || []).map((voice) => ({
                        value: voice,
                        label: voice.charAt(0).toUpperCase() + voice.slice(1),
                    }));
            const remainingFields = configFields.filter(
                (field) => field !== "model" && field !== "voice"
                    // Google Search renders explicitly below for Live Gemini
                    // and must never appear via generic field iteration.
                    && !(currentProvider === "google_realtime" && field === "google_search"),
            );

            // Explicit Google Search toggle for Live Gemini. Rendered
            // literally (not via schema iteration, visibility metadata, or
            // backend-field filtering) so the control is always present.
            // Bound directly to the google_search form value; the backend
            // persists it when present in schema.
            const renderLiveGoogleSearch = () => {
                const searchChecked = watch("realtime_google_search") === true;
                return (
                    <div className="space-y-2">
                        <Label className="capitalize">Google Search</Label>
                        <div className="flex items-center gap-3 py-1">
                            <Switch
                                id="live-gemini-google-search"
                                checked={searchChecked}
                                onCheckedChange={(next) => {
                                    setValue("realtime_google_search", next, { shouldDirty: true });
                                }}
                            />
                            <Label htmlFor="live-gemini-google-search" className="text-sm text-muted-foreground cursor-pointer">
                                {searchChecked ? "On" : "Off"}
                            </Label>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            Allow Gemini to use Google Search for up-to-date information.
                        </p>
                        <p className="text-xs text-muted-foreground">
                            <a
                                href="https://ai.google.dev/gemini-api/docs/live-api/tools"
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-0.5 underline"
                            >
                                View Gemini Live tool documentation <ExternalLink className="h-3 w-3" />
                            </a>
                        </p>
                    </div>
                );
            };
            return (
                <>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label>Provider</Label>
                            <Select
                                value={currentProvider}
                                onValueChange={(providerName) => {
                                    const entry = LIVE_PROVIDERS.find((item) => item.provider === providerName);
                                    if (!entry) return;
                                    applyLiveProvider(entry);
                                }}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder="Select provider" />
                                </SelectTrigger>
                                <SelectContent>
                                    {LIVE_PROVIDERS.map((entry) => (
                                        <SelectItem key={entry.provider} value={entry.provider}>
                                            {entry.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            {(providerSchema?.description || providerSchema?.provider_docs_url) && (
                                <p className="text-xs text-muted-foreground">
                                    {providerSchema?.description}{" "}
                                    {providerSchema?.provider_docs_url && (
                                        <a
                                            href={providerSchema.provider_docs_url}
                                            target="_blank"
                                            rel="noopener noreferrer"
                                            className="inline-flex items-center gap-0.5 underline"
                                        >
                                            Learn more <ExternalLink className="h-3 w-3" />
                                        </a>
                                    )}
                                </p>
                            )}
                        </div>

                        <div className="space-y-2">
                            <Label className="capitalize">Live Model</Label>
                            <Select
                                value={watch("realtime_model") as string || ""}
                                onValueChange={(value) => {
                                    if (!value) return;
                                    setValue("realtime_model", value, { shouldDirty: true });
                                }}
                            >
                                <SelectTrigger className="w-full">
                                    <SelectValue placeholder="Select model" />
                                </SelectTrigger>
                                <SelectContent>
                                    {liveModelOptions.map((model) => (
                                        <SelectItem key={model} value={model}>
                                            {model}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label className="capitalize">Voice</Label>
                            {currentProvider === "openai_realtime" ? (
                                renderLiveVoiceSelect(
                                    liveVoiceOptions,
                                    "live-voice-custom-input",
                                    "https://developers.openai.com/api/docs/guides/live-conversations#voice-options",
                                    "View OpenAI Live voice options",
                                )
                            ) : currentProvider === "google_realtime" ? (
                                renderLiveVoiceSelect(
                                    liveVoiceOptions,
                                    "live-gemini-voice-custom-input",
                                    LIVE_GEMINI_VOICES_URL,
                                    "View Gemini voice options",
                                )
                            ) : (
                                currentProvider && providerSchema && renderField(service, "voice", providerSchema)
                            )}
                        </div>
                        {remainingFields.filter((field) => field !== "voice").length > 0 && (
                            <div className="space-y-2">
                                <Label className="capitalize">{remainingFields.filter((field) => field !== "voice")[0].replace(/_/g, ' ')}</Label>
                                {currentProvider && providerSchema && renderField(service, remainingFields.filter((field) => field !== "voice")[0], providerSchema)}
                            </div>
                        )}
                    </div>

                    {currentProvider === "google_realtime" && renderLiveGoogleSearch()}

                    {remainingFields.filter((field) => field !== "voice").length > 1 && (
                        <div className="grid grid-cols-2 gap-4">
                            {remainingFields.filter((field) => field !== "voice").slice(1).map((field) => {
                                const fieldSchema = providerSchema?.properties[field];
                                const actualFieldSchema = fieldSchema?.$ref && providerSchema?.$defs
                                    ? providerSchema.$defs[fieldSchema.$ref.split('/').pop() || '']
                                    : fieldSchema;
                                const fullWidth = actualFieldSchema?.multiline;
                                return (
                                    <div key={field} className={`space-y-2 ${fullWidth ? "col-span-2" : ""}`}>
                                        <Label className="capitalize">{field.replace(/_/g, ' ')}</Label>
                                        {currentProvider && providerSchema && renderField(service, field, providerSchema)}
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {renderApiKeysBlock()}
                </>
            );
        };

        if (service === "realtime") {
            return (
                <div className="space-y-6">
                    {renderModeToggle()}
                    {realtimeUiMode === "live" ? renderLiveBranch() : renderRealtimeBranch()}
                </div>
            );
        }

        return (
            <div className="space-y-6">
                {renderRealtimeBranch()}
            </div>
        );
    };

    const renderFieldDescription = (field: string, providerSchema: ProviderSchema) => {
        const schema = providerSchema.properties[field];
        if (!schema) return null;
        const actualSchema = schema.$ref && providerSchema.$defs
            ? providerSchema.$defs[schema.$ref.split('/').pop() || '']
            : schema;
        if (!actualSchema?.description && !actualSchema?.docs_url) return null;
        return (
            <p className="text-xs text-muted-foreground">
                {actualSchema?.description}{" "}
                {actualSchema?.docs_url && (
                    <a
                        href={actualSchema.docs_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-0.5 underline"
                    >
                        Supported languages <ExternalLink className="h-3 w-3" />
                    </a>
                )}
            </p>
        );
    };

    const renderField = (service: ServiceSegment, field: string, providerSchema: ProviderSchema) => {
        return (
            <>
                {renderFieldInput(service, field, providerSchema)}
                {renderFieldDescription(field, providerSchema)}
            </>
        );
    };

    const renderFieldInput = (service: ServiceSegment, field: string, providerSchema: ProviderSchema) => {
        const schema = providerSchema.properties[field];
        const actualSchema = schema.$ref && providerSchema.$defs
            ? providerSchema.$defs[schema.$ref.split('/').pop() || '']
            : schema;
        const dropdownOptions = getSchemaDropdownOptions(
            actualSchema,
            watch(`${service}_model`) as string | undefined,
        );
        const numberSchema = getNumberSchema(actualSchema);

        if (service === "tts" && field === "voice" && !actualSchema?.allow_custom_input) {
            if (!dropdownOptions) {
                return (
                    <VoiceSelector
                        provider={serviceProviders.tts}
                        value={watch(`${service}_${field}`) as string || ""}
                        onChange={(voiceId) => {
                            setValue(`${service}_${field}`, voiceId, { shouldDirty: true });
                        }}
                        model={watch("tts_model") as string || undefined}
                    />
                );
            }
        }

        if (actualSchema?.allow_custom_input && dropdownOptions && dropdownOptions.length > 0) {
            const fieldKey = `${service}_${field}`;
            const currentValue = watch(fieldKey) as string || "";
            const options = dropdownOptions;

            if (isCustomInput[fieldKey]) {
                return (
                    <div className="space-y-2">
                        <Input
                            type="text"
                            placeholder={`Enter ${field}`}
                            value={currentValue}
                            onChange={(e) => {
                                setValue(fieldKey, e.target.value, { shouldDirty: true });
                            }}
                        />
                        <div className="flex items-center space-x-2">
                            <Checkbox
                                id={`custom-input-${fieldKey}`}
                                checked={true}
                                onCheckedChange={(checked) => {
                                    setIsCustomInput(prev => ({ ...prev, [fieldKey]: checked as boolean }));
                                    if (!checked && options.length > 0) {
                                        setValue(fieldKey, options[0], { shouldDirty: true });
                                    }
                                }}
                            />
                            <Label htmlFor={`custom-input-${fieldKey}`} className="text-sm font-normal cursor-pointer">
                                Enter Custom Value
                            </Label>
                        </div>
                    </div>
                );
            }

            return (
                <div className="space-y-2">
                    <Select
                        value={currentValue}
                        onValueChange={(value) => {
                            if (!value) return;
                            setValue(fieldKey, value, { shouldDirty: true });
                        }}
                    >
                        <SelectTrigger className="w-full">
                            <SelectValue placeholder={`Select ${field}`} />
                        </SelectTrigger>
                        <SelectContent>
                            {options.map((value: string) => (
                                <SelectItem key={value} value={value}>
                                    {value}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <div className="flex items-center space-x-2">
                        <Checkbox
                            id={`custom-input-${fieldKey}-dropdown`}
                            checked={false}
                            onCheckedChange={(checked) => {
                                setIsCustomInput(prev => ({ ...prev, [fieldKey]: checked as boolean }));
                            }}
                        />
                        <Label htmlFor={`custom-input-${fieldKey}-dropdown`} className="text-sm font-normal cursor-pointer">
                            Enter Custom Value
                        </Label>
                    </div>
                </div>
            );
        }

        if (dropdownOptions && dropdownOptions.length > 0) {
            const getDisplayName = (value: string) => {
                if (field === "language") {
                    return LANGUAGE_DISPLAY_NAMES[value] || value;
                }
                if (field === "voice") {
                    return VOICE_DISPLAY_NAMES[value] || value.charAt(0).toUpperCase() + value.slice(1);
                }
                return value;
            };

            return (
                <Select
                    value={watch(`${service}_${field}`) as string || ""}
                    onValueChange={(value) => {
                        if (!value) return;
                        setValue(`${service}_${field}`, value, { shouldDirty: true });
                    }}
                >
                    <SelectTrigger className="w-full">
                        <SelectValue placeholder={`Select ${field}`} />
                    </SelectTrigger>
                    <SelectContent>
                        {dropdownOptions.map((value: string) => (
                            <SelectItem key={value} value={value}>
                                {getDisplayName(value)}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            );
        }

        if (actualSchema?.type === "boolean") {
            const fieldKey = `${service}_${field}`;
            const checked = watch(fieldKey);
            return (
                <div className="flex items-center gap-3 py-1">
                    <Switch
                        id={`${service}-${field}`}
                        checked={checked === true || checked === "true"}
                        onCheckedChange={(next) => setValue(fieldKey, next, { shouldDirty: true })}
                    />
                    <Label htmlFor={`${service}-${field}`} className="text-sm text-muted-foreground cursor-pointer">
                        {checked ? "On" : "Off"}
                    </Label>
                </div>
            );
        }

        if (actualSchema?.multiline) {
            return (
                <Textarea
                    rows={6}
                    className="font-mono text-xs"
                    placeholder={`Enter ${field}`}
                    {...register(`${service}_${field}`, {
                        required: service !== "embeddings" && providerSchema.required?.includes(field),
                    })}
                />
            );
        }

        return (
            <Input
                type={numberSchema ? "number" : "text"}
                {...(numberSchema && {
                    step: "any",
                    min: numberSchema.minimum,
                    max: numberSchema.maximum,
                })}
                placeholder={`Enter ${field}`}
                {...register(`${service}_${field}`, {
                    required: service !== "embeddings" && providerSchema.required?.includes(field),
                    ...(numberSchema && {
                        setValueAs: (value: string) => value === "" ? undefined : Number(value),
                    }),
                })}
            />
        );
    };

    const handleOverrideToggle = (service: string, enabled: boolean) => {
        setEnabledOverrides(prev => ({ ...prev, [service]: enabled }));
    };

    const renderOverrideToggle = (service: ServiceSegment, label: string) => {
        const globalVal = (userConfig as Record<string, unknown> | null)?.[service] as Record<string, unknown> | null | undefined;
        const isEnabled = enabledOverrides[service];
        const globalProvider = globalVal?.provider as string | undefined;
        const globalProviderSchema = globalProvider ? schemas?.[service]?.[globalProvider] : undefined;

        return (
            <div className="flex items-center justify-between p-3 border rounded-md bg-muted/20 mb-4">
                <div className="space-y-0.5">
                    <Label htmlFor={`override-${service}`} className="text-sm cursor-pointer font-medium">
                        Override {label}
                    </Label>
                    {!isEnabled && (
                        <p className="text-xs text-muted-foreground">
                            Using global: {getGlobalSummary(globalVal, globalProviderSchema)}
                        </p>
                    )}
                </div>
                <Switch
                    id={`override-${service}`}
                    checked={isEnabled}
                    onCheckedChange={(checked) => handleOverrideToggle(service, checked)}
                />
            </div>
        );
    };

    const getVisibleTabs = () => {
        if (mode === 'override') {
            return isRealtime ? OVERRIDE_REALTIME_TABS : OVERRIDE_STANDARD_TABS;
        }
        return isRealtime ? REALTIME_TABS : STANDARD_TABS;
    };

    const visibleTabs = getVisibleTabs();
    const defaultTab = isRealtime ? "realtime" : "llm";

    return (
        <form onSubmit={handleSubmit(onSubmit)}>
            {/* Realtime toggle — hidden when the parent locks the mode (v2 tabs) */}
            {forceRealtime === undefined && (
                <div className="flex items-center justify-between mb-4 p-4 border rounded-lg">
                    <div>
                        <Label htmlFor="realtime-toggle" className="text-sm font-medium">
                            Realtime Mode
                        </Label>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Uses a single speech-to-speech model (no separate STT/TTS). An LLM is still required for variable extraction and QA.
                        </p>
                    </div>
                    <Switch
                        id="realtime-toggle"
                        checked={isRealtime}
                        onCheckedChange={setIsRealtime}
                    />
                </div>
            )}

            <Card>
                <CardContent className="pt-6">
                    <Tabs key={defaultTab} defaultValue={defaultTab} className="w-full">
                        <TabsList className="grid w-full mb-6" style={{ gridTemplateColumns: `repeat(${visibleTabs.length}, 1fr)` }}>
                            {visibleTabs.map(({ key, label }) => (
                                <TabsTrigger key={key} value={key}>
                                    {label}
                                </TabsTrigger>
                            ))}
                        </TabsList>

                        {visibleTabs.map(({ key, label }) => (
                            <TabsContent key={key} value={key} className="mt-0">
                                {mode === 'override' && renderOverrideToggle(key, label)}
                                {(mode === 'global' || enabledOverrides[key]) && renderServiceFields(key)}
                            </TabsContent>
                        ))}
                    </Tabs>
                </CardContent>
            </Card>

            {apiError && <p className="text-red-500 mt-4">{apiError}</p>}

            <Button type="submit" className="w-full mt-6" disabled={isSaving}>
                {isSaving ? "Saving..." : (submitLabel || "Save Configuration")}
            </Button>
        </form>
    );
}
