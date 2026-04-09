"use client";

import { ExternalLink } from "lucide-react";

import { ServiceConfigurationForm } from "@/components/ServiceConfigurationForm";
import { useUserConfig } from "@/context/UserConfigContext";

interface ServiceConfigurationProps {
    docsUrl?: string;
}

export default function ServiceConfiguration({ docsUrl }: ServiceConfigurationProps) {
    const { saveUserConfig } = useUserConfig();

    return (
        <div className="w-full max-w-2xl mx-auto">
            <div className="mb-6">
                <h1 className="text-3xl font-bold mb-2">AI Models Configuration</h1>
                <p className="text-muted-foreground">
                    Configure your AI model, voice, and transcription services.{" "}
                    {docsUrl && (
                        <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                            Learn more <ExternalLink className="h-3 w-3" />
                        </a>
                    )}
                </p>
            </div>

            <ServiceConfigurationForm
                mode="global"
                onSave={async (config) => {
                    await saveUserConfig(config);
                }}
            />
        </div>
    );
}
