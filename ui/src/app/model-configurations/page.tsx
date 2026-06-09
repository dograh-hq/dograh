
import ModelConfigurationV2 from "@/components/ModelConfigurationV2";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

interface ServiceConfigurationPageProps {
    searchParams?: Promise<{
        action?: string | string[];
    }>;
}

export default async function ServiceConfigurationPage({ searchParams }: ServiceConfigurationPageProps) {
    const params = searchParams ? await searchParams : {};
    const action = Array.isArray(params.action) ? params.action[0] : params.action;

    return (
        <div className="min-h-screen bg-background">
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-4xl mx-auto">
                    <ModelConfigurationV2
                        docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides}
                        initialAction={action}
                    />
                </div>
            </div>
        </div>
    );
}
