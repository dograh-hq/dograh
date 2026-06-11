"use client";

import {
  ExternalLink,
  Loader2,
  Phone,
  RefreshCw,
  RotateCcw,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  type AdminClient,
  assignDidToClient,
  listAdminClients,
  retryProvisionClient,
} from "@/lib/adminClients";
import { useAuth } from "@/lib/auth";
import { impersonateAsSuperadmin } from "@/lib/utils";

function VoiceLinkStatusBadge({ client }: { client: AdminClient }) {
  if (client.voicelink_status === "provisioned") {
    return <Badge>Provisioned</Badge>;
  }
  if (client.voicelink_status === "pending") {
    const badge = <Badge variant="destructive">Pending</Badge>;
    if (!client.voicelink_error) return badge;
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex cursor-help">{badge}</span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          <p>{client.voicelink_error}</p>
        </TooltipContent>
      </Tooltip>
    );
  }
  return <Badge variant="outline">Not provisioned</Badge>;
}

export default function ClientsPage() {
  const { user, getAccessToken, loading: authLoading } = useAuth();
  const hasFetched = useRef(false);

  const [clients, setClients] = useState<AdminClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Assign DID dialog state
  const [assignTarget, setAssignTarget] = useState<AdminClient | null>(null);
  const [didNumber, setDidNumber] = useState("");
  const [clientId, setClientId] = useState("");

  // Retry provisioning dialog state
  const [retryTarget, setRetryTarget] = useState<AdminClient | null>(null);
  const [retryPassword, setRetryPassword] = useState("");

  const fetchClients = useCallback(
    async (showSpinner = false) => {
      if (showSpinner) setRefreshing(true);
      try {
        const token = await getAccessToken();
        if (!token) throw new Error("Missing access token");
        const result = await listAdminClients(token);
        setClients(result.clients);
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Failed to load clients",
        );
      } finally {
        setLoading(false);
        if (showSpinner) setRefreshing(false);
      }
    },
    [getAccessToken],
  );

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    fetchClients();
  }, [authLoading, user, fetchClients]);

  const openAssignDialog = (client: AdminClient) => {
    setAssignTarget(client);
    setDidNumber(client.did_number ?? "");
    setClientId(client.voicelink_client_id ?? "");
  };

  const openRetryDialog = (client: AdminClient) => {
    setRetryTarget(client);
    setRetryPassword("");
  };

  const onAssignDid = async () => {
    if (!assignTarget) return;
    setSubmitting(true);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("Missing access token");
      const result = await assignDidToClient(token, assignTarget.organization_id, {
        did_number: didNumber.trim(),
        ...(clientId.trim() ? { client_id: clientId.trim() } : {}),
      });
      toast.success(
        result.created
          ? "VoiceLink telephony configuration created with the DID"
          : "DID updated on the existing VoiceLink configuration",
      );
      setAssignTarget(null);
      await fetchClients();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to assign DID");
    } finally {
      setSubmitting(false);
    }
  };

  const onRetryProvision = async () => {
    if (!retryTarget) return;
    setSubmitting(true);
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("Missing access token");
      const result = await retryProvisionClient(
        token,
        retryTarget.organization_id,
        retryPassword,
      );
      if (result.voicelink_status === "provisioned") {
        toast.success("VoiceLink client provisioned");
      } else {
        toast.error(result.voicelink_error || "Provisioning is still pending");
      }
      setRetryTarget(null);
      await fetchClients();
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to retry provisioning",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const onImpersonate = async (client: AdminClient) => {
    if (!client.owner_provider_id) {
      toast.error("This organization has no owner user to impersonate");
      return;
    }
    try {
      const token = await getAccessToken();
      if (!token) throw new Error("Missing access token");
      await impersonateAsSuperadmin({
        accessToken: token,
        providerUserId: client.owner_provider_id,
        redirectPath: "/model-configurations",
        openInNewTab: true,
      });
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to impersonate user",
      );
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="container mx-auto max-w-6xl px-4 py-8">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="mb-2 flex items-center gap-2 text-3xl font-bold">
              <Users className="h-7 w-7" /> Clients
            </h1>
            <p className="text-muted-foreground">
              Client organizations and their VoiceLink provisioning state.
              Assign a DID once the client&apos;s channels are purchased, or
              retry provisioning when it is pending. Use{" "}
              <Link href="/superadmin" className="underline underline-offset-2">
                superadmin impersonation
              </Link>{" "}
              to configure a client&apos;s models.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => fetchClients(true)}
            disabled={loading || refreshing}
          >
            <RefreshCw
              className={`mr-2 h-4 w-4 ${refreshing ? "animate-spin" : ""}`}
            />
            Refresh
          </Button>
        </div>

        {loading ? (
          <div className="grid gap-3">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : clients.length === 0 ? (
          <div className="rounded-md border p-8 text-center text-muted-foreground">
            No client organizations yet. New signups appear here automatically.
          </div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Organization</TableHead>
                  <TableHead>Owner email</TableHead>
                  <TableHead>VoiceLink status</TableHead>
                  <TableHead>Client ID</TableHead>
                  <TableHead>DID</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {clients.map((client) => (
                  <TableRow key={client.organization_id}>
                    <TableCell>
                      <div className="font-medium">
                        #{client.organization_id}
                      </div>
                      <div className="max-w-[180px] truncate text-xs text-muted-foreground">
                        {client.organization_name}
                      </div>
                    </TableCell>
                    <TableCell>{client.owner_email ?? "—"}</TableCell>
                    <TableCell>
                      <VoiceLinkStatusBadge client={client} />
                    </TableCell>
                    <TableCell>
                      {client.voicelink_client_id ?? "—"}
                      {client.voicelink_username && (
                        <div className="text-xs text-muted-foreground">
                          {client.voicelink_username}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      {client.did_number ??
                        (client.has_voicelink_config ? "Config, no DID" : "—")}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openAssignDialog(client)}
                        >
                          <Phone className="mr-1 h-3.5 w-3.5" />
                          Assign DID
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openRetryDialog(client)}
                        >
                          <RotateCcw className="mr-1 h-3.5 w-3.5" />
                          Retry
                        </Button>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onImpersonate(client)}
                              disabled={!client.owner_provider_id}
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent side="top">
                            <p>
                              Impersonate the owner (new tab) to configure
                              their models
                            </p>
                          </TooltipContent>
                        </Tooltip>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {/* Assign DID dialog */}
        <Dialog
          open={assignTarget !== null}
          onOpenChange={(open) => !open && setAssignTarget(null)}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Assign DID</DialogTitle>
              <DialogDescription>
                Creates or updates the org&apos;s VoiceLink telephony
                configuration with this DID and marks it default for outbound.
                The client can call once the DID and channels are mapped in
                the VoiceLink portal.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="assign-did-number">DID number</Label>
                <Input
                  id="assign-did-number"
                  value={didNumber}
                  onChange={(e) => setDidNumber(e.target.value)}
                  placeholder="919484959244"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="assign-client-id">
                  VoiceLink client ID (optional)
                </Label>
                <Input
                  id="assign-client-id"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  placeholder={
                    assignTarget?.voicelink_client_id ?? "e.g. 474"
                  }
                />
                <p className="text-xs text-muted-foreground">
                  Defaults to the org&apos;s provisioned client ID when left
                  empty.
                </p>
              </div>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setAssignTarget(null)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button
                onClick={onAssignDid}
                disabled={submitting || !didNumber.trim()}
              >
                {submitting && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                Assign DID
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Retry provisioning dialog */}
        <Dialog
          open={retryTarget !== null}
          onOpenChange={(open) => !open && setRetryTarget(null)}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Retry VoiceLink provisioning</DialogTitle>
              <DialogDescription>
                Re-runs client creation for{" "}
                {retryTarget?.owner_email ?? "this organization"} using the
                stored username
                {retryTarget?.voicelink_username
                  ? ` (${retryTarget.voicelink_username})`
                  : ""}
                . Client passwords are never stored — set a new VoiceLink
                password for the client below.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label htmlFor="retry-password">New VoiceLink password</Label>
              <Input
                id="retry-password"
                type="password"
                value={retryPassword}
                onChange={(e) => setRetryPassword(e.target.value)}
                placeholder="Minimum 8 characters"
              />
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setRetryTarget(null)}
                disabled={submitting}
              >
                Cancel
              </Button>
              <Button
                onClick={onRetryProvision}
                disabled={submitting || retryPassword.length < 8}
              >
                {submitting && (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                )}
                Retry provisioning
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
