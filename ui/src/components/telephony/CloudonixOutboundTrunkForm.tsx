"use client";

import { type FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";

import { updateTelephonyConfigurationApiV1OrganizationsTelephonyConfigsConfigIdPut } from "@/client/sdk.gen";
import type {
  CloudonixConfigurationRequest,
  CloudonixConfigurationResponse,
  CloudonixOutboundTrunkConfiguration,
  TelephonyConfigurationDetail,
} from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

// Mirrors the trunk-name rule the Cloudonix config schema enforces server-side.
const TRUNK_NAME_PATTERN = /^[A-Za-z0-9-]+$/;

interface OutboundTrunkFormState {
  /** Empty until the backend mints one on first save. */
  id: string;
  enabled: boolean;
  name: string;
  sipDomain: string;
}

interface CloudonixOutboundTrunkFormProps {
  configuration: TelephonyConfigurationDetail;
  /** Region selected above; the trunk's remote peer is derived from it. */
  region: string;
  onSaved: (
    configuration: TelephonyConfigurationDetail,
  ) => void | Promise<void>;
}

function formStateFromConfiguration(
  configuration: TelephonyConfigurationDetail,
): OutboundTrunkFormState {
  const credentials =
    configuration.credentials as Partial<CloudonixConfigurationResponse>;
  // Storage holds a list so more trunks can be added later; this form edits the
  // first one and round-trips its id so the backend updates rather than
  // re-creates the Cloudonix trunk behind it.
  const trunk = credentials.outbound_trunks?.[0];

  return {
    id: trunk?.id ?? "",
    // A new trunk is enabled when the user first saves the form. Preserve an
    // explicit false value so an existing trunk can remain deactivated.
    enabled: trunk?.enabled ?? true,
    name: trunk?.name ?? "",
    sipDomain: trunk?.sip_domain ?? "",
  };
}

export function CloudonixOutboundTrunkForm({
  configuration,
  region,
  onSaved,
}: CloudonixOutboundTrunkFormProps) {
  const { getAccessToken } = useAuth();
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
    const sipDomain = form.sipDomain.trim();
    if (form.enabled && (!name || !sipDomain)) {
      toast.error("Trunk name and SIP domain are required");
      return;
    }
    if (name && !TRUNK_NAME_PATTERN.test(name)) {
      toast.error("Trunk name may only contain letters, digits and hyphens");
      return;
    }

    try {
      const outboundTrunk: CloudonixOutboundTrunkConfiguration = {
        ...(form.id ? { id: form.id } : {}),
        enabled: form.enabled,
        name: name || undefined,
        region,
        sip_domain: sipDomain || undefined,
      };
      const config = {
        provider: "cloudonix",
        bearer_token: credentials.bearer_token,
        domain_id: credentials.domain_id,
        application_name: credentials.application_name,
        // Trunks beyond the first are preserved as stored; this form owns one.
        outbound_trunks: [
          outboundTrunk,
          ...(credentials.outbound_trunks ?? []).slice(1),
        ],
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
          <p className="text-xs text-muted-foreground">
            Letters, digits and hyphens only.
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="cloudonix-trunk-sip-domain">SIP Domain</Label>
          <Input
            id="cloudonix-trunk-sip-domain"
            value={form.sipDomain}
            onChange={(event) => updateForm("sipDomain", event.target.value)}
            placeholder="sip.example.com"
            disabled={submitting}
          />
          <p className="text-xs text-muted-foreground">
            Used for both the SIP To header and the Request-URI.
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          The remote peer follows the {region} region selected above.
        </p>
        <Button type="submit" disabled={submitting}>
          {submitting ? "Saving..." : "Save outbound trunk"}
        </Button>
      </div>
    </form>
  );
}
