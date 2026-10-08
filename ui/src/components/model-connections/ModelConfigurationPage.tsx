"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { PageShell } from "@/components/layout/PageShell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrgConfig } from "@/context/OrgConfigContext";

import { NamedConfigurationEditor } from "./NamedConfigurationEditor";
import { useModelConnections } from "./useModelConnections";

export function ModelConfigurationPage({ configurationUuid, duplicateUuid }: { configurationUuid?: string; duplicateUuid?: string }) {
    const { catalog, connections, configurations, loading, error, reload } = useModelConnections();
    const { refreshConfig } = useOrgConfig();
    const router = useRouter();
    const sourceUuid = configurationUuid || duplicateUuid;
    const saved = configurations.find(item => item.uuid === sourceUuid && item.is_active);

    return <PageShell className="space-y-6">
        <Button asChild variant="ghost" className="-ml-3"><Link href="/model-configurations"><ArrowLeft className="h-4 w-4" />All model configurations</Link></Button>
        {loading && !catalog ? <Skeleton className="h-80 w-full" /> : error ? <div role="alert" className="space-y-3 text-sm text-destructive">
            <p>{error}</p><Button variant="outline" onClick={() => void reload()}>Retry</Button>
        </div> : sourceUuid && !saved ? <p role="alert">This model configuration is unavailable.</p>
            : catalog && <div className="rounded-lg border bg-card p-6">
                <NamedConfigurationEditor key={`${sourceUuid || "new"}-${saved?.revision || 0}`} catalog={catalog} connections={connections} saved={saved} duplicate={Boolean(duplicateUuid)}
                    onSaved={async (uuid, editFallbacks) => {
                        await reload();
                        await refreshConfig();
                        toast.success("Model configuration saved");
                        if (editFallbacks) router.push(`/model-configurations/${uuid}/llm`);
                        else if (!configurationUuid) router.replace(`/model-configurations/${uuid}`);
                    }} />
            </div>}
    </PageShell>;
}
