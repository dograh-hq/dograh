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
import { Textarea } from "@/components/ui/textarea";

import { validateWorkEmail } from "./isPersonalEmail";
import {
  ENTERPRISE_COMPANY_SIZE_OPTIONS,
  ENTERPRISE_INDUSTRY_OPTIONS,
  ENTERPRISE_TIMELINE_OPTIONS,
  type LeadSource,
} from "./leadFieldOptions";
import { MathCaptcha } from "./MathCaptcha";
import { submitLead } from "./submitLead";

interface EnterpriseModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: LeadSource;
}

export function EnterpriseModal({ open, onOpenChange, source }: EnterpriseModalProps) {
  const [company, setCompany] = useState("");
  const [industry, setIndustry] = useState("");
  const [companySize, setCompanySize] = useState("");
  const [timeline, setTimeline] = useState("");
  const [workEmail, setWorkEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [notes, setNotes] = useState("");
  const [emailError, setEmailError] = useState<string | null>(null);
  const [captchaValid, setCaptchaValid] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setCompany(""); setIndustry(""); setCompanySize(""); setTimeline("");
    setWorkEmail(""); setPhone(""); setNotes(""); setEmailError(null);
    setCaptchaValid(false); setSubmitting(false);
  };

  const handleSubmit = async () => {
    const err = validateWorkEmail(workEmail);
    if (err) { setEmailError(err); return; }
    if (!company.trim() || !industry || !companySize || !timeline) {
      toast.error("Please fill in all required fields");
      return;
    }
    if (!captchaValid) { toast.error("Please answer the quick check"); return; }

    setSubmitting(true);
    try {
      await submitLead({
        kind: "enterprise",
        source,
        payload: { company, industry, companySize, timeline, workEmail, phone, notes },
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
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Enterprise deployment</DialogTitle>
          <DialogDescription>
            SSO, on-prem, SOC2, data residency. Tell us about your environment.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="ent-company">Company name</Label>
            <Input id="ent-company" value={company} onChange={(e) => setCompany(e.target.value)} />
          </div>

          <div className="space-y-1.5">
            <Label>Industry</Label>
            <Select value={industry} onValueChange={setIndustry}>
              <SelectTrigger><SelectValue placeholder="Select industry" /></SelectTrigger>
              <SelectContent>
                {ENTERPRISE_INDUSTRY_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label>Company size</Label>
            <Select value={companySize} onValueChange={setCompanySize}>
              <SelectTrigger><SelectValue placeholder="Select size" /></SelectTrigger>
              <SelectContent>
                {ENTERPRISE_COMPANY_SIZE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label>Timeline</Label>
            <Select value={timeline} onValueChange={setTimeline}>
              <SelectTrigger><SelectValue placeholder="Select timeline" /></SelectTrigger>
              <SelectContent>
                {ENTERPRISE_TIMELINE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="ent-email">Work email</Label>
            <Input
              id="ent-email"
              type="email"
              value={workEmail}
              onChange={(e) => { setWorkEmail(e.target.value); setEmailError(null); }}
              placeholder="you@company.com"
            />
            {emailError && <p className="text-sm text-destructive">{emailError}</p>}
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="ent-phone">Phone <span className="text-muted-foreground">(optional)</span></Label>
            <Input id="ent-phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="ent-notes">
              Anything else we should know? <span className="text-muted-foreground">(optional)</span>
            </Label>
            <Textarea
              id="ent-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Regulatory context, urgency, current stack…"
              rows={3}
            />
          </div>

          <div className="sm:col-span-2">
            <MathCaptcha id="ent-captcha" onValidChange={setCaptchaValid} />
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Submitting…" : "Submit"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
