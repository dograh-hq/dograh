"use client";

import { Sparkles } from "lucide-react";
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
import { useAuth } from "@/lib/auth";

import { FormTrustLine } from "./FormTrustLine";
import { HIRE_VOLUME_OPTIONS, type LeadSource } from "./leadFieldOptions";
import { LeadModalShell } from "./LeadModalShell";
import { MathCaptcha } from "./MathCaptcha";
import { PhoneField } from "./PhoneField";
import { submitLead } from "./submitLead";

interface HireExpertModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: LeadSource;
  onOpenEnterprise: () => void;
}

export function HireExpertModal({ open, onOpenChange, source, onOpenEnterprise }: HireExpertModalProps) {
  const { getAccessToken } = useAuth();  // Dograh token for the onboarding service
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [agentGoal, setAgentGoal] = useState("");
  const [phone, setPhone] = useState("");
  const [volume, setVolume] = useState("");
  const [captchaValid, setCaptchaValid] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setName(""); setCompany(""); setJobTitle(""); setAgentGoal("");
    setPhone(""); setVolume(""); setCaptchaValid(false); setSubmitting(false);
  };

  const canSubmit =
    Boolean(name.trim()) &&
    Boolean(company.trim()) &&
    Boolean(agentGoal.trim()) &&
    Boolean(phone.trim()) &&
    Boolean(volume) &&
    captchaValid &&
    !submitting;

  const handleSubmit = async () => {
    if (!name.trim() || !company.trim() || !agentGoal.trim() || !phone.trim() || !volume) {
      toast.error("Please fill in all required fields");
      return;
    }
    if (!captchaValid) { toast.error("Please answer the quick check"); return; }

    setSubmitting(true);
    try {
      // Resolve the token best-effort; submission still succeeds via PostHog if it fails.
      const token = await getAccessToken().catch(() => undefined);
      await submitLead({
        kind: "hire_expert",
        source,
        payload: { name, company, jobTitle, agentGoal, phone, volume },
        token,
      });
      toast.success("Thanks — we'll reach out about building your agent.");
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
      icon={Sparkles}
      eyebrow="Done-for-you"
      title="Let us build your voice agent"
      description="Building good voice agents is nuanced. Tell us what you need and we'll take it end-to-end."
      primary={{ label: "Submit", onClick: handleSubmit, disabled: !canSubmit, loading: submitting }}
      secondary={{ label: "Cancel", onClick: () => onOpenChange(false), disabled: submitting }}
      helper={
        <button
          type="button"
          onClick={onOpenEnterprise}
          className="underline decoration-dashed underline-offset-4 hover:text-foreground"
        >
          Need enterprise deployment? (SSO, on-prem, data residency)
        </button>
      }
      trustLine={<FormTrustLine />}
    >
      <div className="grid gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="hire-name">Name</Label>
            <Input id="hire-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="hire-company">Company name</Label>
            <Input id="hire-company" value={company} onChange={(e) => setCompany(e.target.value)} />
          </div>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="hire-title">
            Job title <span className="text-muted-foreground">(optional)</span>
          </Label>
          <Input id="hire-title" value={jobTitle} onChange={(e) => setJobTitle(e.target.value)} />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="hire-goal">What do you want the voice agent to do?</Label>
          <Textarea
            id="hire-goal"
            value={agentGoal}
            onChange={(e) => setAgentGoal(e.target.value)}
            placeholder="Use case, target outcomes, any remarks…"
            rows={3}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="hire-phone">Phone</Label>
            <PhoneField id="hire-phone" value={phone} onChange={setPhone} required />
          </div>
          <div className="space-y-1.5">
            <Label>Expected monthly call volume</Label>
            <Select value={volume} onValueChange={setVolume}>
              <SelectTrigger><SelectValue placeholder="Select" /></SelectTrigger>
              <SelectContent>
                {HIRE_VOLUME_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <MathCaptcha id="hire-captcha" onValidChange={setCaptchaValid} />
      </div>
    </LeadModalShell>
  );
}
