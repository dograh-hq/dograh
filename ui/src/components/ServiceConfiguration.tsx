"use client";

import { ServiceConfigurationForm } from "@/components/ServiceConfigurationForm";
import { useUserConfig } from "@/context/UserConfigContext";

export default function ServiceConfiguration() {
    const { saveUserConfig } = useUserConfig();

    return (
        <div className="w-full max-w-2xl mx-auto">
            <div className="mb-6">
                <h1 className="text-3xl font-bold mb-2">AI Models Configuration</h1>
                <p className="text-muted-foreground">
                    Configure your AI model, voice, and transcription services.
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
