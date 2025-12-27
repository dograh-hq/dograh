"use client";

import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet } from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VoiceSelector } from "@/components/VoiceSelector";
import { useUserConfig } from "@/context/UserConfigContext";

type ServiceSegment = "llm" | "tts" | "stt";

interface SchemaProperty {
    type?: string;
    default?: string | number | boolean;
    enum?: string[];
    examples?: string[];
    $ref?: string;
    description?: string;
    format?: string;
}

interface ProviderSchema {
    properties: Record<string, SchemaProperty>;
    required?: string[];
    $defs?: Record<string, SchemaProperty>;
    [key: string]: unknown;
}

interface FormValues {
    [key: string]: string | number | boolean;
}

const TAB_CONFIG: { key: ServiceSegment; label: string }[] = [
    { key: "llm", label: "LLM" },
    { key: "tts", label: "Voice" },
    { key: "stt", label: "Transcriber" },
];

// Display names for language codes (Deepgram + Sarvam)
const LANGUAGE_DISPLAY_NAMES: Record<string, string> = {
    // Deepgram languages
    "multi": "Multilingual (Auto-detect)",
    "en": "English",
    "en-US": "English (US)",
    "en-GB": "English (UK)",
    "en-AU": "English (Australia)",
    "en-IN": "English (India)",
    "es": "Spanish",
    "es-419": "Spanish (Latin America)",
    "fr": "French",
    "fr-CA": "French (Canada)",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "pt-BR": "Portuguese (Brazil)",
    "nl": "Dutch",
    "hi": "Hindi",
    "ja": "Japanese",
    "ko": "Korean",
    "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
    "ru": "Russian",
    "pl": "Polish",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "id": "Indonesian",
    "th": "Thai",
    // Sarvam Indian languages
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "hi-IN": "Hindi",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "od-IN": "Odia",
    "pa-IN": "Punjabi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "as-IN": "Assamese",
};

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

export default function ServiceConfiguration() {
    const [apiError, setApiError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const { userConfig, saveUserConfig } = useUserConfig();
    const [schemas, setSchemas] = useState<Record<ServiceSegment, Record<string, ProviderSchema>>>({
        llm: {},
        tts: {},
        stt: {}
    });
    const [serviceProviders, setServiceProviders] = useState<Record<ServiceSegment, string>>({
        llm: "",
        tts: "",
        stt: ""
    });
    const [isManualModelInput, setIsManualModelInput] = useState(false);
    const [hasCheckedManualMode, setHasCheckedManualMode] = useState(false);

    const {
        register,
        handleSubmit,
        formState: { errors },
        reset,
        getValues,
        setValue,
        watch
    } = useForm();

    useEffect(() => {
        const fetchConfigurations = async () => {
            const response = await getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet();
            if (response.data) {
                setSchemas({
                    llm: response.data.llm as Record<string, ProviderSchema>,
                    tts: response.data.tts as Record<string, ProviderSchema>,
                    stt: response.data.stt as Record<string, ProviderSchema>
                });
            } else {
                console.error("Failed to fetch configurations");
                return;
            }

            const defaultValues: Record<string, string | number | boolean> = {};
            const selectedProviders: Record<ServiceSegment, string> = {
                llm: response.data.default_providers.llm,
                tts: response.data.default_providers.tts,
                stt: response.data.default_providers.stt
            };

            const setServicePropertyValues = (service: ServiceSegment) => {
                if (userConfig?.[service]?.provider) {
                    Object.entries(userConfig?.[service]).forEach(([field, value]) => {
                        if (field !== "provider") {
                            defaultValues[`${service}_${field}`] = value;
                        }
                    });
                    selectedProviders[service] = userConfig?.[service]?.provider as string;
                } else {
                    const properties = response.data[service]?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            if (field !== "provider" && schema.default) {
                                defaultValues[`${service}_${field}`] = schema.default;
                            }
                        });
                    }
                }
            }

            setServicePropertyValues("llm");
            setServicePropertyValues("tts");
            setServicePropertyValues("stt");

            // IMPORTANT: Reset form values BEFORE changing providers
            // Otherwise, Radix Select sees old values that don't match new provider's enum
            // and calls onValueChange('') to clear "invalid" values
            reset(defaultValues);
            setServiceProviders(selectedProviders);
        };
        fetchConfigurations();
    }, [reset, userConfig]);

    // Check if the saved LLM model is not in the suggested options (custom model)
    useEffect(() => {
        if (hasCheckedManualMode) return;

        const currentProvider = serviceProviders.llm;
        const providerSchema = schemas?.llm?.[currentProvider];
        if (!providerSchema) return;

        const modelSchema = providerSchema.properties.model;
        const actualModelSchema = modelSchema?.$ref && providerSchema.$defs
            ? providerSchema.$defs[modelSchema.$ref.split('/').pop() || '']
            : modelSchema;

        if (actualModelSchema?.examples && userConfig?.llm?.model) {
            const savedModel = userConfig.llm.model as string;
            const isInOptions = actualModelSchema.examples.includes(savedModel);
            if (!isInOptions) {
                setIsManualModelInput(true);
            }
            setHasCheckedManualMode(true);
        }
    }, [schemas, serviceProviders.llm, userConfig?.llm?.model, hasCheckedManualMode]);

    const handleProviderChange = (service: ServiceSegment, providerName: string) => {
        if (!providerName) {
            return;
        }

        const currentValues = getValues();
        const preservedValues: Record<string, string | number | boolean> = {};

        // Preserve values from other services
        Object.keys(currentValues).forEach(key => {
            if (!key.startsWith(`${service}_`)) {
                preservedValues[key] = currentValues[key];
            }
        });

        // Set default values from schema
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

        // Reset manual model input when LLM provider changes
        if (service === "llm") {
            setIsManualModelInput(false);
        }
    }


    const onSubmit = async (data: FormValues) => {
        setApiError(null);
        setIsSaving(true);

        const userConfig = {
            llm: {
                provider: serviceProviders.llm,
                api_key: data.llm_api_key as string,
                model: data.llm_model as string
            },
            tts: {
                provider: serviceProviders.tts,
                api_key: data.tts_api_key as string
            },
            stt: {
                provider: serviceProviders.stt,
                api_key: data.stt_api_key as string
            }
        };

        // Add any extra properties in the payload
        Object.entries(data).forEach(([property, value]) => {
            const parts = property.split('_');
            const service = parts[0] as ServiceSegment;
            const field = parts.slice(1).join('_');

            if (userConfig[service] && !(field in userConfig[service])) {
                (userConfig[service] as Record<string, string>)[field] = value as string;
            }
        });

        try {
            await saveUserConfig({
                llm: userConfig.llm,
                tts: userConfig.tts,
                stt: userConfig.stt
            });
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

        // Find all config fields (not provider, not api_key)
        return Object.keys(providerSchema.properties).filter(
            field => field !== "provider" && field !== "api_key"
        );
    };

    const renderServiceFields = (service: ServiceSegment) => {
        const currentProvider = serviceProviders[service];
        const providerSchema = schemas?.[service]?.[currentProvider];
        const availableProviders = schemas?.[service] ? Object.keys(schemas[service]) : [];
        const configFields = getConfigFields(service);

        return (
            <div className="space-y-6">
                {/* Provider and first config field in one row */}
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
                                        {provider}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    {currentProvider && providerSchema && configFields[0] && (
                        <div className="space-y-2">
                            <Label className="capitalize">{configFields[0].replace(/_/g, ' ')}</Label>
                            {renderField(service, configFields[0], providerSchema)}
                        </div>
                    )}
                </div>

                {/* Additional config fields (like voice for TTS) */}
                {currentProvider && providerSchema && configFields.length > 1 && (
                    <div className="grid grid-cols-2 gap-4">
                        {configFields.slice(1).map((field) => (
                            <div key={field} className="space-y-2">
                                <Label className="capitalize">{field.replace(/_/g, ' ')}</Label>
                                {renderField(service, field, providerSchema)}
                            </div>
                        ))}
                    </div>
                )}

                {/* API Key in bottom row */}
                {currentProvider && providerSchema && providerSchema.properties.api_key && (
                    <div className="space-y-2">
                        <Label>API Key</Label>
                        <Input
                            type="text"
                            placeholder="Enter API key"
                            {...register(`${service}_api_key`, {
                                required: providerSchema.required?.includes("api_key"),
                            })}
                        />
                        {errors[`${service}_api_key`] && (
                            <p className="text-sm text-red-500">
                                {typeof errors[`${service}_api_key`]?.message === 'string'
                                    ? String(errors[`${service}_api_key`]?.message)
                                    : "This field is required"}
                            </p>
                        )}
                    </div>
                )}
            </div>
        );
    };

    const renderField = (service: ServiceSegment, field: string, providerSchema: ProviderSchema) => {
        const schema = providerSchema.properties[field];
        const actualSchema = schema.$ref && providerSchema.$defs
            ? providerSchema.$defs[schema.$ref.split('/').pop() || '']
            : schema;

        // Use VoiceSelector for voice field in TTS service (except Sarvam which uses predefined options)
        if (service === "tts" && field === "voice") {
            const currentProvider = serviceProviders.tts;
            // Sarvam uses predefined voice options, not VoiceSelector
            const hasVoiceOptions = actualSchema?.enum || actualSchema?.examples;
            if (currentProvider !== "sarvam" && !hasVoiceOptions) {
                return (
                    <VoiceSelector
                        provider={currentProvider}
                        value={watch(`${service}_${field}`) as string || ""}
                        onChange={(voiceId) => {
                            setValue(`${service}_${field}`, voiceId, { shouldDirty: true });
                        }}
                    />
                );
            }
        }

        // Handle LLM model field with manual input toggle (uses examples from schema)
        if (service === "llm" && field === "model" && actualSchema?.examples) {
            const currentValue = watch(`${service}_${field}`) as string || "";
            const modelOptions = actualSchema.examples;

            if (isManualModelInput) {
                return (
                    <div className="space-y-2">
                        <Input
                            type="text"
                            placeholder="Enter model name"
                            value={currentValue}
                            onChange={(e) => {
                                setValue(`${service}_${field}`, e.target.value, { shouldDirty: true });
                            }}
                        />
                        <div className="flex items-center space-x-2">
                            <Checkbox
                                id="manual-model-input"
                                checked={isManualModelInput}
                                onCheckedChange={(checked) => {
                                    setIsManualModelInput(checked as boolean);
                                    if (!checked && modelOptions.length > 0) {
                                        // Reset to first option when switching back
                                        setValue(`${service}_${field}`, modelOptions[0], { shouldDirty: true });
                                    }
                                }}
                            />
                            <Label
                                htmlFor="manual-model-input"
                                className="text-sm font-normal cursor-pointer"
                            >
                                Add Model Manually
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
                            setValue(`${service}_${field}`, value, { shouldDirty: true });
                        }}
                    >
                        <SelectTrigger className="w-full">
                            <SelectValue placeholder="Select model" />
                        </SelectTrigger>
                        <SelectContent>
                            {modelOptions.map((value: string) => (
                                <SelectItem key={value} value={value}>
                                    {value}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <div className="flex items-center space-x-2">
                        <Checkbox
                            id="manual-model-input-dropdown"
                            checked={isManualModelInput}
                            onCheckedChange={(checked) => {
                                setIsManualModelInput(checked as boolean);
                            }}
                        />
                        <Label
                            htmlFor="manual-model-input-dropdown"
                            className="text-sm font-normal cursor-pointer"
                        >
                            Add Model Manually
                        </Label>
                    </div>
                </div>
            );
        }

        // Handle fields with enum or examples (dropdown options)
        const dropdownOptions = actualSchema?.enum || actualSchema?.examples;
        if (dropdownOptions && dropdownOptions.length > 0) {
            // Use friendly display names for language and voice fields
            const getDisplayName = (value: string) => {
                if (field === "language") {
                    return LANGUAGE_DISPLAY_NAMES[value] || value;
                }
                if (field === "voice") {
                    return VOICE_DISPLAY_NAMES[value] || value;
                }
                return value;
            };

            return (
                <Select
                    value={watch(`${service}_${field}`) as string || ""}
                    onValueChange={(value) => {
                        // Ignore empty string - Radix Select sometimes calls onValueChange('')
                        // when options change, even if current value is valid
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

        return (
            <Input
                type={actualSchema?.type === "number" ? "number" : "text"}
                {...(actualSchema?.type === "number" && { step: "any" })}
                placeholder={`Enter ${field}`}
                {...register(`${service}_${field}`, {
                    required: providerSchema.required?.includes(field),
                    valueAsNumber: actualSchema?.type === "number"
                })}
            />
        );
    };

    return (
        <div className="w-full max-w-2xl mx-auto">
            <div className="mb-6">
                <h1 className="text-3xl font-bold mb-2">AI Models Configuration</h1>
                <p className="text-muted-foreground">
                    Configure your AI model, voice, and transcription services.
                </p>
            </div>

            <form onSubmit={handleSubmit(onSubmit)}>
                <Card>
                    <CardContent className="pt-6">
                        <Tabs defaultValue="llm" className="w-full">
                            <TabsList className="grid w-full grid-cols-3 mb-6">
                                {TAB_CONFIG.map(({ key, label }) => (
                                    <TabsTrigger key={key} value={key}>
                                        {label}
                                    </TabsTrigger>
                                ))}
                            </TabsList>

                            {TAB_CONFIG.map(({ key }) => (
                                <TabsContent key={key} value={key} className="mt-0">
                                    {renderServiceFields(key)}
                                </TabsContent>
                            ))}
                        </Tabs>
                    </CardContent>
                </Card>

                {apiError && <p className="text-red-500 mt-4">{apiError}</p>}

                <Button type="submit" className="w-full mt-6" disabled={isSaving}>
                    {isSaving ? "Saving..." : "Save Configuration"}
                </Button>
            </form>
        </div>
    );
}
