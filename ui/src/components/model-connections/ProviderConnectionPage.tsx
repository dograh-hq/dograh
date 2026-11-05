"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { PageShell } from "@/components/layout/PageShell";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrgConfig } from "@/context/OrgConfigContext";

import { ConnectionEditor } from "./ConnectionEditor";
import { useModelConnections } from "./useModelConnections";

export function ProviderConnectionPage({ connectionUuid }: { connectionUuid?: string }) {
    const { catalog, connections, loading, error, reload } = useModelConnections();
    const { refreshConfig } = useOrgConfig();
    const router = useRouter();
    const connection = connections.find(item => item.uuid === connectionUuid && item.is_active);

    return <PageShell className="space-y-6">
        <Button asChild variant="ghost" className="-ml-3"><Link href="/provider-connections"><ArrowLeft className="h-4 w-4" />All provider connections</Link></Button>
        {loading && !catalog ? <Skeleton className="h-80 w-full" /> : error ? <div role="alert" className="space-y-3 text-sm text-destructive">
            <p>{error}</p><Button variant="outline" onClick={() => void reload()}>Retry</Button>
        </div> : connectionUuid && !connection ? <p role="alert">This provider connection is unavailable.</p>
            : catalog && <div className="rounded-lg border bg-card p-6">
                <ConnectionEditor key={`${connectionUuid || "new"}-${connection?.revision || 0}`} catalog={catalog} connection={connection}
                    onSaved={async uuid => {
                        await reload();
                        await refreshConfig();
                        toast.success("Provider connection saved");
                        if (!connectionUuid) router.replace(`/provider-connections/${uuid}`);
                    }} />
            </div>}
    </PageShell>;
}
