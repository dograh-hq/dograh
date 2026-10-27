import { ModelConfigurationPage } from "@/components/model-connections/ModelConfigurationPage";

export default async function EditModelConfigurationPage({ params }: { params: Promise<{ configurationUuid: string }> }) {
    const { configurationUuid } = await params;
    return <ModelConfigurationPage configurationUuid={configurationUuid} />;
}
