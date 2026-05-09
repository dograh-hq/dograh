"use client";

import {
  AlertTriangle,
  ChevronRight,
  Copy,
  ExternalLink,
  Pencil,
  Plus,
  Star,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  deleteTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdDelete,
  getTelephonyConfigurationByIdApiV1OrganizationsTelephonyConfigsConfigIdGet,
  listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
  setDefaultOutboundApiV1OrganizationsTelephonyConfigsConfigIdSetDefaultOutboundPost,
} from "@/client/sdk.gen";
import type {
  TelephonyConfigurationDetail,
  TelephonyConfigurationListItem,
} from "@/client/types.gen";
import { ConfigFormDialog } from "@/components/telephony/ConfigFormDialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useTelephonyConfigWarnings } from "@/context/TelephonyConfigWarningsContext";
import { useAuth } from "@/lib/auth";

export default function TelephonyConfigurationsPage() {
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const {
    telnyxMissingWebhookPublicKeyCount,
    refresh: refreshWarnings,
  } = useTelephonyConfigWarnings();
  const [items, setItems] = useState<TelephonyConfigurationListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<TelephonyConfigurationDetail | null>(
    null,
  );
  const [editOpen, setEditOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] =
    useState<TelephonyConfigurationListItem | null>(null);

  const fetchItems = useCallback(async () => {
    if (authLoading || !user) return;
    setLoading(true);
    try {
      const token = await getAccessToken();
      const res = await listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet(
        { headers: { Authorization: `Bearer ${token}` } },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      setItems(res.data?.configurations ?? []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load configurations");
    } finally {
      setLoading(false);
    }
  }, [authLoading, user, getAccessToken]);

  // After a save (create/update), the backing config may have flipped between
  // missing/present webhook_public_key — refresh the cached warning state so
  // the page banner and nav badge update without a manual reload.
  const onSaved = useCallback(async () => {
    await fetchItems();
    await refreshWarnings();
  }, [fetchItems, refreshWarnings]);

  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  const onEdit = async (item: TelephonyConfigurationListItem) => {
    try {
      const token = await getAccessToken();
      const res = await getTelephonyConfigurationByIdApiV1OrganizationsTelephonyConfigsConfigIdGet(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: item.id },
        },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      setEditTarget(res.data ?? null);
      setEditOpen(true);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load configuration");
    }
  };

  const onSetDefault = async (item: TelephonyConfigurationListItem) => {
    try {
      const token = await getAccessToken();
      const res = await setDefaultOutboundApiV1OrganizationsTelephonyConfigsConfigIdSetDefaultOutboundPost(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: item.id },
        },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success(`${item.name} is now the default outbound configuration`);
      fetchItems();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to set default");
    }
  };

  const onConfirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      const token = await getAccessToken();
      const res = await deleteTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdDelete(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: deleteTarget.id },
        },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success("Configuration deleted");
      setDeleteTarget(null);
      fetchItems();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete configuration");
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="container mx-auto px-4 py-8">
        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h1 className="text-3xl font-bold mb-2">Telephony configurations</h1>
            <p className="text-muted-foreground">
              Connect one or more telephony provider accounts. Each campaign uses one
              configuration; inbound calls are routed to the right one by account ID.{" "}
              <a
                href="https://docs.dograh.com/integrations/telephony/overview"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 underline"
              >
                Learn more <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4 mr-2" /> Add configuration
          </Button>
        </div>

        {telnyxMissingWebhookPublicKeyCount > 0 && (
          <div className="mb-6 rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5" />
              <div className="space-y-1 text-sm">
                <p className="font-medium">Webhook public key not configured</p>
                <p>
                  {telnyxMissingWebhookPublicKeyCount === 1
                    ? "1 Telnyx configuration is"
                    : `${telnyxMissingWebhookPublicKeyCount} Telnyx configurations are`}{" "}
                  missing a webhook public key. Without it, Telnyx call status
                  updates and inbound calls will be rejected starting{" "}
                  <span className="font-medium">15 May 2026</span>. Copy your
                  public key from{" "}
                  <span className="whitespace-nowrap">
                    Mission Control Portal → Keys &amp; Credentials → Public Key
                  </span>{" "}
                  and paste it into the affected Telnyx configuration below.
                </p>
              </div>
            </div>
          </div>
        )}

        {loading ? (
          <div className="grid gap-3">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : items.length === 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>No telephony configurations yet</CardTitle>
              <CardDescription>
                Add one to enable outbound calls and receive inbound calls.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4 mr-2" /> Add configuration
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-3">
            {items.map((item) => (
              <Card key={item.id}>
                <CardContent className="flex items-center gap-4 py-4">
                  <Link
                    href={`/telephony-configurations/${item.id}`}
                    className="flex flex-1 items-center gap-4 min-w-0"
                  >
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">{item.name}</span>
                        <Badge variant="secondary">{item.provider}</Badge>
                        {item.is_default_outbound && (
                          <Badge className="gap-1">
                            <Star className="h-3 w-3 fill-current" />
                            Default
                          </Badge>
                        )}
                      </div>
                      <span className="text-sm text-muted-foreground">
                        {item.phone_number_count} phone{" "}
                        {item.phone_number_count === 1 ? "number" : "numbers"}
                      </span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          navigator.clipboard
                            .writeText(String(item.id))
                            .then(() => toast.success("Configuration ID copied"))
                            .catch(() => toast.error("Failed to copy ID"));
                        }}
                        title="Click to copy"
                        className="inline-flex items-center gap-1 self-start rounded font-mono text-xs text-muted-foreground hover:text-foreground"
                      >
                        <span className="truncate">Configuration ID: {item.id}</span>
                        <Copy className="h-3 w-3 shrink-0" />
                      </button>
                    </div>
                  </Link>
                  <div className="flex items-center gap-1">
                    {!item.is_default_outbound && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onSetDefault(item)}
                        title="Set as default outbound"
                      >
                        <Star className="h-4 w-4" />
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onEdit(item)}
                      title="Edit"
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setDeleteTarget(item)}
                      title="Delete"
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                    <Link
                      href={`/telephony-configurations/${item.id}`}
                      className="text-muted-foreground"
                      aria-label="View phone numbers"
                    >
                      <ChevronRight className="h-5 w-5" />
                    </Link>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      <ConfigFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        existing={null}
        onSaved={onSaved}
      />
      <ConfigFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        existing={editTarget}
        onSaved={onSaved}
      />

      <AlertDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete configuration?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.name} and all of its phone numbers will be removed. Any
              campaigns that reference this configuration will block the deletion until
              they are reassigned.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmDelete}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function detailFromError(err: unknown): string {
  if (typeof err === "string") return err;
  const e = err as { detail?: unknown };
  if (typeof e?.detail === "string") return e.detail;
  if (Array.isArray(e?.detail) && e.detail.length > 0) {
    const first = e.detail[0] as { msg?: string };
    if (first?.msg) return first.msg;
  }
  return "Request failed";
}
