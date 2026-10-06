import { ModelConfigurationPage } from "@/components/model-connections/ModelConfigurationPage";

export default async function NewModelConfigurationPage({ searchParams }: { searchParams: Promise<{ duplicate?: string }> }) {
    const { duplicate } = await searchParams;
    return <ModelConfigurationPage duplicateUuid={duplicate} />;
}
