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
import { Textarea } from "@/components/ui/textarea";
import { useAppConfig } from "@/context/AppConfigContext";
import { useAuth } from "@/lib/auth";

import { CaptchaChallenge } from "./CaptchaChallenge";
import {
  EMPTY_ENTERPRISE_FIELDS,
  type EnterpriseFieldsValue,
  EnterpriseLeadFields,
} from "./EnterpriseLeadFields";
import { validateWorkEmail } from "./isPersonalEmail";
import {
  ONBOARDING_HEARD_OPTIONS,
  ONBOARDING_MIGRATION_OPTIONS,
  ONBOARDING_ONPREM_OPTIONS,
  ONBOARDING_ONPREM_PERSONAS,
  ONBOARDING_PERSONA_OPTIONS,
  ONBOARDING_VOLUME_OPTIONS,
} from "./leadFieldOptions";
import { LeadModalShell } from "./LeadModalShell";
import { submitLead } from "./submitLead";
import { type OnboardingAnswers, submitOnboarding } from "./submitOnboarding";

interface OnboardingModalProps {
  open: boolean;
  // Called after a tracked submit to dismiss the gate and stamp the server-side
  // "completed" flag. Onboarding is compulsory — `skipped` is always false now.
  onComplete: (skipped: boolean) => void;
}

export function OnboardingModal({ open, onComplete }: OnboardingModalProps) {
  const { user } = useAuth(); // logged-in identity → onboarding email (sent silently)
  const { config } = useAppConfig();
  // Deployment provenance (analytics only).
  const origin = config?.deploymentMode === "cloud" ? "cloud_app" : "oss_app";
  // The logged-in user's email (Stack uses primaryEmail; local uses email). Sent in the
  // body — there is no visible email field on the onboarding form.
  const userEmail = user ? ("primaryEmail" in user ? user.primaryEmail ?? "" : user.email ?? "") : "";

  const [persona, setPersona] = useState("");
  const [onPremNeed, setOnPremNeed] = useState("");
  const [migratingFrom, setMigratingFrom] = useState("");
  const [migratingOtherProvider, setMigratingOtherProvider] = useState("");
  const [switchReason, setSwitchReason] = useState("");
  const [howHeard, setHowHeard] = useState("");
  const [volume, setVolume] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Inline on-prem expansion: the FULL enterprise form, submitted through the same
  // /api/v1/leads/enterprise path as the standalone Enterprise modal.
  const [onPremExpanded, setOnPremExpanded] = useState(false);
  const [ef, setEf] = useState<EnterpriseFieldsValue>(EMPTY_ENTERPRISE_FIELDS);
  const [efEmailError, setEfEmailError] = useState<string | null>(null);
  const [captchaActive, setCaptchaActive] = useState(false);

  const showOnPrem = ONBOARDING_ONPREM_PERSONAS.includes(persona);
  const showManagedNote = showOnPrem && onPremNeed === "yes";
  const wantsOnPrem = showManagedNote && onPremExpanded;
  const isOtherProvider = migratingFrom === "other";
  const isMigrating = Boolean(migratingFrom) && migratingFrom !== "no";

  // All four questions are required (onboarding is compulsory). "Other" provider also
  // needs its free-text name; the "why switching" note is optional.
  const baseValid =
    Boolean(persona) &&
    Boolean(migratingFrom) &&
    (!isOtherProvider || Boolean(migratingOtherProvider.trim())) &&
    Boolean(howHeard) &&
    Boolean(volume);
  const canSubmit = baseValid && !submitting;

  const answers = (): OnboardingAnswers => ({
    persona: persona || undefined,
    onPremNeed: showOnPrem ? onPremNeed || undefined : undefined,
    migratingFrom: migratingFrom || undefined,
    migratingOtherProvider: isOtherProvider ? migratingOtherProvider.trim() || undefined : undefined,
    switchReason: isMigrating ? switchReason.trim() || undefined : undefined,
    howHeard: howHeard || undefined,
    volume: volume || undefined,
  });

  const onEfChange = (patch: Partial<EnterpriseFieldsValue>) => {
    setEf((v) => ({ ...v, ...patch }));
    if ("workEmail" in patch) setEfEmailError(null);
  };

  const expandOnPrem = () => setOnPremExpanded(true);

  const collapseOnPrem = () => {
    setOnPremExpanded(false);
    setCaptchaActive(false);
    setEfEmailError(null);
  };

  // Best-effort persistence must never trap the user. Dismiss immediately, then fire
  // the network work in the background. `withEnterprise` = also send the on-prem lead.
  const finish = (withEnterprise: boolean) => {
    if (submitting) return;
    setSubmitting(true);
    const data = answers();
    const efSnapshot = withEnterprise ? { ...ef } : null;
    onComplete(false); // compulsory — always "completed", never skipped
    void (async () => {
      try {
        await submitOnboarding(data, origin, userEmail);
        // Two distinct submissions on success: onboarding answers above, and the
        // enterprise on-prem lead here (same endpoint as the standalone form).
        if (efSnapshot) {
          await submitLead({
            kind: "enterprise",
            source: "onboarding",
            origin,
            payload: {
              name: efSnapshot.name,
              company: efSnapshot.company || undefined,
              jobTitle: efSnapshot.jobTitle,
              workEmail: efSnapshot.workEmail,
              phone: efSnapshot.phone,
              volume: efSnapshot.volume,
              // They already answered on-prem = yes; deployment intent is implied.
              deployment: "yes",
              agentGoal: efSnapshot.agentGoal,
            },
          });
          // Only the on-prem/enterprise lead path sends an email; plain onboarding
          // does not. Confirm the email just for this path.
          toast.success("Check your inbox - we just emailed you the next steps (give it a minute).");
        }
      } catch {
        // Swallowed — the user is already in the product; calls are timeout-bounded.
      }
    })();
  };

  const handleSubmit = () => {
    if (!baseValid) {
      toast.error(
        isOtherProvider && !migratingOtherProvider.trim()
          ? "Please tell us which provider you're migrating from"
          : "Please answer all the questions",
      );
      return;
    }
    // If the user engaged the on-prem section, validate it + pop the anti-spam check.
    if (wantsOnPrem) {
      const err = validateWorkEmail(ef.workEmail);
      if (err) { setEfEmailError(err); return; }
      if (!ef.name.trim() || !ef.company.trim() || !ef.jobTitle.trim() || !ef.phone.trim() || !ef.volume) {
        toast.error("Please complete the on-prem details below, or remove that section.");
        return;
      }
      setCaptchaActive(true);
      return;
    }
    finish(false);
  };

  // Runs once the captcha popup is verified (on-prem path).
  const submitWithOnPrem = () => {
    setCaptchaActive(false);
    finish(true);
  };

  return (
    <LeadModalShell
      open={open}
      // Hard gate: no outside/escape close, hide the built-in ×. Onboarding is
      // compulsory — the only exit is "Get started" once the questions are answered.
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
      primary={{ label: "Get started", onClick: handleSubmit, disabled: !canSubmit, loading: submitting }}
      overlay={captchaActive ? <CaptchaChallenge onVerified={submitWithOnPrem} onCancel={() => setCaptchaActive(false)} /> : undefined}
    >
      <div className="grid gap-4">
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

        <div className="space-y-1.5">
          <Label htmlFor="ob-volume">Expected monthly call volume</Label>
          <Select value={volume} onValueChange={setVolume}>
            <SelectTrigger id="ob-volume"><SelectValue placeholder="Select one" /></SelectTrigger>
            <SelectContent>
              {ONBOARDING_VOLUME_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ob-migrating">Are you migrating from another provider?</Label>
          <Select
            value={migratingFrom}
            onValueChange={(v) => {
              setMigratingFrom(v);
              if (v !== "other") setMigratingOtherProvider("");
              if (v === "no") setSwitchReason("");
            }}
          >
            <SelectTrigger id="ob-migrating"><SelectValue placeholder="Select one" /></SelectTrigger>
            <SelectContent>
              {ONBOARDING_MIGRATION_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          {isOtherProvider && (
            <div className="mt-2 space-y-1.5">
              <Label htmlFor="ob-other-provider">Other provider</Label>
              <Input
                id="ob-other-provider"
                placeholder="Enter the provider here"
                value={migratingOtherProvider}
                onChange={(e) => setMigratingOtherProvider(e.target.value)}
              />
            </div>
          )}

          {isMigrating && (
            <div className="mt-2 space-y-1.5">
              <Label htmlFor="ob-switch-reason">
                Why are you switching? <span className="text-muted-foreground">(optional)</span>
              </Label>
              <Textarea
                id="ob-switch-reason"
                rows={2}
                placeholder="e.g. cost, self-hosting, concurrency, data security, latency"
                value={switchReason}
                onChange={(e) => setSwitchReason(e.target.value)}
              />
            </div>
          )}
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="ob-heard">How did you hear about us?</Label>
          <Select value={howHeard} onValueChange={setHowHeard}>
            <SelectTrigger id="ob-heard"><SelectValue placeholder="Select one" /></SelectTrigger>
            <SelectContent>
              {ONBOARDING_HEARD_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </LeadModalShell>
  );
}
