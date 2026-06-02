"use client";

import { useState } from "react";
import { toast } from "sonner";

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

import { validateWorkEmail } from "./isPersonalEmail";
import {
  type LeadSource,
  TOPUP_COMPANY_SIZE_OPTIONS,
  TOPUP_VOLUME_OPTIONS,
  VOLUME_PRICING_GATE,
} from "./leadFieldOptions";
import { MathCaptcha } from "./MathCaptcha";
import { submitLead } from "./submitLead";

interface TopUpModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: LeadSource;
  onOpenEnterprise: () => void;
}

export function TopUpModal({ open, onOpenChange, source, onOpenEnterprise }: TopUpModalProps) {
  const [credits, setCredits] = useState("");
  const [useCase, setUseCase] = useState("");
  const [volume, setVolume] = useState("");
  const [workEmail, setWorkEmail] = useState("");
  const [company, setCompany] = useState("");
  const [companySize, setCompanySize] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [captchaValid, setCaptchaValid] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const wantsVolumePricing = volume === VOLUME_PRICING_GATE;

  const reset = () => {
    setCredits(""); setUseCase(""); setVolume(""); setWorkEmail("");
    setCompany(""); setCompanySize(""); setEmailError(null);
    setCaptchaValid(false); setSubmitting(false);
  };

  const handleSubmit = async () => {
    if (!credits.trim() || !useCase.trim() || !volume) {
      toast.error("Please fill in all required fields");
      return;
    }
    if (wantsVolumePricing) {
      const err = validateWorkEmail(workEmail);
      if (err) { setEmailError(err); return; }
      if (!company.trim() || !companySize) {
        toast.error("Please complete the volume-pricing details");
        return;
      }
    }
    if (!captchaValid) { toast.error("Please answer the quick check"); return; }

    setSubmitting(true);
    try {
      await submitLead({
        kind: "topup",
        source,
        payload: {
          credits, useCase, volume, wantsVolumePricing,
          ...(wantsVolumePricing ? { workEmail, company, companySize } : {}),
        },
      });
      toast.success("Thanks — we'll get your top-up sorted shortly.");
      reset();
      onOpenChange(false);
    } catch {
      toast.error("Something went wrong. Please try again.");
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Request a credit top-up</DialogTitle>
          <DialogDescription>
            Tell us how many credits you need and we&apos;ll sort you out.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="topup-credits">How many credits?</Label>
            <Input
              id="topup-credits"
              inputMode="numeric"
              value={credits}
              onChange={(e) => setCredits(e.target.value)}
              placeholder="e.g. 5000"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="topup-usecase">What&apos;s the use case?</Label>
            <Input id="topup-usecase" value={useCase} onChange={(e) => setUseCase(e.target.value)} />
          </div>

          <div className="space-y-1.5">
            <Label>Expected monthly call volume</Label>
            <Select value={volume} onValueChange={setVolume}>
              <SelectTrigger><SelectValue placeholder="Select volume" /></SelectTrigger>
              <SelectContent>
                {TOPUP_VOLUME_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {wantsVolumePricing && (
            <div className="space-y-3 rounded-md border-l-2 border-primary bg-muted/40 p-3">
              <p className="text-sm font-medium text-primary">Talk to us about volume pricing</p>

              <div className="space-y-1.5">
                <Label htmlFor="topup-email">Work email</Label>
                <Input
                  id="topup-email"
                  type="email"
                  value={workEmail}
                  onChange={(e) => { setWorkEmail(e.target.value); setEmailError(null); }}
                  placeholder="you@company.com"
                />
                {emailError && <p className="text-sm text-destructive">{emailError}</p>}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="topup-company">Company name</Label>
                <Input id="topup-company" value={company} onChange={(e) => setCompany(e.target.value)} />
              </div>

              <div className="space-y-1.5">
                <Label>Company size</Label>
                <Select value={companySize} onValueChange={setCompanySize}>
                  <SelectTrigger><SelectValue placeholder="Select size" /></SelectTrigger>
                  <SelectContent>
                    {TOPUP_COMPANY_SIZE_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <button
                type="button"
                onClick={onOpenEnterprise}
                className="text-sm text-muted-foreground underline decoration-dashed underline-offset-4 hover:text-foreground"
              >
                Need enterprise deployment? (SSO, on-prem, SOC2, data residency)
              </button>
            </div>
          )}

          <MathCaptcha id="topup-captcha" onValidChange={setCaptchaValid} />
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Submitting…" : "Submit request"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
