"use client";

import { ChevronDown } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { updateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdPut } from "@/client/sdk.gen";
import type {
  CloudonixConfigurationRequest,
  CloudonixConfigurationResponse,
  CloudonixOutboundTrunkConfiguration,
  CloudonixOutboundTrunkProfile,
  TelephonyConfigurationDetail,
} from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

type Transport = "udp" | "tcp" | "tls";

interface OutboundTrunkFormState {
  enabled: boolean;
  name: string;
  ip: string;
  port: string;
  transport: Transport;
  prefix: string;
  authenticationUsername: string;
  authenticationPassword: string;
  overwriteFrom: boolean;
  hostname: string;
  domain: string;
  ruriDomain: string;
  connectionTimeout: string;
  provisionalTimeout: string;
}

interface CloudonixOutboundTrunkFormProps {
  configuration: TelephonyConfigurationDetail;
  onSaved: (
    configuration: TelephonyConfigurationDetail,
  ) => void | Promise<void>;
}

function formStateFromConfiguration(
  configuration: TelephonyConfigurationDetail,
): OutboundTrunkFormState {
  const credentials =
    configuration.credentials as Partial<CloudonixConfigurationResponse>;
  const trunk = credentials.outbound_trunk;
  const profile = trunk?.profile;
  const authentication = profile?.authentication;

  return {
    // A new trunk is enabled when the user first saves the form. Preserve an
    // explicit false value so an existing trunk can remain deactivated.
    enabled: trunk?.enabled ?? true,
    name: trunk?.name ?? "",
    ip: trunk?.ip ?? "",
    port: String(trunk?.port ?? 5060),
    transport: trunk?.transport ?? "udp",
    prefix: trunk?.prefix ?? "",
    authenticationUsername: authentication?.username ?? "",
    authenticationPassword: authentication?.password ?? "",
    overwriteFrom: authentication?.overwrite_from ?? false,
    hostname: profile?.hostname ?? "",
    domain: profile?.domain ?? "",
    ruriDomain: profile?.ruri_domain ?? "",
    connectionTimeout:
      profile?.connection_timeout === undefined ||
      profile.connection_timeout === null
        ? ""
        : String(profile.connection_timeout),
    provisionalTimeout:
      profile?.provisional_timeout === undefined ||
      profile.provisional_timeout === null
        ? ""
        : String(profile.provisional_timeout),
  };
}

function parseOptionalPositiveInteger(
  rawValue: string,
  label: string,
): number | undefined {
  if (!rawValue.trim()) return undefined;
  const value = Number(rawValue);
  if (!Number.isInteger(value) || value < 1) {
    throw new Error(`${label} must be a positive whole number`);
  }
  return value;
}

export function CloudonixOutboundTrunkForm({
  configuration,
  onSaved,
}: CloudonixOutboundTrunkFormProps) {
  const { getAccessToken } = useAuth();
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState(() =>
    formStateFromConfiguration(configuration),
  );

  useEffect(() => {
    setForm(formStateFromConfiguration(configuration));
  }, [configuration]);

  const updateForm = <Key extends keyof OutboundTrunkFormState>(
    key: Key,
    value: OutboundTrunkFormState[Key],
  ) => setForm((current) => ({ ...current, [key]: value }));

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const credentials =
      configuration.credentials as Partial<CloudonixConfigurationResponse>;
    if (
      typeof credentials.bearer_token !== "string" ||
      typeof credentials.domain_id !== "string"
    ) {
      toast.error("Cloudonix credentials are unavailable");
      return;
    }

    const name = form.name.trim();
    const ip = form.ip.trim();
    if (form.enabled && (!name || !ip)) {
      toast.error("Trunk name and remote SIP address are required");
      return;
    }

    const username = form.authenticationUsername.trim();
    const password = form.authenticationPassword.trim();
    if (Boolean(username) !== Boolean(password)) {
      toast.error("Authentication username and password must be provided together");
      return;
    }

    try {
      const port = parseOptionalPositiveInteger(form.port, "Remote SIP port") ?? 5060;
      if (port > 65535) {
        throw new Error("Remote SIP port must be between 1 and 65535");
      }
      const connectionTimeout = parseOptionalPositiveInteger(
        form.connectionTimeout,
        "Connection timeout",
      );
      const provisionalTimeout = parseOptionalPositiveInteger(
        form.provisionalTimeout,
        "Provisional timeout",
      );

      const profile: CloudonixOutboundTrunkProfile = {};
      if (form.hostname.trim()) profile.hostname = form.hostname.trim();
      if (form.domain.trim()) profile.domain = form.domain.trim();
      if (form.ruriDomain.trim()) profile.ruri_domain = form.ruriDomain.trim();
      if (connectionTimeout !== undefined) {
        profile.connection_timeout = connectionTimeout;
      }
      if (provisionalTimeout !== undefined) {
        profile.provisional_timeout = provisionalTimeout;
      }
      if (username && password) {
        profile.authentication = {
          username,
          password,
          overwrite_from: form.overwriteFrom,
        };
      }

      const outboundTrunk: CloudonixOutboundTrunkConfiguration = {
        enabled: form.enabled,
        name: name || undefined,
        ip: ip || undefined,
        port,
        transport: form.transport,
        prefix: form.prefix.trim(),
        ...(Object.keys(profile).length > 0 ? { profile } : {}),
      };
      const config = {
        provider: "cloudonix",
        bearer_token: credentials.bearer_token,
        domain_id: credentials.domain_id,
        application_name: credentials.application_name,
        outbound_trunk: outboundTrunk,
      } satisfies CloudonixConfigurationRequest;

      setSubmitting(true);
      const token = await getAccessToken();
      const response =
        await updateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdPut(
          {
            headers: { Authorization: `Bearer ${token}` },
            path: { config_id: configuration.id },
            body: { config },
          },
        );
      if (response.error) {
        throw new Error(
          detailFromError(response.error, "Failed to save outbound SIP trunk"),
        );
      }
      if (!response.data) {
        throw new Error("Cloudonix did not return the updated configuration");
      }

      toast.success(
        form.enabled ? "Outbound SIP trunk saved" : "Outbound SIP trunk disabled",
      );
      await onSaved(response.data);
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "Failed to save outbound SIP trunk",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form className="space-y-5" onSubmit={handleSubmit}>
      <div className="flex items-center justify-between rounded-md border p-3">
        <div className="space-y-0.5">
          <Label htmlFor="cloudonix-trunk-enabled">Enable outbound trunk</Label>
          <p className="text-xs text-muted-foreground">
            When disabled, outbound calls are not pinned to this trunk.
          </p>
        </div>
        <Switch
          id="cloudonix-trunk-enabled"
          checked={form.enabled}
          onCheckedChange={(checked) => updateForm("enabled", checked)}
          disabled={submitting}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="cloudonix-trunk-name">Trunk Name</Label>
          <Input
            id="cloudonix-trunk-name"
            value={form.name}
            onChange={(event) => updateForm("name", event.target.value)}
            placeholder="e.g. dograh-carrier"
            disabled={submitting}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="cloudonix-trunk-ip">Remote SIP Address</Label>
          <Input
            id="cloudonix-trunk-ip"
            value={form.ip}
            onChange={(event) => updateForm("ip", event.target.value)}
            placeholder="sip.example.com"
            disabled={submitting}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="cloudonix-trunk-port">Remote SIP Port</Label>
          <Input
            id="cloudonix-trunk-port"
            type="number"
            min={1}
            max={65535}
            value={form.port}
            onChange={(event) => updateForm("port", event.target.value)}
            placeholder="5060"
            disabled={submitting}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="cloudonix-trunk-transport">Transport</Label>
          <Select
            value={form.transport}
            onValueChange={(value: Transport) => updateForm("transport", value)}
            disabled={submitting}
          >
            <SelectTrigger id="cloudonix-trunk-transport">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="udp">UDP</SelectItem>
              <SelectItem value="tcp">TCP</SelectItem>
              <SelectItem value="tls">TLS</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <CollapsibleTrigger asChild>
          <Button type="button" variant="ghost" size="sm" className="px-0">
            Advanced
            <ChevronDown
              className={`ml-2 h-4 w-4 transition-transform ${advancedOpen ? "rotate-180" : ""}`}
            />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-5 border-t pt-4">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="cloudonix-trunk-prefix">Technical Prefix</Label>
              <Input
                id="cloudonix-trunk-prefix"
                value={form.prefix}
                onChange={(event) => updateForm("prefix", event.target.value)}
                placeholder="Optional"
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="cloudonix-trunk-hostname">
                Cloudonix Border Gateway
              </Label>
              <Input
                id="cloudonix-trunk-hostname"
                value={form.hostname}
                onChange={(event) => updateForm("hostname", event.target.value)}
                placeholder="Optional"
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="cloudonix-trunk-domain">SIP To Domain</Label>
              <Input
                id="cloudonix-trunk-domain"
                value={form.domain}
                onChange={(event) => updateForm("domain", event.target.value)}
                placeholder="Optional"
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="cloudonix-trunk-ruri-domain">
                SIP Request-URI Domain
              </Label>
              <Input
                id="cloudonix-trunk-ruri-domain"
                value={form.ruriDomain}
                onChange={(event) => updateForm("ruriDomain", event.target.value)}
                placeholder="Optional"
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="cloudonix-trunk-connection-timeout">
                Connection Timeout (seconds)
              </Label>
              <Input
                id="cloudonix-trunk-connection-timeout"
                type="number"
                min={1}
                value={form.connectionTimeout}
                onChange={(event) =>
                  updateForm("connectionTimeout", event.target.value)
                }
                placeholder="10"
                disabled={submitting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="cloudonix-trunk-provisional-timeout">
                Provisional Timeout (seconds)
              </Label>
              <Input
                id="cloudonix-trunk-provisional-timeout"
                type="number"
                min={1}
                value={form.provisionalTimeout}
                onChange={(event) =>
                  updateForm("provisionalTimeout", event.target.value)
                }
                placeholder="2"
                disabled={submitting}
              />
            </div>
          </div>

          <div className="space-y-4 rounded-md border p-4">
            <div>
              <h4 className="text-sm font-medium">SIP authentication</h4>
              <p className="text-xs text-muted-foreground">
                Leave blank when the remote peer authenticates by IP address.
              </p>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="cloudonix-trunk-auth-username">Username</Label>
                <Input
                  id="cloudonix-trunk-auth-username"
                  value={form.authenticationUsername}
                  onChange={(event) =>
                    updateForm("authenticationUsername", event.target.value)
                  }
                  autoComplete="username"
                  disabled={submitting}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="cloudonix-trunk-auth-password">Password</Label>
                <Input
                  id="cloudonix-trunk-auth-password"
                  type="password"
                  value={form.authenticationPassword}
                  onChange={(event) =>
                    updateForm("authenticationPassword", event.target.value)
                  }
                  autoComplete="current-password"
                  disabled={submitting}
                />
              </div>
            </div>
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="cloudonix-trunk-overwrite-from">
                Use authentication username as caller ID
              </Label>
              <Switch
                id="cloudonix-trunk-overwrite-from"
                checked={form.overwriteFrom}
                onCheckedChange={(checked) =>
                  updateForm("overwriteFrom", checked)
                }
                disabled={
                  submitting ||
                  !form.authenticationUsername.trim() ||
                  !form.authenticationPassword.trim()
                }
              />
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>

      <div className="flex justify-end">
        <Button type="submit" disabled={submitting}>
          {submitting ? "Saving..." : "Save outbound trunk"}
        </Button>
      </div>
    </form>
  );
}
