"use client";

import type { ComponentType } from "react";

import type { TelephonyConfigurationDetail } from "@/client/types.gen";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/**
 * Per-provider pieces of the trunk editor.
 *
 * A trunk itself is provider-agnostic — a name, an on/off switch and the
 * numbers pinned to it — so `TrunkCard` renders all of that generically. What
 * differs is `settings`, which the backend validates against that provider's
 * `ProviderSpec.trunk_settings_cls`. A provider with no entry here still gets
 * a working card with name and enabled; add an entry only when its trunks
 * carry settings the customer has to fill in.
 */

export interface TrunkSettings {
  [key: string]: unknown;
}

export interface TrunkSettingsFieldsProps {
  configuration: TelephonyConfigurationDetail;
  settings: TrunkSettings;
  onChange: (patch: TrunkSettings) => void;
  disabled?: boolean;
}

export interface TrunkProviderUi {
  /** Settings a brand-new trunk starts with. */
  initialSettings: (configuration: TelephonyConfigurationDetail) => TrunkSettings;
  /** Extra form fields, rendered under the name and enabled controls. */
  Fields?: ComponentType<TrunkSettingsFieldsProps>;
  /** Mirrors the provider's server-side trunk schema; message or null. */
  validate?: (name: string, settings: TrunkSettings) => string | null;
  /** One line under the trunk name in the list. */
  summarize?: (settings: TrunkSettings) => string;
}

// Mirrors the trunk-name rule the Cloudonix config schema enforces server-side.
const CLOUDONIX_TRUNK_NAME_PATTERN = /^[A-Za-z0-9-]+$/;

function regionsOf(configuration: TelephonyConfigurationDetail) {
  return configuration.sip_connectivity?.regions ?? [];
}

function CloudonixTrunkFields({
  configuration,
  settings,
  onChange,
  disabled,
}: TrunkSettingsFieldsProps) {
  const regions = regionsOf(configuration);
  const region = typeof settings.region === "string" ? settings.region : "";
  const sipDomain = typeof settings.sip_domain === "string" ? settings.sip_domain : "";
  const originIp = regions.find((candidate) => candidate.region === region)
    ?.outbound_origin_ip;

  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="trunk-sip-domain">SIP domain</Label>
        <Input
          id="trunk-sip-domain"
          value={sipDomain}
          onChange={(event) => onChange({ sip_domain: event.target.value })}
          placeholder="sip.example.com"
          disabled={disabled}
        />
        <p className="text-xs text-muted-foreground">
          Your carrier or PBX. Used for both the SIP To header and the
          Request-URI.
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="trunk-region">Region</Label>
        <Select
          value={region}
          onValueChange={(next) => onChange({ region: next })}
          disabled={disabled}
        >
          <SelectTrigger id="trunk-region" aria-label="Trunk region">
            <SelectValue placeholder="Select a region" />
          </SelectTrigger>
          <SelectContent>
            {regions.map((candidate) => (
              <SelectItem key={candidate.region} value={candidate.region}>
                {candidate.region}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          Sets the remote peer Dograh dials for this trunk.
          {originIp
            ? ` Calls leave from ${originIp} — allow it on your side.`
            : ""}
        </p>
      </div>
    </>
  );
}

const CLOUDONIX: TrunkProviderUi = {
  initialSettings: (configuration) => {
    const regions = regionsOf(configuration);
    const preferred =
      regions.find((candidate) => candidate.region.toLowerCase() === "global") ??
      regions[0];
    return { region: preferred?.region ?? "", sip_domain: "" };
  },
  Fields: CloudonixTrunkFields,
  validate: (name, settings) => {
    if (!CLOUDONIX_TRUNK_NAME_PATTERN.test(name)) {
      return "Trunk name may only contain letters, digits and hyphens";
    }
    if (!settings.sip_domain) return "SIP domain is required";
    if (!settings.region) return "Region is required";
    return null;
  },
  summarize: (settings) =>
    [settings.sip_domain, settings.region].filter(Boolean).join(" · "),
};

const FALLBACK: TrunkProviderUi = {
  initialSettings: () => ({}),
};

const TRUNK_PROVIDER_UI: Record<string, TrunkProviderUi> = {
  cloudonix: CLOUDONIX,
};

export function trunkProviderUi(provider: string): TrunkProviderUi {
  return TRUNK_PROVIDER_UI[provider] ?? FALLBACK;
}
