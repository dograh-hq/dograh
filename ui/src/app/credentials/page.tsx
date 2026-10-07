"use client";

import { Loader2, Pencil, Plus, Search, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { deleteCredentialApiV1CredentialsCredentialUuidDelete, listCredentialsApiV1CredentialsGet } from "@/client";
import type { CredentialResponse, WebhookCredentialType } from "@/client/types.gen";
import { CreateCredentialDialog } from "@/components/http/create-credential-dialog";
import { PageLayout, PageSection } from "@/components/layout/PageLayout";
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

const TYPE_LABELS: Record<WebhookCredentialType, string> = {
    none: "No Authentication",
    api_key: "API Key",
    bearer_token: "Bearer Token",
    basic_auth: "Basic Auth",
    custom_header: "Custom Header",
};

const typeLabel = (type: string) => TYPE_LABELS[type as WebhookCredentialType] ?? type;

export default function CredentialsPage() {
    const { user, loading: authLoading, getAccessToken, redirectToLogin } = useAuth();
    const [credentials, setCredentials] = useState<CredentialResponse[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [reload, setReload] = useState(0);
    const [search, setSearch] = useState("");
    const [createOpen, setCreateOpen] = useState(false);
    const [editTarget, setEditTarget] = useState<CredentialResponse | null>(null);
    const [deleteTarget, setDeleteTarget] = useState<CredentialResponse | null>(null);
    const [deleting, setDeleting] = useState(false);
    const [deleteError, setDeleteError] = useState<string | null>(null);

    useEffect(() => {
        if (!authLoading && !user) redirectToLogin();
    }, [authLoading, user, redirectToLogin]);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        const fetchCredentials = async () => {
            setLoading(true);
            setError(null);
            try {
                const token = await getAccessToken();
                const response = await listCredentialsApiV1CredentialsGet({
                    headers: { Authorization: `Bearer ${token}` },
                });
                if (response.error) throw new Error(detailFromError(response.error, "Failed to load credentials"));
                if (!cancelled) setCredentials(response.data ?? []);
            } catch (cause) {
                if (!cancelled) setError(cause instanceof Error ? cause.message : "Failed to load credentials");
            } finally {
                if (!cancelled) setLoading(false);
            }
        };
        void fetchCredentials();
        return () => { cancelled = true; };
    }, [authLoading, user, getAccessToken, reload]);

    const deleteCredential = async () => {
        if (!deleteTarget || deleting) return;
        setDeleting(true);
        setDeleteError(null);
        try {
            const token = await getAccessToken();
            const response = await deleteCredentialApiV1CredentialsCredentialUuidDelete({
                headers: { Authorization: `Bearer ${token}` },
                path: { credential_uuid: deleteTarget.uuid },
            });
            if (response.error) throw new Error(detailFromError(response.error, "Failed to delete credential"));
            setCredentials((current) => current.filter((item) => item.uuid !== deleteTarget.uuid));
            setDeleteTarget(null);
            toast.success("Credential deleted");
        } catch (cause) {
            setDeleteError(cause instanceof Error ? cause.message : "Failed to delete credential");
        } finally {
            setDeleting(false);
        }
    };

    const query = search.trim().toLowerCase();
    const filtered = credentials.filter((item) =>
        [item.name, item.description, typeLabel(item.credential_type)].some((value) => value?.toLowerCase().includes(query))
    );

    return (
        <>
            <PageLayout title="Credentials" description="Manage reusable authentication for your tools and webhooks.">
                <PageSection title="Your Credentials"
                    description="Save external credentials once and use them across your organization in tools and webhooks."
                    actions={<Button onClick={() => setCreateOpen(true)} disabled={authLoading || !user || loading || !!error}>
                        <Plus className="h-4 w-4" />Add Credential
                    </Button>}
                    toolbar={<div className="relative">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                        <Input aria-label="Search credentials" placeholder="Search credentials..." value={search} onChange={(event) => setSearch(event.target.value)} className="pl-10" />
                    </div>}>

                    {authLoading || !user || loading ? (
                        <div className="space-y-4" role="status" aria-label="Loading credentials">
                            {[1, 2, 3].map((item) => <Skeleton key={item} className="h-24 w-full rounded-lg" />)}
                        </div>
                    ) : error ? (
                        <div role="alert" className="rounded-lg border border-destructive/20 bg-destructive/10 p-4 text-sm text-destructive">
                            <p>{error}</p>
                            <Button variant="outline" size="sm" className="mt-3" onClick={() => setReload((value) => value + 1)}>Retry</Button>
                        </div>
                    ) : filtered.length === 0 ? (
                        <div className="py-12 text-center">
                            <ShieldCheck className="mx-auto mb-4 h-12 w-12 text-muted-foreground" />
                            <p className="mb-4 text-muted-foreground">{query ? "No credentials match your search" : "No credentials yet"}</p>
                            {query ? (
                                <Button variant="outline" onClick={() => setSearch("")}>Clear Search</Button>
                            ) : (
                                <Button onClick={() => setCreateOpen(true)}>Add Your First Credential</Button>
                            )}
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {filtered.map((item) => (
                                <div key={item.uuid} role="button" tabIndex={0} aria-label={`Edit credential ${item.name}`}
                                    className="flex cursor-pointer flex-col justify-between gap-3 rounded-lg border p-4 transition-colors hover:bg-muted/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring sm:flex-row sm:items-center"
                                    onClick={() => setEditTarget(item)}
                                    onKeyDown={event => {
                                        if (event.target !== event.currentTarget) return;
                                        if (event.key === "Enter" || event.key === " ") {
                                            event.preventDefault();
                                            setEditTarget(item);
                                        }
                                    }}>
                                    <div className="min-w-0 space-y-1">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h3 className="break-words font-medium">{item.name}</h3>
                                            <Badge variant="secondary">{typeLabel(item.credential_type)}</Badge>
                                        </div>
                                        {item.description && <p className="break-words text-sm text-muted-foreground">{item.description}</p>}
                                        <p className="text-xs text-muted-foreground">Created {new Date(item.created_at).toLocaleDateString()}</p>
                                    </div>
                                    <div className="flex shrink-0 items-center gap-1 self-end sm:self-auto" onClick={event => event.stopPropagation()}>
                                        <Button variant="ghost" size="sm" aria-label={`Edit ${item.name}`} onClick={() => setEditTarget(item)}>
                                            <Pencil className="h-4 w-4" />Edit
                                        </Button>
                                        <Button variant="ghost" size="icon" aria-label={`Delete ${item.name}`} className="text-destructive hover:text-destructive" onClick={() => { setDeleteError(null); setDeleteTarget(item); }}>
                                            <Trash2 className="h-4 w-4" />
                                        </Button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </PageSection>
            </PageLayout>
            <CreateCredentialDialog open={createOpen} onOpenChange={setCreateOpen} onCreated={(credential) => {
                setCredentials((current) => [credential, ...current]);
                setSearch("");
                toast.success("Credential created");
            }} />
            <CreateCredentialDialog open={!!editTarget} credential={editTarget ?? undefined} onOpenChange={(open) => { if (!open) setEditTarget(null); }} onUpdated={(credential) => {
                setCredentials((current) => current.map((item) => item.uuid === credential.uuid ? credential : item));
                toast.success("Credential updated");
            }} />
            <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open && !deleting) setDeleteTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Delete credential?</AlertDialogTitle>
                        <AlertDialogDescription>
                            Delete &quot;{deleteTarget?.name}&quot;? Tools and webhooks using this credential will need another credential to authenticate.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    {deleteError && <p role="alert" className="text-sm text-destructive">{deleteError}</p>}
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
                        <Button variant="destructive" disabled={deleting} onClick={deleteCredential}>
                            {deleting && <Loader2 className="h-4 w-4 animate-spin" />}
                            {deleting ? "Deleting..." : "Delete"}
                        </Button>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </>
    );
}
