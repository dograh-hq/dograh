import ProviderConnectionsManager from "@/components/model-connections/ProviderConnectionsManager";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

export default function ProviderConnectionsPage() {
    return (
        <ProviderConnectionsManager docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides} />
    );
}
