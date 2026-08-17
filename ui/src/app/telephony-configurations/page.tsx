"use client";

import {
  AlertTriangle,
  ChevronRight,
  Copy,
  ExternalLink,
  Pencil,
  Plus,
  RotateCcw,
  Star,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  deleteTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdDelete,
  getTelephonyConfigurationByIdApiV1OrganizationsTelephonyConfigsConfigIdGet,
  getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet,
  listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
  reactivateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdReactivatePost,
  setDefaultOutboundApiV1OrganizationsTelephonyConfigsConfigIdSetDefaultOutboundPost,
} from "@/client/sdk.gen";
import type {
  TelephonyConfigurationDetail,
  TelephonyConfigurationListItem,
  TelephonyProviderMetadata,
} from "@/client/types.gen";
import { ConfigFormDialog } from "@/components/telephony/ConfigFormDialog";
import { ProviderBrand } from "@/components/telephony/ProviderBrand";
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
import { useLocale } from "@/context/LocaleContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { copyTextToClipboard } from "@/lib/clipboard";

export default function TelephonyConfigurationsPage() {
  const { t } = useLocale();
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const {
    telnyxMissingWebhookPublicKeyCount,
    vonageMissingSignatureSecretCount,
    refresh: refreshWarnings,
  } = useTelephonyConfigWarnings();
  const [items, setItems] = useState<TelephonyConfigurationListItem[]>([]);
  const [providers, setProviders] = useState<TelephonyProviderMetadata[]>([]);
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
      toast.error(err instanceof Error ? err.message : t("telephony.failedToLoadConfigurations"));
    } finally {
      setLoading(false);
    }
  }, [authLoading, user, getAccessToken, t]);

  const fetchProviders = useCallback(async () => {
    if (authLoading || !user) return;
    try {
      const token = await getAccessToken();
      const res = await getTelephonyProvidersMetadataApiV1OrganizationsTelephonyProvidersMetadataGet(
        { headers: { Authorization: `Bearer ${token}` } },
      );
      setProviders(res.data?.providers ?? []);
    } catch {
      // Branding is optional presentation metadata; configuration management still works.
      setProviders([]);
    }
  }, [authLoading, user, getAccessToken]);

  // After a save (create/update), webhook-verification warning state may have
  // changed — refresh the cached warning state so the page banner and nav badge
  // update without a manual reload.
  const onSaved = useCallback(async () => {
    await fetchItems();
    await refreshWarnings();
  }, [fetchItems, refreshWarnings]);

  useEffect(() => {
    fetchItems();
    fetchProviders();
  }, [fetchItems, fetchProviders]);

  const providersByName = Object.fromEntries(
    providers.map((provider) => [provider.provider, provider]),
  );

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
      toast.error(err instanceof Error ? err.message : t("telephony.failedToLoadConfiguration"));
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
      toast.success(`${item.name} ${t("telephony.defaultOutboundSuccess")}`);
      fetchItems();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("telephony.failedToSetDefault"));
    }
  };

  const onReactivate = async (item: TelephonyConfigurationListItem) => {
    try {
      const token = await getAccessToken();
      const res = await reactivateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdReactivatePost(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: item.id },
        },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success(`${item.name} ${t("telephony.reactivatedSuccess")}`);
      fetchItems();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : t("telephony.failedToReactivate"),
      );
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
      toast.success(t("telephony.configurationDeleted"));
      setDeleteTarget(null);
      fetchItems();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("telephony.failedToDelete"));
    }
  };

  return (
    <div className="min-h-screen">
      <div className="container mx-auto px-4 py-8">
        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h1 className="text-3xl font-bold mb-2">{t("telephony.title")}</h1>
            <p className="text-muted-foreground">
              {t("telephony.description")} {" "}
              <a
                href="https://docs.dograh.com/integrations/telephony/overview"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 underline"
              >
                {t("telephony.learnMore")} <ExternalLink className="h-3 w-3" />
              </a>
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4 mr-2" /> {t("telephony.addConfiguration")}
          </Button>
        </div>

        {telnyxMissingWebhookPublicKeyCount > 0 && (
          <div className="mb-6 rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5" />
              <div className="space-y-1 text-sm">
                <p className="font-medium">{t("telephony.webhookKeyMissing")}</p>
                <p>
                  {telnyxMissingWebhookPublicKeyCount}{" "}
                  {t(telnyxMissingWebhookPublicKeyCount === 1 ? "telephony.webhookKeyMissingSingular" : "telephony.webhookKeyMissingPlural")}
                </p>
              </div>
            </div>
          </div>
        )}

        {vonageMissingSignatureSecretCount > 0 && (
          <div className="mb-6 rounded-md border border-amber-300 bg-amber-50 p-4 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5" />
              <div className="space-y-1 text-sm">
                <p className="font-medium">{t("telephony.signatureSecretMissing")}</p>
                <p>
                  {vonageMissingSignatureSecretCount}{" "}
                  {t(vonageMissingSignatureSecretCount === 1 ? "telephony.signatureSecretMissingSingular" : "telephony.signatureSecretMissingPlural")}
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
              <CardTitle>{t("telephony.noConfigurations")}</CardTitle>
              <CardDescription>
                {t("telephony.noConfigurationsDescription")}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Button onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4 mr-2" /> {t("telephony.addConfiguration")}
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-3">
            {items.map((item) => (
              <Card key={item.id}>
                <CardContent className="flex flex-col gap-4 py-4 sm:flex-row sm:items-center">
                  <Link
                    href={`/telephony-configurations/${item.id}`}
                    className="flex flex-1 items-center gap-4 min-w-0"
                  >
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <ProviderBrand
                          logoUrl={providersByName[item.provider]?.branding?.logo_url}
                          displayName={providersByName[item.provider]?.display_name}
                        />
                        <span className="font-medium truncate">{item.name}</span>
                        <Badge variant="secondary">{item.provider}</Badge>
                        {item.is_default_outbound && (
                          <Badge className="gap-1">
                            <Star className="h-3 w-3 fill-current" />
                            {t("telephony.default")}
                          </Badge>
                        )}
                        {item.inactive && (
                          <Badge variant="destructive">{t("telephony.inactive")}</Badge>
                        )}
                      </div>
                      <span className="text-sm text-muted-foreground">
                        {item.phone_number_count} {t(item.phone_number_count === 1 ? "telephony.phoneNumber" : "telephony.phoneNumbers")}
                      </span>
                      {item.inactive && (
                        <span className="text-sm text-destructive">
                          {t("telephony.disabledAfterFailures")}
                          {item.inactive_reason ? `: ${item.inactive_reason}` : ""}
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          copyTextToClipboard(String(item.id))
                            .then(() => toast.success(t("telephony.configurationIdCopied")))
                            .catch(() => toast.error(t("telephony.failedToCopyId")));
                        }}
                        title={t("telephony.copyConfigurationId")}
                        className="inline-flex items-center gap-1 self-start rounded font-mono text-xs text-muted-foreground hover:text-foreground"
                      >
                        <span className="truncate">{t("telephony.configurationId")}: {item.id}</span>
                        <Copy className="h-3 w-3 shrink-0" />
                      </button>
                    </div>
                  </Link>
                  <div className="flex w-full flex-wrap items-center justify-end gap-1 sm:w-auto sm:flex-nowrap">
                    {item.inactive && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onReactivate(item)}
                        title={t("telephony.reconnectNow")}
                      >
                        <RotateCcw className="h-4 w-4 mr-1" />
                        {t("telephony.reactivate")}
                      </Button>
                    )}
                    {!item.is_default_outbound && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => onSetDefault(item)}
                        title={t("telephony.setDefaultOutbound")}
                      >
                        <Star className="h-4 w-4" />
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onEdit(item)}
                      title={t("telephony.edit")}
                    >
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setDeleteTarget(item)}
                      title={t("telephony.delete")}
                    >
                      <Trash2 className="h-4 w-4 text-destructive" />
                    </Button>
                    <Button variant="outline" size="sm" asChild>
                      <Link
                        href={`/telephony-configurations/${item.id}`}
                        aria-label={`${t("telephony.managePhoneNumbersFor")} ${item.name}`}
                      >
                        {t("telephony.managePhoneNumbers")}
                        <ChevronRight className="h-4 w-4" />
                      </Link>
                    </Button>
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
            <AlertDialogTitle>{t("telephony.deleteConfiguration")}?</AlertDialogTitle>
            <AlertDialogDescription>
              {deleteTarget?.name} {t("telephony.deleteConfigurationDescription")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("telephony.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmDelete}>{t("telephony.delete")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
