
import ServiceConfiguration from "@/components/ServiceConfiguration";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

export default function ServiceConfigurationPage() {
    return (
        <div className="min-h-screen bg-background">
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-4xl mx-auto">
                    <ServiceConfiguration docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides} />
                </div>
            </div>
        </div>
    );
}
