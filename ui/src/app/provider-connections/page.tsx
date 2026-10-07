import { PageShell } from "@/components/layout/PageShell";
import ModelConnectionsManager from "@/components/model-connections/ModelConnectionsManager";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

export default function ProviderConnectionsPage() {
    return (
        <PageShell>
            <ModelConnectionsManager view="providers" docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides} />
        </PageShell>
    );
}
