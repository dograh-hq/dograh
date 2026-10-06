import ModelConnectionsManager from "@/components/model-connections/ModelConnectionsManager";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

export default function ProviderConnectionsPage() {
    return (
        <div className="min-h-screen">
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-5xl mx-auto">
                    <ModelConnectionsManager view="providers" docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides} />
                </div>
            </div>
        </div>
    );
}
