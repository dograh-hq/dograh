"use client";

import { Archive, ChevronRight, ExternalLink, Pencil, Plus, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { archiveNamedModelConfiguration, archiveProviderConnection, restoreNamedModelConfiguration, restoreProviderConnection, setDefaultModelConfiguration } from "@/client/sdk.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { useOrgConfig } from "@/context/OrgConfigContext";
import { detailFromError } from "@/lib/apiError";

import { configurationMode, connectionProviders } from "./configuration";
import type { NamedModelConfiguration, ProviderConnection } from "./types";
import { MODE_LABELS, ROLE_LABELS, ROLES } from "./types";
import { useModelConnections } from "./useModelConnections";

function ConfigurationSummary({ configuration, connections }: { configuration: NamedModelConfiguration; connections: ProviderConnection[] }) {
    const mode = configurationMode(configuration.configuration, connections);
    return <p className="text-xs text-muted-foreground">{MODE_LABELS[mode]} · {mode === "dograh"
        ? connections.find(item => item.uuid === configuration.configuration.llm.provider_connection_uuid)?.name || "Unavailable connection"
        : ROLES.filter(role => configuration.configuration[role]).map(role => `${ROLE_LABELS[role]}: ${configuration.configuration[role]?.settings.model || connections.find(item => item.uuid === configuration.configuration[role]?.provider_connection_uuid)?.name || "provider default"}`).join(" · ")}</p>;
}

export default function ModelConnectionsManager({ view, docsUrl }: { view: "providers" | "models"; docsUrl?: string }) {
    const state = useModelConnections({ includeArchived: true });
    const { catalog, connections, configurations, defaultUuid, loading, error, reload } = state;
    const { refreshConfig } = useOrgConfig();
    const [archive, setArchive] = useState<{ type: "connection" | "configuration"; uuid: string; name: string } | null>(null);
    const [busy, setBusy] = useState(false);
    const [actionError, setActionError] = useState<string | null>(null);
    const [archivedOpen, setArchivedOpen] = useState(false);
    const [restoringUuid, setRestoringUuid] = useState<string | null>(null);

    const refresh = async () => { await reload(); await refreshConfig(); };
    const activeConfigurations = configurations.filter(item => item.is_active);
    const activeConnections = connections.filter(item => item.is_active);
    const archivedItems = (view === "providers" ? connections : configurations).filter(item => !item.is_active);
    const providers = catalog ? connectionProviders(catalog) : {};

    const restore = async (uuid: string) => {
        setBusy(true); setRestoringUuid(uuid); setActionError(null);
        try {
            const response = view === "providers"
                ? await restoreProviderConnection({ path: { connection_uuid: uuid } })
                : await restoreNamedModelConfiguration({ path: { configuration_uuid: uuid } });
            if (response.error) throw new Error(detailFromError(response.error, "Unable to restore this item"));
            await refresh(); toast.success("Restored");
        } catch (cause) { setActionError(cause instanceof Error ? cause.message : "Unable to restore this item"); }
        finally { setBusy(false); setRestoringUuid(null); }
    };

    if (loading && !catalog) return <div className="space-y-6"><Skeleton className="h-12 w-72" /><Skeleton className="h-36 w-full" /><Skeleton className="h-80 w-full" /></div>;

    return <div className="space-y-7">
        <div>
            <h1 className="text-3xl font-bold">{view === "providers" ? "Providers" : "Models"}</h1>
            <p className="mt-2 text-sm text-muted-foreground">{view === "providers"
                ? <>Connect provider accounts, then use them in your <Link href="/model-configurations" className="underline">named model configurations</Link>.</>
                : <>Create named model configurations using your <Link href="/provider-connections" className="underline">provider connections</Link> in Dograh, Cascade, or Realtime mode.</>}
                {docsUrl && <> <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 underline">Learn more<ExternalLink className="h-3 w-3" /></a></>}
            </p>
        </div>
        {(error || actionError) && <div role="alert" className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 p-4 text-sm text-destructive">
            <span>{actionError || error}</span>{error && <Button type="button" size="sm" variant="outline" onClick={() => void reload()}>Retry</Button>}
        </div>}
        {catalog && <>
            {view === "providers" && <Card>
                <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-4">
                    <div className="space-y-1.5"><CardTitle>Provider Connections</CardTitle><CardDescription>Add multiple accounts from any provider, including Dograh.</CardDescription></div>
                    <Button asChild><Link href="/provider-connections/new"><Plus className="h-4 w-4" />Add Provider</Link></Button>
                </CardHeader>
                <CardContent className="space-y-4">
                    {activeConnections.length === 0 && <p className="rounded-md border border-dashed p-6 text-sm text-muted-foreground">No provider connections yet. Add a provider to get started.</p>}
                    {activeConnections.map(connection => {
                        const entry = providers[connection.provider];
                        return <div key={connection.uuid} className="flex flex-col justify-between gap-3 rounded-md border p-4 sm:flex-row sm:items-center">
                            <div className="min-w-0 space-y-1"><h3 className="truncate font-medium">{connection.name}</h3>
                                <p className="text-xs text-muted-foreground">{entry?.title || connection.provider}</p>
                            </div>
                            <div className="flex flex-wrap gap-1">
                                <Button asChild variant="ghost" size="sm"><Link href={`/provider-connections/${connection.uuid}`}><Pencil className="h-3.5 w-3.5" />Edit</Link></Button>
                                <Button type="button" variant="ghost" size="sm" onClick={() => { setActionError(null); setArchive({ type: "connection", uuid: connection.uuid, name: connection.name }); }}>Archive</Button>
                            </div>
                        </div>;
                    })}
                </CardContent>
            </Card>}
            {view === "models" && <Card>
                <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-4">
                    <div className="space-y-1.5"><CardTitle>Named Model Configurations</CardTitle><CardDescription>Choose a setup for each workflow or make one your org default. Workflows inherit the default unless overridden.</CardDescription></div>
                    <Button asChild><Link href="/model-configurations/new"><Plus className="h-4 w-4" />Add Configuration</Link></Button>
                </CardHeader>
                <CardContent className="space-y-3">
                    {activeConfigurations.length === 0 && <p className="rounded-md border border-dashed p-6 text-sm text-muted-foreground"><Link href="/provider-connections" className="underline">Add provider connections</Link>, then save your first named model configuration.</p>}
                    {activeConfigurations.map(configuration => {
                        return <div key={configuration.uuid} className="flex flex-col justify-between gap-3 rounded-md border p-4 sm:flex-row sm:items-center">
                            <div className="min-w-0 space-y-1"><h3 className="font-medium">{configuration.name}{defaultUuid === configuration.uuid && <span className="ml-2 rounded bg-muted px-2 py-0.5 text-xs font-normal">Org default</span>}</h3>
                                <ConfigurationSummary configuration={configuration} connections={connections} />
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-1">
                                {defaultUuid !== configuration.uuid && <Button type="button" variant="outline" size="sm" disabled={busy || loading} onClick={async () => {
                                    setBusy(true); setActionError(null);
                                    try {
                                        const response = await setDefaultModelConfiguration({ body: { model_configuration_uuid: configuration.uuid } });
                                        if (response.error) throw new Error(detailFromError(response.error, "Failed to set organization default"));
                                        await refresh(); toast.success("Organization default updated");
                                    } catch (cause) { setActionError(cause instanceof Error ? cause.message : "Failed to set organization default"); }
                                    finally { setBusy(false); }
                                }}>Make org default</Button>}
                                <Button asChild variant="ghost" size="sm"><Link href={`/model-configurations/${configuration.uuid}`}>Edit</Link></Button>
                                <Button asChild variant="ghost" size="sm"><Link href={`/model-configurations/new?duplicate=${configuration.uuid}`}>Duplicate</Link></Button>
                                <Button type="button" variant="ghost" size="sm" disabled={defaultUuid === configuration.uuid} onClick={() => { setActionError(null); setArchive({ type: "configuration", uuid: configuration.uuid, name: configuration.name }); }}>Archive</Button>
                            </div>
                        </div>;
                    })}
                </CardContent>
            </Card>}
            {archivedItems.length > 0 && <Collapsible open={archivedOpen} onOpenChange={setArchivedOpen}>
                <CollapsibleTrigger asChild>
                    <button type="button" aria-label="Toggle Archived" className="group flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left transition-colors hover:bg-accent">
                        <ChevronRight size={16} className={`shrink-0 text-muted-foreground transition-transform duration-200 ${archivedOpen ? "rotate-90" : ""}`} />
                        <Archive size={16} className="shrink-0 text-muted-foreground" />
                        <span className="font-medium text-muted-foreground">Archived</span>
                        <Badge variant="secondary" className="ml-1 font-normal">{archivedItems.length}</Badge>
                    </button>
                </CollapsibleTrigger>
                <CollapsibleContent className="space-y-3 pt-3">
                    {archivedItems.map(item => {
                        const needsProviders = "configuration" in item && [
                            ...ROLES.map(role => item.configuration[role]),
                            ...(item.configuration.llm_fallback?.rules || []).map(rule => rule.target),
                        ].some(selection => {
                            return selection && !activeConnections.some(connection => connection.uuid === selection.provider_connection_uuid);
                        });
                        return <div key={item.uuid} className="flex flex-col justify-between gap-3 rounded-md border p-4 sm:flex-row sm:items-center">
                            <div className="min-w-0 space-y-1">
                                <div className="opacity-60">
                                    <h3 className="font-medium">{item.name}</h3>
                                    {"configuration" in item ? <ConfigurationSummary configuration={item} connections={connections} />
                                        : <p className="text-xs text-muted-foreground">{providers[item.provider]?.title || item.provider}</p>}
                                </div>
                                {needsProviders && <p className="text-xs text-muted-foreground">Restore this configuration’s <Link href="/provider-connections" className="underline">provider connections</Link> first.</p>}
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-1">
                                <Button type="button" variant="outline" size="sm" disabled={busy || loading || needsProviders} onClick={() => void restore(item.uuid)}>
                                    <RotateCcw className="h-3.5 w-3.5" />{restoringUuid === item.uuid ? "Restoring…" : "Restore"}
                                </Button>
                            </div>
                        </div>;
                    })}
                </CollapsibleContent>
            </Collapsible>}
            <Dialog open={archive !== null} onOpenChange={open => { if (!open && !busy) setArchive(null); }}>
                <DialogContent onOpenAutoFocus={() => undefined}>
                    <DialogHeader><DialogTitle>Archive {archive?.name}?</DialogTitle><DialogDescription>Archived items cannot be selected for future runs. Items still referenced by active configurations or workflows must be unlinked first.</DialogDescription></DialogHeader>
                    {actionError && <p role="alert" className="text-sm text-destructive">{actionError}</p>}
                    <div className="flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={() => setArchive(null)}>Cancel</Button>
                        <Button disabled={busy} onClick={async () => {
                            if (!archive) return;
                            setBusy(true); setActionError(null);
                            try {
                                const result = archive.type === "connection" ? await archiveProviderConnection({ path: { connection_uuid: archive.uuid } }) : await archiveNamedModelConfiguration({ path: { configuration_uuid: archive.uuid } });
                                if (result.error) throw new Error(detailFromError(result.error, "Unable to archive this item"));
                                setArchive(null); await refresh(); toast.success("Archived");
                            } catch (cause) { setActionError(cause instanceof Error ? cause.message : "Unable to archive this item"); }
                            finally { setBusy(false); }
                        }}>{busy ? "Archiving…" : "Archive"}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </>}
    </div>;
}
