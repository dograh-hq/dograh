"use client";

import {
  AlertTriangle,
  ArrowLeft,
  Copy,
  ExternalLink,
  Pencil,
  Plus,
  RotateCcw,
  Star,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  deletePhoneNumberApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersPhoneNumberIdDelete,
  getTelephonyConfigurationByIdApiV1OrganizationsTelephonyConfigsConfigIdGet,
  listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet,
  reactivateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdReactivatePost,
  setDefaultCallerIdApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersPhoneNumberIdSetDefaultCallerPost,
  setDefaultOutboundApiV1OrganizationsTelephonyConfigsConfigIdSetDefaultOutboundPost,
} from "@/client/sdk.gen";
import type {
  PhoneNumberResponse,
  TelephonyConfigurationDetail,
} from "@/client/types.gen";
import { ConfigFormDialog } from "@/components/telephony/ConfigFormDialog";
import { PhoneNumberDialog } from "@/components/telephony/PhoneNumberDialog";
import { SetupChecklistCard } from "@/components/telephony/SetupChecklistCard";
import { SipConnectivityCard } from "@/components/telephony/SipConnectivityCard";
import { TrunkCard } from "@/components/telephony/TrunkCard";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAppConfig } from "@/context/AppConfigContext";
import { useOrgConfig } from "@/context/OrgConfigContext";
import { useOrganizationTimezone } from "@/hooks/useOrganizationTimezone";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { copyTextToClipboard } from "@/lib/clipboard";
import { formatDateTime } from "@/lib/dateTime";
import { resolveWebhookBaseUrl } from "@/lib/webhookUrl";

const INBOUND_WEBHOOK_PATH = "/api/v1/telephony/inbound/run";

export default function TelephonyConfigurationDetailPage() {
  const router = useRouter();
  const params = useParams<{ configId: string }>();
  const configId = Number(params.configId);

  const { user, getAccessToken, loading: authLoading } = useAuth();
  const { config: appConfig } = useAppConfig();
  const { externalPbxIntegrationsEnabled } = useOrgConfig();
  const organizationTimezone = useOrganizationTimezone();
  const inboundWebhookUrl = `${resolveWebhookBaseUrl(appConfig?.tunnelUrl)}${INBOUND_WEBHOOK_PATH}`;
  const [config, setConfig] = useState<TelephonyConfigurationDetail | null>(null);
  // ARI only: Dograh generates the Stasis application name, so the dialplan
  // line cannot be written until the configuration has been saved.
  const stasisAppName =
    typeof config?.credentials?.stasis_app_name === "string"
      ? config.credentials.stasis_app_name
      : "";
  const stasisDialplanLine = `same => n,Stasis(${stasisAppName})`;
  const [phoneNumbers, setPhoneNumbers] = useState<PhoneNumberResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [editConfigOpen, setEditConfigOpen] = useState(false);

  const [phoneDialogOpen, setPhoneDialogOpen] = useState(false);
  const [phoneEditTarget, setPhoneEditTarget] = useState<PhoneNumberResponse | null>(
    null,
  );
  const [phoneDeleteTarget, setPhoneDeleteTarget] = useState<PhoneNumberResponse | null>(
    null,
  );
  // Set when the dialog is opened from a trunk, so the number lands on it.
  const [phoneDefaultTrunkId, setPhoneDefaultTrunkId] = useState<number | null>(null);

  const openPhoneDialog = useCallback(
    (target: PhoneNumberResponse | null, trunkId: number | null = null) => {
      setPhoneEditTarget(target);
      setPhoneDefaultTrunkId(trunkId);
      setPhoneDialogOpen(true);
    },
    [],
  );

  const fetchAll = useCallback(async () => {
    if (authLoading || !user || !configId) return;
    setLoading(true);
    try {
      const token = await getAccessToken();
      const [cfgRes, numbersRes] = await Promise.all([
        getTelephonyConfigurationByIdApiV1OrganizationsTelephonyConfigsConfigIdGet({
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: configId },
        }),
        listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet({
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: configId },
        }),
      ]);

      if (cfgRes.error) throw new Error(detailFromError(cfgRes.error));
      if (numbersRes.error) throw new Error(detailFromError(numbersRes.error));

      setConfig(cfgRes.data ?? null);
      setPhoneNumbers(numbersRes.data?.phone_numbers ?? []);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load configuration");
    } finally {
      setLoading(false);
    }
  }, [authLoading, user, configId, getAccessToken]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const onSetDefaultOutbound = async () => {
    if (!config) return;
    try {
      const token = await getAccessToken();
      const res = await setDefaultOutboundApiV1OrganizationsTelephonyConfigsConfigIdSetDefaultOutboundPost(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: config.id },
        },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success("Set as default outbound");
      fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to set default");
    }
  };

  const onReactivate = async () => {
    if (!config) return;
    try {
      const token = await getAccessToken();
      const res = await reactivateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdReactivatePost(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: config.id },
        },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success("Reactivated — reconnecting within a minute");
      fetchAll();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to reactivate configuration",
      );
    }
  };

  const onSetDefaultCaller = async (n: PhoneNumberResponse) => {
    try {
      const token = await getAccessToken();
      const res = await setDefaultCallerIdApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersPhoneNumberIdSetDefaultCallerPost(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: { config_id: configId, phone_number_id: n.id },
        },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success(`${n.address} is now the default caller ID`);
      fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to set default caller");
    }
  };

  const onConfirmDeletePhone = async () => {
    if (!phoneDeleteTarget) return;
    try {
      const token = await getAccessToken();
      const res = await deletePhoneNumberApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersPhoneNumberIdDelete(
        {
          headers: { Authorization: `Bearer ${token}` },
          path: {
            config_id: configId,
            phone_number_id: phoneDeleteTarget.id,
          },
        },
      );
      if (res.error) throw new Error(detailFromError(res.error));
      toast.success("Phone number deleted");
      setPhoneDeleteTarget(null);
      fetchAll();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to delete phone number");
    }
  };

  if (loading) {
    return (
      <div className="container mx-auto px-4 py-8 space-y-3">
        <Skeleton className="h-10 w-1/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (!config) {
    return (
      <div className="container mx-auto px-4 py-8">
        <Button variant="ghost" onClick={() => router.push("/telephony-configurations")}>
          <ArrowLeft className="h-4 w-4 mr-2" /> Back
        </Button>
        <p className="mt-4 text-muted-foreground">Configuration not found.</p>
      </div>
    );
  }

  return (
    <div className="container mx-auto px-4 py-8 space-y-6">
      <div>
        <Link
          href="/telephony-configurations"
          className="inline-flex items-center text-sm text-muted-foreground hover:underline"
        >
          <ArrowLeft className="h-4 w-4 mr-1" /> All configurations
        </Link>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div className="space-y-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <CardTitle className="truncate">{config.name}</CardTitle>
              <Badge variant="secondary">{config.provider}</Badge>
              {config.is_default_outbound && (
                <Badge className="gap-1">
                  <Star className="h-3 w-3 fill-current" />
                  Default
                </Badge>
              )}
              {config.inactive && <Badge variant="destructive">Inactive</Badge>}
            </div>
            <CardDescription>
              Updated {formatDateTime(config.updated_at, organizationTimezone)}
            </CardDescription>
            <button
              type="button"
              onClick={() => {
                copyTextToClipboard(String(config.id))
                  .then(() => toast.success("Configuration ID copied"))
                  .catch(() => toast.error("Failed to copy ID"));
              }}
              title="Click to copy"
              className="inline-flex items-center gap-1 self-start rounded font-mono text-xs text-muted-foreground hover:text-foreground"
            >
              <span className="truncate">Configuration ID: {config.id}</span>
              <Copy className="h-3 w-3 shrink-0" />
            </button>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {config.inactive && (
              <Button variant="outline" size="sm" onClick={onReactivate}>
                <RotateCcw className="h-4 w-4 mr-2" /> Reactivate
              </Button>
            )}
            {!config.is_default_outbound && (
              <Button variant="outline" size="sm" onClick={onSetDefaultOutbound}>
                <Star className="h-4 w-4 mr-2" /> Set as default
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={() => setEditConfigOpen(true)}>
              <Pencil className="h-4 w-4 mr-2" /> Edit credentials
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {config.inactive && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5 text-destructive" />
                <div className="space-y-1 text-sm">
                  <p className="font-medium text-destructive">
                    This configuration is disabled
                  </p>
                  <p className="text-muted-foreground">
                    Dograh stopped reconnecting after repeated connection
                    failures
                    {config.inactive_reason ? `: ${config.inactive_reason}` : ""}.
                    Calls will not work until it is reconnected. Correct the
                    settings below, then choose Reactivate to try again.
                  </p>
                  {config.inactive_since && (
                    <p className="text-muted-foreground">
                      Disabled{" "}
                      {formatDateTime(config.inactive_since, organizationTimezone)}
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            {Object.entries(config.credentials ?? {})
              .filter(([key]) => key !== "external_pbx" || externalPbxIntegrationsEnabled)
              .filter(([key]) => key !== "stasis_app_name")
              .map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3">
                  <dt className="text-muted-foreground">{k}</dt>
                  <dd className="font-mono text-right truncate max-w-[60%]">
                    {v && typeof v === "object" ? "Configured" : String(v ?? "")}
                  </dd>
                </div>
              ))}
          </dl>
          {stasisAppName && (
            <div className="space-y-1 rounded-md border border-dashed p-3">
              <p className="text-sm font-medium">Route calls into this Stasis application</p>
              <p className="text-xs text-muted-foreground">
                Add this line to your Asterisk <code>extensions.conf</code>, then run{" "}
                <code>dialplan reload</code>. Until you do, calls reach Asterisk but never
                arrive at Dograh.
              </p>
              <button
                type="button"
                onClick={() => {
                  copyTextToClipboard(stasisDialplanLine)
                    .then(() => toast.success("Dialplan line copied"))
                    .catch(() => toast.error("Failed to copy"));
                }}
                title="Click to copy"
                className="group mt-1 flex w-full items-center gap-2 rounded-md border bg-muted/20 p-2 text-left font-mono text-xs transition-colors hover:bg-muted/40"
              >
                <code className="flex-1 truncate">{stasisDialplanLine}</code>
                <Copy className="h-3 w-3 shrink-0 text-muted-foreground group-hover:text-foreground" />
              </button>
            </div>
          )}
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">Inbound webhook URL</p>
            <button
              type="button"
              onClick={() => {
                const url = inboundWebhookUrl;
                copyTextToClipboard(url)
                  .then(() => toast.success("Inbound webhook URL copied"))
                  .catch(() => toast.error("Failed to copy URL"));
              }}
              title="Click to copy inbound webhook URL"
              aria-label="Copy inbound webhook URL"
              className="inline-flex items-center gap-1 self-start rounded font-mono text-xs text-muted-foreground hover:text-foreground"
            >
              <span className="truncate">{inboundWebhookUrl}</span>
              <Copy className="h-3 w-3 shrink-0" />
            </button>
          </div>
        </CardContent>
      </Card>

      {config.setup_checklist ? (
        <SetupChecklistCard
          checklist={config.setup_checklist}
          connectivity={config.connectivity}
        />
      ) : null}

      {config.sip_connectivity?.regions.length ? (
        <SipConnectivityCard
          details={config.sip_connectivity}
          // The checklist sends the user in here for the endpoints to hand
          // their carrier, so don't make them find the toggle first.
          defaultOpen={config.setup_checklist?.ready_for_outbound === false}
        />
      ) : null}

      {config.supports_trunks ? (
        <TrunkCard
          configuration={config}
          phoneNumbers={phoneNumbers}
          onChanged={fetchAll}
          onAddPhoneNumber={(trunk) => openPhoneDialog(null, trunk.id)}
          onEditPhoneNumber={(number) => openPhoneDialog(number)}
        />
      ) : null}

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-4">
          <div className="space-y-1">
            <CardTitle>Phone numbers</CardTitle>
            <CardDescription>
              Numbers used as caller ID for outbound and accepted for inbound matching.
              SIP URIs and extensions are supported alongside PSTN numbers.{" "}
              <a
                href="https://docs.dograh.com/integrations/telephony/inbound"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 underline"
              >
                Inbound docs <ExternalLink className="h-3 w-3" />
              </a>
            </CardDescription>
          </div>
          <Button size="sm" onClick={() => openPhoneDialog(null)}>
            <Plus className="h-4 w-4 mr-2" /> Add phone number
          </Button>
        </CardHeader>
        <CardContent>
          {phoneNumbers.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No phone numbers yet. Add one to start placing or receiving calls on this
              configuration.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Address</TableHead>
                  <TableHead>Phone number ID</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Label</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Inbound workflow</TableHead>
                  {(config.trunks?.length ?? 0) > 0 && (
                    <TableHead>Outbound trunk</TableHead>
                  )}
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {phoneNumbers.map((n) => (
                  <TableRow key={n.id}>
                    <TableCell className="font-mono">{n.address}</TableCell>
                    <TableCell>
                      <button
                        type="button"
                        onClick={() => {
                          copyTextToClipboard(String(n.id))
                            .then(() => toast.success("Phone number ID copied"))
                            .catch(() => toast.error("Failed to copy ID"));
                        }}
                        title="Click to copy phone number ID"
                        aria-label={`Copy phone number ID ${n.id}`}
                        className="group inline-flex items-center gap-1 rounded font-mono text-xs text-muted-foreground hover:text-foreground"
                      >
                        <span>{n.id}</span>
                        <Copy className="h-3 w-3 shrink-0" />
                      </button>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline">{n.address_type}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {n.label ?? "-"}
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {n.is_active ? (
                          <Badge variant="secondary">Active</Badge>
                        ) : (
                          <Badge variant="outline">Inactive</Badge>
                        )}
                        {n.is_default_caller_id && (
                          <Badge className="gap-1">
                            <Star className="h-3 w-3 fill-current" /> Default caller
                          </Badge>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {n.inbound_workflow_id ? (
                        <Link
                          href={`/workflow/${n.inbound_workflow_id}`}
                          className="inline-flex items-center gap-1 hover:underline hover:text-foreground"
                        >
                          <span>#{n.inbound_workflow_id}</span>
                          {n.inbound_workflow_name && (
                            <span
                              className="truncate max-w-[160px]"
                              title={n.inbound_workflow_name}
                            >
                              {n.inbound_workflow_name.length > 24
                                ? `${n.inbound_workflow_name.slice(0, 24)}…`
                                : n.inbound_workflow_name}
                            </span>
                          )}
                        </Link>
                      ) : (
                        "-"
                      )}
                    </TableCell>
                    {(config.trunks?.length ?? 0) > 0 && (
                      <TableCell className="text-muted-foreground">
                        {/* Unassigned is only ambiguous once there are
                            several trunks; with one the call path falls
                            back to it. */}
                        {config.trunks?.find(
                          (t) => t.id === n.telephony_trunk_id,
                        )?.name ?? (
                          <span
                            className={
                              (config.trunks?.length ?? 0) > 1
                                ? "text-amber-600 dark:text-amber-500"
                                : undefined
                            }
                          >
                            Unassigned
                          </span>
                        )}
                      </TableCell>
                    )}
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        {!n.is_default_caller_id && n.is_active && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onSetDefaultCaller(n)}
                            title="Set as default caller ID"
                          >
                            <Star className="h-4 w-4" />
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openPhoneDialog(n)}
                          title="Edit"
                        >
                          <Pencil className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setPhoneDeleteTarget(n)}
                          title="Delete"
                        >
                          <Trash2 className="h-4 w-4 text-destructive" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConfigFormDialog
        open={editConfigOpen}
        onOpenChange={setEditConfigOpen}
        existing={config}
        onSaved={fetchAll}
      />

      <PhoneNumberDialog
        open={phoneDialogOpen}
        onOpenChange={setPhoneDialogOpen}
        configId={configId}
        trunks={config?.trunks}
        defaultTrunkId={phoneDefaultTrunkId}
        existing={phoneEditTarget}
        onSaved={fetchAll}
      />

      <AlertDialog
        open={!!phoneDeleteTarget}
        onOpenChange={(o) => !o && setPhoneDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete phone number?</AlertDialogTitle>
            <AlertDialogDescription>
              {phoneDeleteTarget?.address} will no longer accept inbound calls or be
              available as a caller ID for this configuration.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={onConfirmDeletePhone}>Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
