import ModelConfigurationsManager from "@/components/model-connections/ModelConfigurationsManager";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

export default function ServiceConfigurationPage() {
    return (
        <ModelConfigurationsManager docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides} />
    );
}
