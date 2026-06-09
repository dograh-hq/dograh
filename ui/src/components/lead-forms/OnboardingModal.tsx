"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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

import {
  ONBOARDING_ONPREM_OPTIONS,
  ONBOARDING_ONPREM_PERSONAS,
  ONBOARDING_PERSONA_OPTIONS,
  ONBOARDING_USAGE_CONTEXT_OPTIONS,
} from "./leadFieldOptions";
import { type OnboardingAnswers, skipOnboarding, submitOnboarding } from "./submitOnboarding";

interface OnboardingModalProps {
  open: boolean;
  // Called after a tracked outcome (submit or skip) to dismiss the gate.
  onComplete: () => void;
  // Opens the existing EnterpriseModal, prefilled with what we already collected.
  onOpenEnterprise: (prefill: { company?: string }) => void;
}

export function OnboardingModal({ open, onComplete, onOpenEnterprise }: OnboardingModalProps) {
  const { getAccessToken } = useAuth();  // Dograh token for the onboarding service
  const [companyName, setCompanyName] = useState("");
  const [usageContext, setUsageContext] = useState("");
  const [persona, setPersona] = useState("");
  const [onPremNeed, setOnPremNeed] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const showOnPrem = ONBOARDING_ONPREM_PERSONAS.includes(persona);
  const showManagedNote = showOnPrem && onPremNeed === "yes";

  const answers = (): OnboardingAnswers => ({
    companyName: companyName.trim() || undefined,
    usageContext: usageContext || undefined,
    persona: persona || undefined,
    onPremNeed: showOnPrem ? onPremNeed || undefined : undefined,
  });

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      const token = await getAccessToken().catch(() => undefined);
      await submitOnboarding(answers(), token);
      onComplete();
    } catch {
      // Submission is best-effort. Never block the user from reaching the
      // product — treat a failure as complete.
      onComplete();
    }
  };

  const handleSkip = async () => {
    // Skipping is itself signal — capture whatever was filled.
    try {
      const token = await getAccessToken().catch(() => undefined);
      await skipOnboarding(answers(), token);
    } finally {
      onComplete();
    }
  };

  return (
    <Dialog open={open}>
      <DialogContent
        // No tracked-outcome-free exits: block Escape, outside-click, and hide
        // the built-in close (×). The only ways out are Skip or Get started.
        className="max-w-md [&>button]:hidden"
        onEscapeKeyDown={(e) => e.preventDefault()}
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Welcome to Dograh</DialogTitle>
          <DialogDescription>
            A few quick questions so we can tailor your experience. Takes ~20 seconds.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="ob-company">
              Company name <span className="text-muted-foreground">(optional)</span>
            </Label>
            <Input id="ob-company" value={companyName} onChange={(e) => setCompanyName(e.target.value)} />
          </div>

          <div className="space-y-1.5">
            <Label>Where do you plan to use this?</Label>
            <Select value={usageContext} onValueChange={setUsageContext}>
              <SelectTrigger><SelectValue placeholder="Select one" /></SelectTrigger>
              <SelectContent>
                {ONBOARDING_USAGE_CONTEXT_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label>What best describes you?</Label>
            <Select
              value={persona}
              onValueChange={(v) => {
                setPersona(v);
                // Reset the conditional answer if they leave the on-prem-eligible persona.
                if (!ONBOARDING_ONPREM_PERSONAS.includes(v)) setOnPremNeed("");
              }}
            >
              <SelectTrigger><SelectValue placeholder="Select one" /></SelectTrigger>
              <SelectContent>
                {ONBOARDING_PERSONA_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {showOnPrem && (
            <div className="space-y-1.5 rounded-md border-l-2 border-primary bg-muted/40 p-3">
              <Label>Do you need on-prem deployment for compliance &amp; data residency?</Label>
              <Select value={onPremNeed} onValueChange={setOnPremNeed}>
                <SelectTrigger><SelectValue placeholder="Select one" /></SelectTrigger>
                <SelectContent>
                  {ONBOARDING_ONPREM_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {showManagedNote && (
                <div className="mt-2 space-y-2">
                  <p className="text-sm text-muted-foreground">
                    We provide a <span className="font-medium text-foreground">Managed On-Prem solution</span> for
                    enterprises to ensure compliance and data security. Share your contact and our team will reach out.
                  </p>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => onOpenEnterprise({ company: companyName.trim() || undefined })}
                  >
                    Talk to us about on-prem
                  </Button>
                  <p className="text-xs text-muted-foreground">Optional — you can skip and continue.</p>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2">
          <Button variant="ghost" onClick={handleSkip} disabled={submitting}>
            Skip for now
          </Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Saving…" : "Get started"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
