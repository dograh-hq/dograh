import { ProviderConnectionPage } from "@/components/model-connections/ProviderConnectionPage";

export default async function EditProviderConnectionPage({ params }: { params: Promise<{ connectionUuid: string }> }) {
    const { connectionUuid } = await params;
    return <ProviderConnectionPage connectionUuid={connectionUuid} />;
}
