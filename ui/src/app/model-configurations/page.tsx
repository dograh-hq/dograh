import { PageShell } from "@/components/layout/PageShell";
import ModelConnectionsManager from "@/components/model-connections/ModelConnectionsManager";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

export default function ServiceConfigurationPage() {
    return (
        <PageShell>
            <ModelConnectionsManager view="models" docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides} />
        </PageShell>
    );
}
