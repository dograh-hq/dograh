import { LLMConfigurationPage } from "@/components/model-connections/LLMConfigurationPage";

export default async function EditLLMConfigurationPage({ params }: { params: Promise<{ configurationUuid: string }> }) {
    const { configurationUuid } = await params;
    return <LLMConfigurationPage configurationUuid={configurationUuid} />;
}
