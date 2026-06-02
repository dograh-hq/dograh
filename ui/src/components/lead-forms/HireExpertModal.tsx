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
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import {
  HIRE_STAGE_OPTIONS,
  HIRE_TIMELINE_OPTIONS,
  HIRE_VOLUME_OPTIONS,
  type LeadSource,
} from "./leadFieldOptions";
import { MathCaptcha } from "./MathCaptcha";
import { submitLead } from "./submitLead";

interface HireExpertModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  source: LeadSource;
  onOpenEnterprise: () => void;
}

export function HireExpertModal({ open, onOpenChange, source, onOpenEnterprise }: HireExpertModalProps) {
  const [company, setCompany] = useState("");
  const [business, setBusiness] = useState("");
  const [agentGoal, setAgentGoal] = useState("");
  const [phone, setPhone] = useState("");
  const [timeline, setTimeline] = useState("");
  const [volume, setVolume] = useState("");
  const [hasScripts, setHasScripts] = useState("");
  const [stage, setStage] = useState("");
  const [captchaValid, setCaptchaValid] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const reset = () => {
    setCompany(""); setBusiness(""); setAgentGoal(""); setPhone("");
    setTimeline(""); setVolume(""); setHasScripts(""); setStage("");
    setCaptchaValid(false); setSubmitting(false);
  };

  const handleSubmit = async () => {
    if (!company.trim() || !business.trim() || !timeline || !volume || !hasScripts || !stage) {
      toast.error("Please fill in all required fields");
      return;
    }
    if (!captchaValid) { toast.error("Please answer the quick check"); return; }

    setSubmitting(true);
    try {
      await submitLead({
        kind: "hire_expert",
        source,
        payload: { company, business, agentGoal, phone, timeline, volume, hasScripts, stage },
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
    <Dialog open={open} onOpenChange={(o) => { if (!o) reset(); onOpenChange(o); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Let us build your voice agent</DialogTitle>
          <DialogDescription>
            Building good voice agents is nuanced. Tell us what you need and we&apos;ll take it end-to-end.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="hire-company">Company name</Label>
            <Input id="hire-company" value={company} onChange={(e) => setCompany(e.target.value)} />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="hire-business">What does your business do? <span className="text-muted-foreground">(1 line)</span></Label>
            <Input id="hire-business" value={business} onChange={(e) => setBusiness(e.target.value)} />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="hire-goal">
              What do you want the agent to do? <span className="text-muted-foreground">(optional)</span>
            </Label>
            <Textarea
              id="hire-goal"
              value={agentGoal}
              onChange={(e) => setAgentGoal(e.target.value)}
              placeholder="Use case and any remarks…"
              rows={3}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="hire-phone">Phone / WhatsApp <span className="text-muted-foreground">(optional)</span></Label>
            <Input id="hire-phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Timeline</Label>
              <Select value={timeline} onValueChange={setTimeline}>
                <SelectTrigger><SelectValue placeholder="Select" /></SelectTrigger>
                <SelectContent>
                  {HIRE_TIMELINE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
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

          <div className="space-y-1.5">
            <Label>Existing call scripts or workflows to share?</Label>
            <RadioGroup value={hasScripts} onValueChange={setHasScripts} className="flex gap-6">
              <div className="flex items-center gap-2">
                <RadioGroupItem value="yes" id="scripts-yes" />
                <Label htmlFor="scripts-yes" className="font-normal">Yes</Label>
              </div>
              <div className="flex items-center gap-2">
                <RadioGroupItem value="no" id="scripts-no" />
                <Label htmlFor="scripts-no" className="font-normal">No</Label>
              </div>
            </RadioGroup>
          </div>

          <div className="space-y-1.5">
            <Label>Current stage</Label>
            <Select value={stage} onValueChange={setStage}>
              <SelectTrigger><SelectValue placeholder="Select your current stage" /></SelectTrigger>
              <SelectContent>
                {HIRE_STAGE_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <MathCaptcha id="hire-captcha" onValidChange={setCaptchaValid} />
        </div>

        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={submitting}>
            {submitting ? "Submitting…" : "Submit"}
          </Button>
        </div>

        <div className="mt-3 border-t pt-3">
          <button
            type="button"
            onClick={onOpenEnterprise}
            className="text-sm text-muted-foreground underline decoration-dashed underline-offset-4 hover:text-foreground"
          >
            Need enterprise deployment? (SSO, on-prem, SOC2, data residency)
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
