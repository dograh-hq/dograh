import { ModelConfigurationPage } from "@/components/model-connections/ModelConfigurationPage";

export default async function NewModelConfigurationPage({ searchParams }: { searchParams: Promise<{ duplicate?: string | string[] }> }) {
    const { duplicate: value } = await searchParams;
    const duplicate = Array.isArray(value) ? value[0] : value;
    return <ModelConfigurationPage duplicateUuid={duplicate} />;
}
