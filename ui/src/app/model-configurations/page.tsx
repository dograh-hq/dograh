
import ModelConfigurationV2 from "@/components/ModelConfigurationV2";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

export default function ServiceConfigurationPage() {
    return (
        <div className="min-h-screen">
            <div className="container mx-auto px-4 py-8">
                <div className="max-w-4xl mx-auto">
                    <ModelConfigurationV2 docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides} />
                </div>
            </div>
        </div>
    );
}
