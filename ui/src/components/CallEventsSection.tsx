"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import {
  deleteCallEventsSettingsApiV1OrganizationsCallEventsDelete,
  getCallEventsSettingsApiV1OrganizationsCallEventsGet,
  saveCallEventsSettingsApiV1OrganizationsCallEventsPut,
  testCallEventsConnectionApiV1OrganizationsCallEventsTestPost,
} from "@/client/sdk.gen";
import type { CallEventsSettings } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useOrgConfig } from "@/context/OrgConfigContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type DestinationFieldsProps = {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  deploymentIdentityAvailable: boolean;
};

function BigQueryFields({ config, onChange, deploymentIdentityAvailable }: DestinationFieldsProps) {
  const authMode = config.auth_mode === "application_default" ? "application_default" : "service_account";
  const update = (field: string, value: string) => onChange({ ...config, [field]: value });
  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="call-events-table">Table</Label>
        <Input id="call-events-table" placeholder="project.dataset.table" value={String(config.table ?? "")} onChange={(e) => update("table", e.target.value)} required />
        <p className="text-sm text-muted-foreground">Use an existing table with the pipeline diagnostics schema.</p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="call-events-auth">Authentication</Label>
        <Select value={authMode} onValueChange={(value) => onChange({ table: config.table ?? "", auth_mode: value })}>
          <SelectTrigger id="call-events-auth"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="service_account">Service account</SelectItem>
            {deploymentIdentityAvailable && <SelectItem value="application_default">Deployment identity</SelectItem>}
          </SelectContent>
        </Select>
      </div>
      {authMode === "service_account" ? (
        <>
          <div className="space-y-2">
            <Label htmlFor="call-events-email">Service account email</Label>
            <Input id="call-events-email" type="email" autoComplete="off" placeholder="account@project.iam.gserviceaccount.com" value={String(config.client_email ?? "")} onChange={(e) => update("client_email", e.target.value)} required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="call-events-key">Private key</Label>
            <Textarea id="call-events-key" autoComplete="off" spellCheck={false} placeholder="Paste the private_key value from your service account key" value={String(config.private_key ?? "")} onChange={(e) => update("private_key", e.target.value.replaceAll("\\n", "\n"))} required className="font-mono text-xs" />
            <p className="text-sm text-muted-foreground">Saved keys are masked. Leave the masked value unchanged to keep the current key.</p>
          </div>
        </>
      ) : <p className="text-sm text-muted-foreground">Uses the Google identity configured on your Dograh server.</p>}
      <p className="text-sm text-muted-foreground">The identity needs permission to read the table schema and insert rows.</p>
    </div>
  );
}

// Each destination owns its fields. Adding a sink does not change event capture.
const destinations = {
  bigquery: { label: "BigQuery", Fields: BigQueryFields },
};

export function CallEventsSection() {
  const { user, loading: authLoading } = useAuth();
  const { orgContext, loading: orgLoading } = useOrgConfig();
  if (authLoading || orgLoading || !user || !orgContext?.organization_id) {
    return <p className="text-sm text-muted-foreground">Loading call event settings…</p>;
  }
  return <CallEventsForm key={orgContext.organization_id} />;
}

function CallEventsForm() {
  const [settings, setSettings] = useState<CallEventsSettings>({ enabled: false, sink_type: "bigquery", config: {} });
  const [deploymentIdentityAvailable, setDeploymentIdentityAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setLoadError(null);
    try {
      const { data, error } = await getCallEventsSettingsApiV1OrganizationsCallEventsGet({ signal });
      if (signal?.aborted) return;
      if (error || !data) throw new Error(detailFromError(error, "Could not load call event settings"));
      setSettings({ enabled: data.enabled, sink_type: data.sink_type ?? "bigquery", config: data.config ?? {} });
      setDeploymentIdentityAvailable(data.deployment_identity_available);
    } catch (error) {
      if (!signal?.aborted) setLoadError(error instanceof Error ? error.message : "Could not load call event settings");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function run(action: "save" | "test" | "remove") {
    setBusy(true);
    try {
      if (action === "test") {
        const { data, error } = await testCallEventsConnectionApiV1OrganizationsCallEventsTestPost({ body: settings });
        if (error || !data) throw new Error(detailFromError(error, "Connection check failed"));
        toast.success(data.message);
      } else {
        const { data, error } = action === "save"
          ? await saveCallEventsSettingsApiV1OrganizationsCallEventsPut({ body: settings })
          : await deleteCallEventsSettingsApiV1OrganizationsCallEventsDelete();
        if (error || !data) throw new Error(detailFromError(error, "Could not update call event settings"));
        setSettings({ enabled: data.enabled, sink_type: data.sink_type ?? "bigquery", config: data.config ?? {} });
        toast.success(action === "save" ? "Call event settings saved" : "Call event destination removed");
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading call event settings…</p>;
  if (loadError) return <div className="space-y-2"><p role="alert">{loadError}</p><Button variant="outline" onClick={() => void load()}>Retry</Button></div>;

  const destination = destinations[settings.sink_type as keyof typeof destinations];
  return (
    <form onSubmit={(event) => { event.preventDefault(); void run("save"); }}>
      <fieldset disabled={busy} className="space-y-4">
        <div className="flex items-center gap-2">
          <Switch id="call-events-enabled" checked={settings.enabled ?? false} onCheckedChange={(enabled) => setSettings({ ...settings, enabled })} />
          <Label htmlFor="call-events-enabled">Export call events</Label>
        </div>
        <p className="text-sm text-muted-foreground">Send call diagnostics, including stage latencies, silence events and call outcomes, to your destination after each call.</p>
        <div className="space-y-2">
          <Label htmlFor="call-events-destination">Destination</Label>
          <Select value={settings.sink_type ?? "bigquery"} onValueChange={(sink_type) => setSettings({ ...settings, sink_type, config: {} })}>
            <SelectTrigger id="call-events-destination"><SelectValue /></SelectTrigger>
            <SelectContent>{Object.entries(destinations).map(([value, item]) => <SelectItem key={value} value={value}>{item.label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        {destination && <destination.Fields config={settings.config ?? {}} deploymentIdentityAvailable={deploymentIdentityAvailable} onChange={(config) => setSettings({ ...settings, config })} />}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={!destination}>{busy ? "Working…" : "Save"}</Button>
          <Button type="button" variant="outline" disabled={!destination} onClick={() => void run("test")}>Test connection</Button>
          <Button type="button" variant="ghost" onClick={() => void run("remove")}>Remove</Button>
        </div>
      </fieldset>
    </form>
  );
}
