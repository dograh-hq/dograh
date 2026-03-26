"use client";

import { Plus, X } from "lucide-react";
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
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";
import { useUserConfig } from "@/context/UserConfigContext";

type ServiceSegment = "llm" | "tts" | "stt" | "embeddings";

interface SchemaProperty {
    type?: string;
    default?: string | number | boolean;
    enum?: string[];
    examples?: string[];
    model_options?: Record<string, string[]>;
    allow_custom_input?: boolean;
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
    { key: "embeddings", label: "Embedding" },
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

export default function ServiceConfiguration() {
    const [apiError, setApiError] = useState<string | null>(null);
    const [isSaving, setIsSaving] = useState(false);
    const { userConfig, saveUserConfig } = useUserConfig();
    const [schemas, setSchemas] = useState<Record<ServiceSegment, Record<string, ProviderSchema>>>({
        llm: {},
        tts: {},
        stt: {},
        embeddings: {}
    });
    const [serviceProviders, setServiceProviders] = useState<Record<ServiceSegment, string>>({
        llm: "",
        tts: "",
        stt: "",
        embeddings: ""
    });
    const [apiKeys, setApiKeys] = useState<Record<ServiceSegment, string[]>>({
        llm: [""],
        tts: [""],
        stt: [""],
        embeddings: [""],
    });
    const [isCustomInput, setIsCustomInput] = useState<Record<string, boolean>>({});

    const {
        register,
        handleSubmit,
        formState: { },
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
                    stt: response.data.stt as Record<string, ProviderSchema>,
                    embeddings: response.data.embeddings as Record<string, ProviderSchema>
                });
            } else {
                console.error("Failed to fetch configurations");
                return;
            }

            const defaultValues: Record<string, string | number | boolean> = {};
            const selectedProviders: Record<ServiceSegment, string> = {
                llm: response.data.default_providers.llm,
                tts: response.data.default_providers.tts,
                stt: response.data.default_providers.stt,
                embeddings: response.data.default_providers.embeddings
            };

            const loadedApiKeys: Record<ServiceSegment, string[]> = {
                llm: [""],
                tts: [""],
                stt: [""],
                embeddings: [""],
            };

            const setServicePropertyValues = (service: ServiceSegment) => {
                if (userConfig?.[service]?.provider) {
                    Object.entries(userConfig?.[service]).forEach(([field, value]) => {
                        if (field === "api_key") {
                            // Handle api_key separately — it can be string or string[]
                            if (Array.isArray(value)) {
                                loadedApiKeys[service] = value.length > 0 ? value : [""];
                            } else {
                                loadedApiKeys[service] = value ? [value as string] : [""];
                            }
                        } else if (field !== "provider") {
                            defaultValues[`${service}_${field}`] = value as string | number | boolean;
                        }
                    });
                    selectedProviders[service] = userConfig?.[service]?.provider as string;
                    // Fill in schema defaults for fields not present in userConfig
                    const properties = response.data[service]?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            const key = `${service}_${field}`;
                            if (field !== "provider" && field !== "api_key" && schema.default !== undefined && !(key in defaultValues)) {
                                defaultValues[key] = schema.default;
                            }
                        });
                    }
                } else {
                    const properties = response.data[service]?.[selectedProviders[service]]?.properties as Record<string, SchemaProperty>;
                    if (properties) {
                        Object.entries(properties).forEach(([field, schema]) => {
                            if (field !== "provider" && schema.default !== undefined) {
                                defaultValues[`${service}_${field}`] = schema.default;
                            }
                        });
                    }
                }
            }

            setServicePropertyValues("llm");
            setServicePropertyValues("tts");
            setServicePropertyValues("stt");
            setServicePropertyValues("embeddings");

            // Detect saved values that are not in suggested options (custom value)
            const detectedCustomInput: Record<string, boolean> = {};
            const allSchemas = response.data as Record<string, Record<string, ProviderSchema>>;
            (["llm", "tts", "stt", "embeddings"] as ServiceSegment[]).forEach(service => {
                const provider = selectedProviders[service];
                const providerSchema = allSchemas[service]?.[provider];
                if (!providerSchema) return;

                Object.entries(providerSchema.properties).forEach(([field, schema]) => {
                    const actualSchema = (schema as SchemaProperty).$ref && providerSchema.$defs
                        ? providerSchema.$defs[(schema as SchemaProperty).$ref!.split('/').pop() || '']
                        : schema as SchemaProperty;

                    if (!actualSchema?.allow_custom_input || !actualSchema?.examples) return;

                    const savedValue = userConfig?.[service]?.[field] as string | undefined;
                    if (savedValue && !actualSchema.examples.includes(savedValue)) {
                        detectedCustomInput[`${service}_${field}`] = true;
                    }
                });
            });

            // IMPORTANT: Reset form values BEFORE changing providers
            // Otherwise, Radix Select sees old values that don't match new provider's enum
            // and calls onValueChange('') to clear "invalid" values
            reset(defaultValues);
            setApiKeys(loadedApiKeys);
            setServiceProviders(selectedProviders);
            setIsCustomInput(detectedCustomInput);
        };
        fetchConfigurations();
    }, [reset, userConfig]);

    // Reset voice when TTS model changes if the provider has model-dependent voice options
    const ttsModel = watch("tts_model");
    useEffect(() => {
        const voiceSchema = schemas?.tts?.[serviceProviders.tts]?.properties?.voice;
        const modelOptions = voiceSchema?.model_options;
        if (!modelOptions || !ttsModel) return;

        const validVoices = modelOptions[ttsModel as string];
        const currentVoice = getValues("tts_voice") as string;
        if (validVoices && currentVoice && !validVoices.includes(currentVoice)) {
            setValue("tts_voice", validVoices[0], { shouldDirty: true });
        }
    }, [ttsModel, serviceProviders.tts, setValue, getValues, schemas]);

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
        setApiKeys(prev => ({ ...prev, [service]: [""] }));

        // Reset custom input toggles when provider changes
        setIsCustomInput(prev => {
            const next = { ...prev };
            Object.keys(next).forEach(key => {
                if (key.startsWith(`${service}_`)) delete next[key];
            });
            return next;
        });
    }


    const onSubmit = async (data: FormValues) => {
        setApiError(null);
        setIsSaving(true);

        // Collect non-empty API keys per service
        const getServiceApiKeys = (service: ServiceSegment): string[] =>
            apiKeys[service].map(k => k.trim()).filter(k => k.length > 0);

        const userConfig: Record<ServiceSegment, Record<string, string | number | string[]>> = {
            llm: {
                provider: serviceProviders.llm,
                ...(getServiceApiKeys("llm").length > 0 && { api_key: getServiceApiKeys("llm") }),
                model: data.llm_model as string
            },
            tts: {
                provider: serviceProviders.tts,
                ...(getServiceApiKeys("tts").length > 0 && { api_key: getServiceApiKeys("tts") }),
            },
            stt: {
                provider: serviceProviders.stt,
                ...(getServiceApiKeys("stt").length > 0 && { api_key: getServiceApiKeys("stt") }),
            },
            embeddings: {
                provider: serviceProviders.embeddings,
                ...(getServiceApiKeys("embeddings").length > 0 && { api_key: getServiceApiKeys("embeddings") }),
                model: data.embeddings_model as string
            }
        };

        // Add any extra properties in the payload
        Object.entries(data).forEach(([property, value]) => {
            const parts = property.split('_');
            const service = parts[0] as ServiceSegment;
            const field = parts.slice(1).join('_');

            if (field === "api_key") return; // handled via apiKeys state
            if (userConfig[service] && !(field in userConfig[service])) {
                (userConfig[service] as Record<string, string>)[field] = value as string;
            }
        });

        // Build save config - only include embeddings if api_key is provided
        const saveConfig: {
            llm: Record<string, string | number | string[]>;
            tts: Record<string, string | number | string[]>;
            stt: Record<string, string | number | string[]>;
            embeddings?: Record<string, string | number | string[]>;
        } = {
            llm: userConfig.llm,
            tts: userConfig.tts,
            stt: userConfig.stt
        };

        // Only include embeddings if user has configured it (has api_key)
        const embeddingsKeys = getServiceApiKeys("embeddings");
        if (embeddingsKeys.length > 0) {
            saveConfig.embeddings = userConfig.embeddings;
        }

        try {
            await saveUserConfig(saveConfig);
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
        const fields = Object.keys(providerSchema.properties).filter(
            field => field !== "provider" && field !== "api_key"
        );

        return fields;
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

                {/* API Key(s) */}
                {currentProvider && providerSchema && providerSchema.properties.api_key && (
                    <div className="space-y-2">
                        <Label>API Key(s)</Label>
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

        // VoiceSelector for TTS voice fields without predefined options or manual input flag
        if (service === "tts" && field === "voice" && !actualSchema?.allow_custom_input) {
            const hasVoiceOptions = actualSchema?.enum || actualSchema?.examples;
            if (!hasVoiceOptions) {
                return (
                    <VoiceSelector
                        provider={serviceProviders.tts}
                        value={watch(`${service}_${field}`) as string || ""}
                        onChange={(voiceId) => {
                            setValue(`${service}_${field}`, voiceId, { shouldDirty: true });
                        }}
                    />
                );
            }
        }

        // Generic allow_custom_input handler for any field (model, voice with options, etc.)
        if (actualSchema?.allow_custom_input && actualSchema?.examples) {
            const fieldKey = `${service}_${field}`;
            const currentValue = watch(fieldKey) as string || "";
            const options = actualSchema.examples;

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

        // Handle fields with enum or examples (dropdown options)
        let dropdownOptions = actualSchema?.enum || actualSchema?.examples;

        // Use model-dependent options when available (e.g., Sarvam voices per model)
        if (actualSchema?.model_options) {
            const modelValue = watch(`${service}_model`) as string;
            if (modelValue && actualSchema.model_options[modelValue]) {
                dropdownOptions = actualSchema.model_options[modelValue];
            }
        }

        if (dropdownOptions && dropdownOptions.length > 0) {
            // Use friendly display names for language and voice fields
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
                    // Embeddings is optional, so don't require its fields
                    required: service !== "embeddings" && providerSchema.required?.includes(field),
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
                            <TabsList className="grid w-full grid-cols-4 mb-6">
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
