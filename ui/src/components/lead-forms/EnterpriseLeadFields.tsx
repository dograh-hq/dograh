"use client";

// Shared enterprise lead fields, rendered by BOTH the standalone EnterpriseModal
// and the inline on-prem expansion of the onboarding form. One source of truth so
// the two stay identical and submit through the same /api/v1/leads/enterprise
// path. Controlled: the parent owns the values + the submit/captcha flow.

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import {
  ENTERPRISE_DEPLOYMENT_OPTIONS,
  ENTERPRISE_VOLUME_OPTIONS,
} from "./leadFieldOptions";
import { PhoneField } from "./PhoneField";

export interface EnterpriseFieldsValue {
  name: string;
  company: string;
  jobTitle: string;
  workEmail: string;
  phone: string;
  volume: string;
  deployment: string;
  agentGoal: string;
}

export const EMPTY_ENTERPRISE_FIELDS: EnterpriseFieldsValue = {
  name: "",
  company: "",
  jobTitle: "",
  workEmail: "",
  phone: "",
  volume: "",
  deployment: "",
  agentGoal: "",
};

interface EnterpriseLeadFieldsProps {
  // Unique prefix for input ids/labels (e.g. "ent", "ob-op") so the two
  // instances never collide when both exist in the DOM.
  idPrefix: string;
  value: EnterpriseFieldsValue;
  onChange: (patch: Partial<EnterpriseFieldsValue>) => void;
  // The deployment question is surfaced only for certain entry points; elsewhere
  // it is hidden and the caller defaults the payload to "yes".
  showDeployment: boolean;
  emailError?: string | null;
}

export function EnterpriseLeadFields({
  idPrefix: p,
  value,
  onChange,
  showDeployment,
  emailError,
}: EnterpriseLeadFieldsProps) {
  return (
    <div className="grid gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${p}-name`}>Name</Label>
          <Input id={`${p}-name`} placeholder="Your full name" value={value.name} onChange={(e) => onChange({ name: e.target.value })} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${p}-company`}>Company name</Label>
          <Input id={`${p}-company`} placeholder="Acme Inc." value={value.company} onChange={(e) => onChange({ company: e.target.value })} />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${p}-title`}>Job title</Label>
          <Input id={`${p}-title`} placeholder="VP Operations" value={value.jobTitle} onChange={(e) => onChange({ jobTitle: e.target.value })} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${p}-email`}>Work email</Label>
          <Input
            id={`${p}-email`}
            type="email"
            placeholder="you@company.com"
            value={value.workEmail}
            onChange={(e) => onChange({ workEmail: e.target.value })}
          />
          {emailError && <p className="text-sm text-destructive">{emailError}</p>}
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${p}-phone`}>Phone</Label>
          <PhoneField id={`${p}-phone`} value={value.phone} onChange={(phone) => onChange({ phone })} required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${p}-volume`}>Monthly call volume</Label>
          <Select value={value.volume} onValueChange={(v) => onChange({ volume: v })}>
            <SelectTrigger id={`${p}-volume`}><SelectValue placeholder="Select" /></SelectTrigger>
            <SelectContent>
              {ENTERPRISE_VOLUME_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {showDeployment && (
        <div className="space-y-1.5">
          <Label htmlFor={`${p}-deployment`}>Need enterprise deployment (SSO, on-prem, data residency)?</Label>
          <Select value={value.deployment} onValueChange={(v) => onChange({ deployment: v })}>
            <SelectTrigger id={`${p}-deployment`}><SelectValue placeholder="Select" /></SelectTrigger>
            <SelectContent>
              {ENTERPRISE_DEPLOYMENT_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      <div className="space-y-1.5">
        <Label htmlFor={`${p}-goal`}>
          What do you want the voice agent to do? <span className="text-muted-foreground">(optional)</span>
        </Label>
        <Textarea
          id={`${p}-goal`}
          value={value.agentGoal}
          onChange={(e) => onChange({ agentGoal: e.target.value })}
          placeholder="Use case, regulatory context, current stack…"
          rows={3}
        />
      </div>
    </div>
  );
}
