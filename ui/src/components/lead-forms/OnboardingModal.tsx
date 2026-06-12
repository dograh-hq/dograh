"use client";

import { Rocket } from "lucide-react";
import { useState } from "react";
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
import { useAuth } from "@/lib/auth";

import { CaptchaChallenge } from "./CaptchaChallenge";
import {
  EMPTY_ENTERPRISE_FIELDS,
  type EnterpriseFieldsValue,
  EnterpriseLeadFields,
} from "./EnterpriseLeadFields";
import { validateWorkEmail } from "./isPersonalEmail";
import {
  ONBOARDING_ONPREM_OPTIONS,
  ONBOARDING_ONPREM_PERSONAS,
  ONBOARDING_PERSONA_OPTIONS,
  ONBOARDING_USAGE_CONTEXT_OPTIONS,
} from "./leadFieldOptions";
import { LeadModalShell } from "./LeadModalShell";
import { submitLead } from "./submitLead";
import { type OnboardingAnswers, skipOnboarding, submitOnboarding } from "./submitOnboarding";

interface OnboardingModalProps {
  open: boolean;
  // Called after a tracked outcome (submit or skip) to dismiss the gate.
  onComplete: () => void;
}

export function OnboardingModal({ open, onComplete }: OnboardingModalProps) {
  const { getAccessToken } = useAuth(); // Dograh token for the onboarding service
  const [companyName, setCompanyName] = useState("");
  const [usageContext, setUsageContext] = useState("");
  const [persona, setPersona] = useState("");
  const [onPremNeed, setOnPremNeed] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Inline on-prem expansion: the FULL enterprise form, submitted through the
  // same /api/v1/leads/enterprise path as the standalone Enterprise modal.
  const [onPremExpanded, setOnPremExpanded] = useState(false);
  const [ef, setEf] = useState<EnterpriseFieldsValue>(EMPTY_ENTERPRISE_FIELDS);
  const [efEmailError, setEfEmailError] = useState<string | null>(null);
  const [captchaActive, setCaptchaActive] = useState(false);

  const showOnPrem = ONBOARDING_ONPREM_PERSONAS.includes(persona);
  const showManagedNote = showOnPrem && onPremNeed === "yes";
  const wantsOnPrem = showManagedNote && onPremExpanded;

  const answers = (): OnboardingAnswers => ({
    companyName: companyName.trim() || undefined,
    usageContext: usageContext || undefined,
    persona: persona || undefined,
    onPremNeed: showOnPrem ? onPremNeed || undefined : undefined,
  });

  const onEfChange = (patch: Partial<EnterpriseFieldsValue>) => {
    setEf((v) => ({ ...v, ...patch }));
    if ("workEmail" in patch) setEfEmailError(null);
  };

  const expandOnPrem = () => {
    setOnPremExpanded(true);
    // Seed company from what we already collected (don't clobber edits).
    setEf((v) => (v.company ? v : { ...v, company: companyName.trim() }));
  };

  const collapseOnPrem = () => {
    setOnPremExpanded(false);
    setCaptchaActive(false);
    setEfEmailError(null);
  };

  // Best-effort persistence must never trap the user behind this hard gate.
  // Dismiss immediately, then fire the token + network work in the background.
  const finish = (skipped: boolean, withEnterprise: boolean) => {
    if (submitting) return;
    setSubmitting(true);
    const data = answers();
    const efSnapshot = withEnterprise ? { ...ef } : null;
    onComplete();
    void (async () => {
      const token = await getAccessToken().catch(() => undefined);
      try {
        if (skipped) await skipOnboarding(data, token);
        else await submitOnboarding(data, token);
        // Two distinct submissions on success: onboarding answers above, and the
        // enterprise on-prem lead here (same endpoint as the standalone form).
        if (efSnapshot) {
          await submitLead({
            kind: "enterprise",
            source: "onboarding",
            payload: {
              name: efSnapshot.name,
              company: efSnapshot.company || companyName.trim() || undefined,
              jobTitle: efSnapshot.jobTitle,
              workEmail: efSnapshot.workEmail,
              phone: efSnapshot.phone,
              volume: efSnapshot.volume,
              // They already answered on-prem = yes; deployment intent is implied.
              deployment: "yes",
              agentGoal: efSnapshot.agentGoal,
            },
            token,
          });
        }
      } catch {
        // Swallowed — the user is already in the product; network calls are
        // bounded by a timeout in onboardingServiceClient.
      }
    })();
  };

  const handleSubmit = () => {
    // Onboarding answers are all optional, so we only gate on the enterprise
    // fields when the user has actually engaged the on-prem section.
    if (wantsOnPrem) {
      const err = validateWorkEmail(ef.workEmail);
      if (err) { setEfEmailError(err); return; }
      if (!ef.name.trim() || !ef.company.trim() || !ef.jobTitle.trim() || !ef.phone.trim() || !ef.volume) {
        toast.error("Please complete the on-prem details below, or remove that section.");
        return;
      }
      // Pop the anti-spam check on top of the modal before sending the lead.
      setCaptchaActive(true);
      return;
    }
    finish(false, false);
  };

  // Runs once the captcha popup is verified (on-prem path).
  const submitWithOnPrem = () => {
    setCaptchaActive(false);
    finish(false, true);
  };

  const handleSkip = () => finish(true, false);

  return (
    <LeadModalShell
      open={open}
      // Hard gate: no outside/escape close, hide the built-in ×. The only exits
      // are Skip or Get started.
      onOpenChange={() => {}}
      contentProps={{
        className: "[&>button]:hidden",
        onEscapeKeyDown: (e) => e.preventDefault(),
        onPointerDownOutside: (e) => e.preventDefault(),
        onInteractOutside: (e) => e.preventDefault(),
      }}
      icon={Rocket}
      eyebrow="Welcome"
      title="Welcome to Dograh"
      description="A few quick questions so we can tailor your experience. Takes ~20 seconds."
      primary={{ label: "Get started", onClick: handleSubmit, disabled: submitting }}
      secondary={{ label: "Skip for now", onClick: handleSkip, disabled: submitting }}
      overlay={captchaActive ? <CaptchaChallenge onVerified={submitWithOnPrem} onCancel={() => setCaptchaActive(false)} /> : undefined}
    >
      <div className="grid gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="ob-company">
            Company name <span className="text-muted-foreground">(optional)</span>
          </Label>
          <Input id="ob-company" placeholder="Acme Inc." value={companyName} onChange={(e) => setCompanyName(e.target.value)} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ob-usage">Where do you plan to use this?</Label>
          <Select value={usageContext} onValueChange={setUsageContext}>
            <SelectTrigger id="ob-usage"><SelectValue placeholder="Select one" /></SelectTrigger>
            <SelectContent>
              {ONBOARDING_USAGE_CONTEXT_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ob-persona">What best describes you?</Label>
          <Select
            value={persona}
            onValueChange={(v) => {
              setPersona(v);
              // Leaving the on-prem-eligible persona resets the conditional answer
              // and any inline enterprise lead.
              if (!ONBOARDING_ONPREM_PERSONAS.includes(v)) {
                setOnPremNeed("");
                collapseOnPrem();
              }
            }}
          >
            <SelectTrigger id="ob-persona"><SelectValue placeholder="Select one" /></SelectTrigger>
            <SelectContent>
              {ONBOARDING_PERSONA_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {showOnPrem && (
          <div className="space-y-1.5">
            <Label htmlFor="ob-onprem">Do you need on-prem deployment for compliance &amp; data residency?</Label>
            <Select
              value={onPremNeed}
              onValueChange={(v) => {
                setOnPremNeed(v);
                if (v !== "yes") collapseOnPrem();
              }}
            >
              <SelectTrigger id="ob-onprem"><SelectValue placeholder="Select one" /></SelectTrigger>
              <SelectContent>
                {ONBOARDING_ONPREM_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>

            {showManagedNote && (
              <div className="mt-2 space-y-3 rounded-lg border border-border/60 bg-muted/30 p-3">
                <div className="flex items-start justify-between gap-2">
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    We offer a <span className="font-medium text-foreground">Managed On-Prem</span> deployment
                    for compliance and data residency.
                  </p>
                  {onPremExpanded && (
                    <button
                      type="button"
                      onClick={collapseOnPrem}
                      className="shrink-0 text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                    >
                      Remove
                    </button>
                  )}
                </div>

                {!onPremExpanded ? (
                  <button
                    type="button"
                    onClick={expandOnPrem}
                    className="text-xs font-medium text-cta underline-offset-4 hover:underline"
                  >
                    Talk to us about on-prem →
                  </button>
                ) : (
                  <div className="space-y-3">
                    <EnterpriseLeadFields
                      idPrefix="ob-op"
                      value={ef}
                      onChange={onEfChange}
                      showDeployment={false}
                      emailError={efEmailError}
                    />
                    <p className="text-[0.7rem] text-muted-foreground">
                      Our team will reach out about on-prem. Prefer not to? Click &ldquo;Remove&rdquo;.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </LeadModalShell>
  );
}
