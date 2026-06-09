"use client";

import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

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
import { useAuth } from "@/lib/auth";

import { FormTrustLine } from "./FormTrustLine";
import { validateWorkEmail } from "./isPersonalEmail";
import {
  ENTERPRISE_DEPLOYMENT_OPTIONS,
  ENTERPRISE_DEPLOYMENT_SOURCES,
  ENTERPRISE_VOLUME_OPTIONS,
  type LeadSource,
} from "./leadFieldOptions";
import { LeadModalShell } from "./LeadModalShell";
import { MathCaptcha } from "./MathCaptcha";
import { PhoneField } from "./PhoneField";
import { submitLead } from "./submitLead";

interface EnterpriseModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: LeadSource;
  // Optional values to pre-fill when the modal opens (e.g. company name already
  // collected in the onboarding form). Backward-compatible: omitted = no prefill.
  prefill?: { company?: string };
}

export function EnterpriseModal({ open, onOpenChange, source, prefill }: EnterpriseModalProps) {
  const { getAccessToken, isAuthenticated } = useAuth();  // Dograh token for the onboarding service
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [workEmail, setWorkEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [volume, setVolume] = useState("");
  const [deployment, setDeployment] = useState("");
  const [agentGoal, setAgentGoal] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [captchaValid, setCaptchaValid] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // The deployment question is only surfaced for custom-volume / Contact-Us /
  // pricing-custom-volume entry points; elsewhere it is hidden and the payload
  // defaults to "yes".
  const showDeployment = ENTERPRISE_DEPLOYMENT_SOURCES.includes(source);
  // Work email is mandatory only when the visitor is logged out (we already
  // have the email for authenticated users via their Dograh token).
  const workEmailRequired = !isAuthenticated;

  const reset = () => {
    setName(""); setCompany(""); setJobTitle(""); setWorkEmail("");
    setPhone(""); setVolume(""); setDeployment(""); setAgentGoal("");
    setEmailError(null); setCaptchaValid(false); setSubmitting(false);
  };

  // Seed reusable fields from prefill when the modal opens, so we don't re-ask
  // for info already captured upstream (e.g. company name from onboarding).
  const prefillCompany = prefill?.company;
  useEffect(() => {
    if (open && prefillCompany) {
      setCompany((prev) => prev || prefillCompany);
    }
  }, [open, prefillCompany]);

  const canSubmit =
    Boolean(name.trim()) &&
    Boolean(company.trim()) &&
    Boolean(phone.trim()) &&
    Boolean(volume) &&
    (!workEmailRequired || Boolean(workEmail.trim())) &&
    captchaValid &&
    !submitting;

  const handleSubmit = async () => {
    if (workEmailRequired || workEmail.trim()) {
      const err = validateWorkEmail(workEmail);
      if (err) { setEmailError(err); return; }
    }
    if (!name.trim() || !company.trim() || !phone.trim() || !volume) {
      toast.error("Please fill in all required fields");
      return;
    }
    if (!captchaValid) { toast.error("Please answer the quick check"); return; }

    setSubmitting(true);
    try {
      // Resolve the token best-effort; submission still succeeds via PostHog if it fails.
      const token = await getAccessToken().catch(() => undefined);
      await submitLead({
        kind: "enterprise",
        source,
        payload: {
          name,
          company,
          jobTitle,
          workEmail,
          phone,
          volume,
          // Hidden entry points imply enterprise intent — default to "yes".
          deployment: showDeployment ? deployment || "yes" : "yes",
          agentGoal,
        },
        token,
      });
      toast.success("Thanks — our team will reach out about enterprise deployment.");
      reset();
      onOpenChange(false);
    } catch {
      toast.error("Something went wrong. Please try again.");
      setSubmitting(false);
    }
  };

  return (
    <LeadModalShell
      open={open}
      onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}
      icon={ShieldCheck}
      eyebrow="Enterprise"
      title="Talk to our team"
      description="SSO, on-prem, data residency, committed volume. Tell us about your environment."
      primary={{ label: "Submit", onClick: handleSubmit, disabled: !canSubmit, loading: submitting }}
      secondary={{ label: "Cancel", onClick: () => onOpenChange(false), disabled: submitting }}
      trustLine={<FormTrustLine />}
    >
      <div className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ent-name">Name</Label>
            <Input id="ent-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ent-company">Company name</Label>
            <Input id="ent-company" value={company} onChange={(e) => setCompany(e.target.value)} />
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ent-title">
              Job title <span className="text-muted-foreground">(optional)</span>
            </Label>
            <Input id="ent-title" value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ent-email">
              Work email{!workEmailRequired && <span className="text-muted-foreground"> (optional)</span>}
            </Label>
            <Input
              id="ent-email"
              type="email"
              value={workEmail}
              onChange={(e) => { setWorkEmail(e.target.value); setEmailError(null); }}
              placeholder="you@company.com"
            />
            {emailError && <p className="text-sm text-destructive">{emailError}</p>}
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ent-phone">Phone</Label>
            <PhoneField id="ent-phone" value={phone} onChange={setPhone} required />
          </div>
          <div className="space-y-1.5">
            <Label>Monthly call volume</Label>
            <Select value={volume} onValueChange={setVolume}>
              <SelectTrigger><SelectValue placeholder="Select" /></SelectTrigger>
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
            <Label>Need enterprise deployment (SSO, on-prem, data residency)?</Label>
            <Select value={deployment} onValueChange={setDeployment}>
              <SelectTrigger><SelectValue placeholder="Select" /></SelectTrigger>
              <SelectContent>
                {ENTERPRISE_DEPLOYMENT_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="ent-goal">
            What do you want the voice agent to do? <span className="text-muted-foreground">(optional)</span>
          </Label>
          <Textarea
            id="ent-goal"
            value={agentGoal}
            onChange={(e) => setAgentGoal(e.target.value)}
            placeholder="Use case, regulatory context, current stack…"
            rows={3}
          />
        </div>

        <MathCaptcha id="ent-captcha" onValidChange={setCaptchaValid} />
      </div>
    </LeadModalShell>
  );
}
