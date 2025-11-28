"use client";

import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";

import { getDefaultConfigurationsApiV1UserConfigurationsDefaultsGet } from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useUserConfig } from "@/context/UserConfigContext";

type ServiceSegment = "llm" | "tts" | "stt";

interface SchemaProperty {
    type?: string;
    default?: string | number | boolean;
    enum?: string[];
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

            setServiceProviders(selectedProviders);

            reset(defaultValues);
        };
        fetchConfigurations();
    }, [reset, userConfig]);

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
                            type="password"
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

        if (actualSchema?.enum) {
            return (
                <Select
                    value={watch(`${service}_${field}`) as string || ""}
                    onValueChange={(value) => {
                        setValue(`${service}_${field}`, value, { shouldDirty: true });
                    }}
                >
                    <SelectTrigger className="w-full">
                        <SelectValue placeholder={`Select ${field}`} />
                    </SelectTrigger>
                    <SelectContent>
                        {actualSchema.enum.map((value: string) => (
                            <SelectItem key={value} value={value}>
                                {value}
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
