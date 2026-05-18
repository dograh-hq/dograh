"use client";

import {
  ArrowLeft,
  Copy,
  ExternalLink,
  Pencil,
  Plus,
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
  setDefaultCallerIdApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersPhoneNumberIdSetDefaultCallerPost,
  setDefaultOutboundApiV1OrganizationsTelephonyConfigsConfigIdSetDefaultOutboundPost,
} from "@/client/sdk.gen";
import type {
  PhoneNumberResponse,
  TelephonyConfigurationDetail,
} from "@/client/types.gen";
import { ConfigFormDialog } from "@/components/telephony/ConfigFormDialog";
import { PhoneNumberDialog } from "@/components/telephony/PhoneNumberDialog";
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
import { useAuth } from "@/lib/auth";

export default function TelephonyConfigurationDetailPage() {
  const router = useRouter();
  const params = useParams<{ configId: string }>();
  const configId = Number(params.configId);

  const { user, getAccessToken, loading: authLoading } = useAuth();
  const [config, setConfig] = useState<TelephonyConfigurationDetail | null>(null);
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
            </div>
            <CardDescription>
              Updated {new Date(config.updated_at).toLocaleString()}
            </CardDescription>
            <button
              type="button"
              onClick={() => {
                navigator.clipboard
                  .writeText(String(config.id))
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
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
            {Object.entries(config.credentials ?? {}).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-3">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="font-mono text-right truncate max-w-[60%]">
                  {String(v ?? "")}
                </dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>

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
          <Button
            size="sm"
            onClick={() => {
              setPhoneEditTarget(null);
              setPhoneDialogOpen(true);
            }}
          >
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
                  <TableHead>Type</TableHead>
                  <TableHead>Label</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Inbound workflow</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {phoneNumbers.map((n) => (
                  <TableRow key={n.id}>
                    <TableCell className="font-mono">{n.address}</TableCell>
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
                          onClick={() => {
                            setPhoneEditTarget(n);
                            setPhoneDialogOpen(true);
                          }}
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
