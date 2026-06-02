# User Onboarding & Lead-Gen Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add lead-gen surfaces to the Dograh UI — rename the sidebar OBSERVE section to MANAGE with a new Credits & Billing link, move the Dograh Model Credits card to a new /billing page with Top-up + Hire-an-Expert CTAs, three intake modals (Top-up, Hire-an-Expert, Enterprise) with an inline math captcha, and a delayed Hire-an-Expert nudge on the workflow builder — all firing PostHog events.

**Architecture:** Frontend-only (`ui/`). A single `LeadFormsProvider` context (mounted in `AppLayout`) renders the three modals once and exposes `openHireExpert/openTopUp/openEnterprise(source)` plus an `everOpenedHire` flag. All triggers call the hook — no duplicate modal mounts, no prop-drilling. Form submission goes through one `submitLead()` seam that fires PostHog today and will gain a MongoDB POST later. The credits card is *extracted* from `usage/page.tsx` into a reusable component (not rewritten).

**Tech Stack:** Next.js 15 App Router, React 19, TypeScript, Tailwind, shadcn/ui primitives (`dialog`, `select`, `textarea`, `radio-group`, `input`, `label`, `button`, `card`, `progress`, `tooltip` — all already present), `posthog-js` with names in `src/constants/posthog-events.ts`, `sonner` toasts, `lucide-react` icons.

**Branch:** `feat/user-onboarding` (already checked out).

**Working directory for all commands:** `/Users/pk/Documents/WORK Tech/dograh-oss-repo/dograh/ui`

**Verification commands (no test runner for UI components in this repo — verification is type-check + lint + manual):**
- Type-check: `npx tsc --noEmit`
- Lint (per ui/AGENTS.md): `npm run fix-lint` (auto-fixes) then re-run to confirm clean
- Manual dogfood at the end (see final task)

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/constants/posthog-events.ts` | Central PostHog event-name registry | Modify (append 10 events) |
| `src/components/lead-forms/leadFieldOptions.ts` | Shared dropdown option arrays + types | Create |
| `src/components/lead-forms/isPersonalEmail.ts` | Free-domain blocklist helper | Create |
| `src/components/lead-forms/submitLead.ts` | Single submit seam (PostHog now, API later) | Create |
| `src/components/lead-forms/MathCaptcha.tsx` | Inline math captcha field | Create |
| `src/components/lead-forms/TopUpModal.tsx` | Top-up dialog with >20k volume gate | Create |
| `src/components/lead-forms/HireExpertModal.tsx` | Hire-an-Expert dialog | Create |
| `src/components/lead-forms/EnterpriseModal.tsx` | Enterprise intake dialog | Create |
| `src/components/lead-forms/HireExpertNudge.tsx` | Delayed builder nudge banner | Create |
| `src/context/LeadFormsContext.tsx` | Shared modal state provider | Create |
| `src/components/layout/AppLayout.tsx` | Mount the provider | Modify |
| `src/components/billing/DograhCreditsCard.tsx` | Extracted credits card + footer CTAs | Create |
| `src/app/billing/page.tsx` | New Credits & Billing page | Create |
| `src/app/usage/page.tsx` | Remove credits card + its now-unused state | Modify |
| `src/components/layout/AppSidebar.tsx` | OBSERVE→MANAGE, new link, footer button | Modify |
| `src/app/workflow/[workflowId]/RenderWorkflow.tsx` | Mount the nudge | Modify |

---

## Task 1: PostHog event names

**Files:**
- Modify: `src/constants/posthog-events.ts`

- [ ] **Step 1: Append the 10 new event names**

Edit `src/constants/posthog-events.ts` — add these keys inside the `PostHogEvent` object, after the existing `SLACK_COMMUNITY_CLICKED` line and before the closing `} as const;`:

```ts
  HIRE_EXPERT_OPENED: "hire_expert_opened",
  HIRE_EXPERT_SUBMITTED: "hire_expert_submitted",
  TOPUP_REQUEST_OPENED: "topup_request_opened",
  TOPUP_REQUESTED: "topup_requested",
  ENTERPRISE_LEAD_OPENED: "enterprise_lead_opened",
  ENTERPRISE_LEAD_SUBMITTED: "enterprise_lead_submitted",
  HIRE_NUDGE_SHOWN: "hire_nudge_shown",
  HIRE_NUDGE_CLICKED: "hire_nudge_clicked",
  HIRE_NUDGE_DISMISSED: "hire_nudge_dismissed",
  HIRE_NUDGE_EXPIRED: "hire_nudge_expired",
```

- [ ] **Step 2: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors (the file is a plain const object; new keys are valid).

- [ ] **Step 3: Commit**

```bash
git add src/constants/posthog-events.ts
git commit -m "feat(lead-gen): register PostHog events for lead-gen surfaces"
```

---

## Task 2: Shared field options + helpers (leadFieldOptions, isPersonalEmail, submitLead)

**Files:**
- Create: `src/components/lead-forms/leadFieldOptions.ts`
- Create: `src/components/lead-forms/isPersonalEmail.ts`
- Create: `src/components/lead-forms/submitLead.ts`

- [ ] **Step 1: Create `leadFieldOptions.ts`**

```ts
// Shared dropdown options + lead source/kind types for the lead-gen forms.

export type LeadSource =
  | "sidebar"
  | "billing_card"
  | "builder_nudge"
  | "topup"
  | "hire_expert";

export type LeadKind = "topup" | "hire_expert" | "enterprise";

// Top-up: expected monthly call volume. ">20k" unlocks the volume-pricing block.
export const TOPUP_VOLUME_OPTIONS = [
  { value: "0-5k", label: "0–5k calls/month" },
  { value: "5k-20k", label: "5k–20k calls/month" },
  { value: ">20k", label: ">20k calls/month" },
] as const;

// The value that gates the volume-pricing qualifier block.
export const VOLUME_PRICING_GATE = ">20k";

// Top-up volume-pricing qualifier: company size (small-business scale).
export const TOPUP_COMPANY_SIZE_OPTIONS = [
  { value: "only_me", label: "Only me" },
  { value: "2-10", label: "2–10" },
  { value: "10-100", label: "10–100" },
  { value: "100-1000", label: "100–1000" },
  { value: "1000+", label: "1000+" },
] as const;

// Hire-an-Expert timeline.
export const HIRE_TIMELINE_OPTIONS = [
  { value: "asap", label: "ASAP" },
  { value: "2-4_weeks", label: "2–4 weeks" },
  { value: "1-2_months", label: "1–2 months" },
  { value: "flexible", label: "Flexible" },
  { value: "exploring", label: "Exploring" },
] as const;

// Hire-an-Expert expected monthly call volume.
export const HIRE_VOLUME_OPTIONS = [
  { value: "0-5k", label: "0–5k" },
  { value: "5k-100k", label: "5k–100k" },
  { value: "100k+", label: "100k+" },
  { value: "not_sure", label: "Not sure" },
] as const;

// Hire-an-Expert current stage.
export const HIRE_STAGE_OPTIONS = [
  { value: "live_process", label: "Have a live process we want to automate" },
  { value: "idea_no_process", label: "Have an idea, no process yet" },
  { value: "researching", label: "Just researching" },
  { value: "built_need_help", label: "Already built something, need help fixing" },
] as const;

// Enterprise industry.
export const ENTERPRISE_INDUSTRY_OPTIONS = [
  { value: "financial_services", label: "Financial services" },
  { value: "healthcare", label: "Healthcare" },
  { value: "insurance", label: "Insurance" },
  { value: "government", label: "Government" },
  { value: "telecom", label: "Telecom" },
  { value: "bpo", label: "BPO" },
  { value: "other", label: "Other" },
] as const;

// Enterprise company size (enterprise scale — intentionally different from top-up's).
export const ENTERPRISE_COMPANY_SIZE_OPTIONS = [
  { value: "50-200", label: "50–200" },
  { value: "200-1000", label: "200–1000" },
  { value: "1000-5000", label: "1000–5000" },
  { value: "5000+", label: "5000+" },
] as const;

// Enterprise timeline.
export const ENTERPRISE_TIMELINE_OPTIONS = [
  { value: "this_quarter", label: "This quarter" },
  { value: "next_quarter", label: "Next quarter" },
  { value: "6_months", label: "6 months" },
  { value: "exploring", label: "Exploring" },
] as const;
```

- [ ] **Step 2: Create `isPersonalEmail.ts`**

```ts
// Returns true if the email uses a common free/personal domain.
// Used to gate "work email" fields on lead forms.

const PERSONAL_EMAIL_DOMAINS = new Set([
  "gmail.com",
  "googlemail.com",
  "yahoo.com",
  "yahoo.co.in",
  "yahoo.co.uk",
  "ymail.com",
  "outlook.com",
  "hotmail.com",
  "hotmail.co.uk",
  "live.com",
  "msn.com",
  "icloud.com",
  "me.com",
  "mac.com",
  "proton.me",
  "protonmail.com",
  "pm.me",
  "aol.com",
  "gmx.com",
  "gmx.net",
  "mail.com",
  "zoho.com",
  "yandex.com",
  "fastmail.com",
]);

export function isValidEmail(email: string): boolean {
  // Pragmatic check — not RFC-perfect, but rejects obvious garbage.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}

export function isPersonalEmail(email: string): boolean {
  const at = email.trim().toLowerCase().split("@");
  if (at.length !== 2) return false;
  return PERSONAL_EMAIL_DOMAINS.has(at[1]);
}

// Convenience validator for work-email fields.
// Returns an error string, or null if valid.
export function validateWorkEmail(email: string): string | null {
  if (!email.trim()) return "Work email is required";
  if (!isValidEmail(email)) return "Please enter a valid email address";
  if (isPersonalEmail(email)) return "Please use your work email";
  return null;
}
```

- [ ] **Step 3: Create `submitLead.ts`**

```ts
// Single submission seam for all lead forms.
// Today: fires a PostHog capture. Later: add a POST to the backend
// (MongoDB) endpoint here — no form component will need to change.

import posthog from "posthog-js";

import { PostHogEvent } from "@/constants/posthog-events";

import type { LeadKind, LeadSource } from "./leadFieldOptions";

const SUBMIT_EVENT: Record<LeadKind, string> = {
  topup: PostHogEvent.TOPUP_REQUESTED,
  hire_expert: PostHogEvent.HIRE_EXPERT_SUBMITTED,
  enterprise: PostHogEvent.ENTERPRISE_LEAD_SUBMITTED,
};

export interface SubmitLeadArgs {
  kind: LeadKind;
  source: LeadSource;
  // Field values, already validated by the caller. Non-sensitive lead data.
  payload: Record<string, unknown>;
}

export async function submitLead({ kind, source, payload }: SubmitLeadArgs): Promise<void> {
  // PostHog capture — the durable record until the backend endpoint lands.
  posthog.capture(SUBMIT_EVENT[kind], { source, ...payload });

  // FUTURE: when the MongoDB endpoint exists, POST here, e.g.
  //   const res = await submitLeadApiV1LeadsPost({ body: { kind, source, ...payload } });
  //   if (res.error) throw new Error(detailFromError(res.error, "Failed to submit"));
}
```

- [ ] **Step 4: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors. (Imports resolve; `PostHogEvent` keys exist from Task 1.)

- [ ] **Step 5: Commit**

```bash
git add src/components/lead-forms/leadFieldOptions.ts src/components/lead-forms/isPersonalEmail.ts src/components/lead-forms/submitLead.ts
git commit -m "feat(lead-gen): shared field options, work-email validation, and submit seam"
```

---

## Task 3: MathCaptcha component

**Files:**
- Create: `src/components/lead-forms/MathCaptcha.tsx`

- [ ] **Step 1: Create `MathCaptcha.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface MathCaptchaProps {
  // Called whenever validity changes, so the parent can enable/disable submit.
  onValidChange: (valid: boolean) => void;
  id?: string;
}

// Dead-simple anti-spam: "What is X + Y?". Generated client-side on mount.
// Math.random is allowed in browser runtime (this is not a workflow script).
export function MathCaptcha({ onValidChange, id = "math-captcha" }: MathCaptchaProps) {
  const [a, setA] = useState(0);
  const [b, setB] = useState(0);
  const [answer, setAnswer] = useState("");

  useEffect(() => {
    setA(Math.floor(Math.random() * 8) + 1);
    setB(Math.floor(Math.random() * 8) + 1);
  }, []);

  useEffect(() => {
    onValidChange(answer.trim() !== "" && parseInt(answer, 10) === a + b);
  }, [answer, a, b, onValidChange]);

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>
        Quick check: what is {a} + {b}?
      </Label>
      <Input
        id={id}
        inputMode="numeric"
        value={answer}
        onChange={(e) => setAnswer(e.target.value)}
        placeholder="Answer"
        className="w-32"
      />
    </div>
  );
}
```

- [ ] **Step 2: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/lead-forms/MathCaptcha.tsx
git commit -m "feat(lead-gen): inline math captcha field"
```

---

## Task 4: EnterpriseModal (built first — the other two link to it)

**Files:**
- Create: `src/components/lead-forms/EnterpriseModal.tsx`

- [ ] **Step 1: Create `EnterpriseModal.tsx`**

```tsx
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
```

- [ ] **Step 2: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/lead-forms/EnterpriseModal.tsx
git commit -m "feat(lead-gen): enterprise intake modal"
```

---

## Task 5: HireExpertModal

**Files:**
- Create: `src/components/lead-forms/HireExpertModal.tsx`

- [ ] **Step 1: Create `HireExpertModal.tsx`**

```tsx
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
```

- [ ] **Step 2: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/lead-forms/HireExpertModal.tsx
git commit -m "feat(lead-gen): hire-an-expert modal with enterprise link"
```

---

## Task 6: TopUpModal (with >20k volume gate)

**Files:**
- Create: `src/components/lead-forms/TopUpModal.tsx`

- [ ] **Step 1: Create `TopUpModal.tsx`**

```tsx
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
  TOPUP_COMPANY_SIZE_OPTIONS,
  TOPUP_VOLUME_OPTIONS,
  VOLUME_PRICING_GATE,
  type LeadSource,
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
```

- [ ] **Step 2: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/lead-forms/TopUpModal.tsx
git commit -m "feat(lead-gen): top-up modal with >20k volume-pricing gate"
```

---

## Task 7: LeadFormsContext provider

**Files:**
- Create: `src/context/LeadFormsContext.tsx`

- [ ] **Step 1: Create `LeadFormsContext.tsx`**

```tsx
"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";
import posthog from "posthog-js";

import { PostHogEvent } from "@/constants/posthog-events";

import { EnterpriseModal } from "@/components/lead-forms/EnterpriseModal";
import { HireExpertModal } from "@/components/lead-forms/HireExpertModal";
import { TopUpModal } from "@/components/lead-forms/TopUpModal";
import type { LeadSource } from "@/components/lead-forms/leadFieldOptions";

interface LeadFormsContextValue {
  openHireExpert: (source: LeadSource) => void;
  openTopUp: (source: LeadSource) => void;
  openEnterprise: (source: LeadSource) => void;
  // True once the hire modal has been opened this session (used to suppress the builder nudge).
  hasOpenedHireRef: React.MutableRefObject<boolean>;
}

const LeadFormsContext = createContext<LeadFormsContextValue | null>(null);

export function LeadFormsProvider({ children }: { children: ReactNode }) {
  const [hireOpen, setHireOpen] = useState(false);
  const [topUpOpen, setTopUpOpen] = useState(false);
  const [enterpriseOpen, setEnterpriseOpen] = useState(false);
  // Track the originating source so the *_OPENED and submit events agree.
  const [hireSource, setHireSource] = useState<LeadSource>("sidebar");
  const [topUpSource, setTopUpSource] = useState<LeadSource>("billing_card");
  const [enterpriseSource, setEnterpriseSource] = useState<LeadSource>("topup");
  const hasOpenedHireRef = useRef(false);

  const openHireExpert = useCallback((source: LeadSource) => {
    hasOpenedHireRef.current = true;
    setHireSource(source);
    setHireOpen(true);
    posthog.capture(PostHogEvent.HIRE_EXPERT_OPENED, { source });
  }, []);

  const openTopUp = useCallback((source: LeadSource) => {
    setTopUpSource(source);
    setTopUpOpen(true);
    posthog.capture(PostHogEvent.TOPUP_REQUEST_OPENED, { source });
  }, []);

  const openEnterprise = useCallback((source: LeadSource) => {
    setEnterpriseSource(source);
    setEnterpriseOpen(true);
    posthog.capture(PostHogEvent.ENTERPRISE_LEAD_OPENED, { source });
  }, []);

  const value = useMemo(
    () => ({ openHireExpert, openTopUp, openEnterprise, hasOpenedHireRef }),
    [openHireExpert, openTopUp, openEnterprise],
  );

  return (
    <LeadFormsContext.Provider value={value}>
      {children}
      <TopUpModal
        open={topUpOpen}
        onOpenChange={setTopUpOpen}
        source={topUpSource}
        onOpenEnterprise={() => openEnterprise("topup")}
      />
      <HireExpertModal
        open={hireOpen}
        onOpenChange={setHireOpen}
        source={hireSource}
        onOpenEnterprise={() => openEnterprise("hire_expert")}
      />
      <EnterpriseModal
        open={enterpriseOpen}
        onOpenChange={setEnterpriseOpen}
        source={enterpriseSource}
      />
    </LeadFormsContext.Provider>
  );
}

export function useLeadForms(): LeadFormsContextValue {
  const ctx = useContext(LeadFormsContext);
  if (!ctx) throw new Error("useLeadForms must be used within a LeadFormsProvider");
  return ctx;
}
```

- [ ] **Step 2: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors. (All three modals + `LeadSource` resolve.)

- [ ] **Step 3: Commit**

```bash
git add src/context/LeadFormsContext.tsx
git commit -m "feat(lead-gen): shared lead-forms context provider"
```

---

## Task 8: Mount the provider in AppLayout

**Files:**
- Modify: `src/components/layout/AppLayout.tsx`

- [ ] **Step 1: Add the import**

In `src/components/layout/AppLayout.tsx`, add to the import block (after the `useAppConfig` import on line 12):

```tsx
import { LeadFormsProvider } from "@/context/LeadFormsContext";
```

- [ ] **Step 2: Wrap the sidebar branch with the provider**

The provider must wrap the part of the tree where the triggers live (sidebar + main content). Wrap the contents of the `shouldShowSidebar` branch. Change this block (lines 113–146):

```tsx
      {shouldShowSidebar ? (
        <div className="flex min-h-screen w-full">
          <AppSidebar />
          <SidebarInset className="flex-1">
            <BackendStatusBanner />
            {!isWorkflowEditor && <AppHeader />}
```

to:

```tsx
      {shouldShowSidebar ? (
        <LeadFormsProvider>
          <div className="flex min-h-screen w-full">
            <AppSidebar />
            <SidebarInset className="flex-1">
              <BackendStatusBanner />
              {!isWorkflowEditor && <AppHeader />}
```

And close it — change the end of that branch (the `</div>` that closes `flex min-h-screen` then `) : (`) at lines 145–147:

```tsx
            </main>
          </SidebarInset>
        </div>
      ) : (
```

to:

```tsx
            </main>
          </SidebarInset>
          </div>
        </LeadFormsProvider>
      ) : (
```

(Note: indentation of the inner block shifts by one level. Re-indent the wrapped lines for cleanliness, or leave as-is — lint will normalize. Functionally only the added `<LeadFormsProvider>` open/close tags matter.)

- [ ] **Step 3: Verify type-check + lint**

Run: `npx tsc --noEmit && npm run fix-lint`
Expected: no type errors; lint auto-formats indentation. Re-run `npm run fix-lint` to confirm clean.

- [ ] **Step 4: Commit**

```bash
git add src/components/layout/AppLayout.tsx
git commit -m "feat(lead-gen): mount LeadFormsProvider in app layout"
```

---

## Task 9: Sidebar — MANAGE rename, Credits & Billing link, Hire-an-Expert footer button

**Files:**
- Modify: `src/components/layout/AppSidebar.tsx`

- [ ] **Step 1: Add icon import + useLeadForms import**

In `AppSidebar.tsx`, add `UserRound` to the existing `lucide-react` import (alphabetically near `TrendingUp`/`Wrench`). The icon import block (lines 4–24) gains one line:

```tsx
  UserRound,
```

Then add after the `useAuth` import (line 58):

```tsx
import { useLeadForms } from "@/context/LeadFormsContext";
```

- [ ] **Step 2: Rename OBSERVE → MANAGE and add the Credits & Billing item**

In `NAV_SECTIONS`, change the third section (lines 131–145). Replace:

```tsx
  {
    label: "OBSERVE",
    items: [
      {
        title: "Agent Runs",
        url: "/usage",
        icon: TrendingUp,
      },
      {
        title: "Reports",
        url: "/reports",
        icon: FileText,
      },
    ],
  },
```

with:

```tsx
  {
    label: "MANAGE",
    items: [
      {
        title: "Agent Runs",
        url: "/usage",
        icon: TrendingUp,
      },
      {
        title: "Reports",
        url: "/reports",
        icon: FileText,
      },
      {
        title: "Credits & Billing",
        url: "/billing",
        icon: CircleDollarSign,
      },
    ],
  },
```

(`CircleDollarSign` is already imported on line 11.)

- [ ] **Step 3: Get the openHireExpert handler inside the component**

Inside `AppSidebar()` (after line 161, near the other hooks), add:

```tsx
  const { openHireExpert } = useLeadForms();
```

- [ ] **Step 4: Add the Hire-an-Expert button to the footer avatar rows**

In `SidebarFooter`, the avatar lives in a row `<div className={cn("flex", isCollapsed ? "justify-center" : "justify-start")}>` for both the `provider !== "stack"` (line 370) and `provider === "stack"` (line 408) branches. For BOTH branches, change `justify-start` to `justify-between` and add the Hire button as a sibling of the `<DropdownMenu>`.

For the **`provider !== "stack"`** branch, change line 370 from:

```tsx
            <div className={cn("flex", isCollapsed ? "justify-center" : "justify-start")}>
              <DropdownMenu>
```

to:

```tsx
            <div className={cn("flex items-center", isCollapsed ? "flex-col gap-2" : "justify-between")}>
              <DropdownMenu>
```

Then immediately before that row's closing `</div>` (line 404, after `</DropdownMenu>`), insert the Hire button:

```tsx
              {isCollapsed ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      size="icon"
                      className="h-8 w-8"
                      onClick={() => openHireExpert("sidebar")}
                      aria-label="Hire an Expert"
                    >
                      <UserRound className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="right"><p>Hire an Expert</p></TooltipContent>
                </Tooltip>
              ) : (
                <Button size="sm" className="gap-2" onClick={() => openHireExpert("sidebar")}>
                  <UserRound className="h-4 w-4" />
                  Hire an Expert
                </Button>
              )}
```

For the **`provider === "stack"`** branch, make the identical change: line 408 `justify-start` → the same `items-center / flex-col gap-2 / justify-between` className, and insert the same Hire button block before that row's closing `</div>` (line 453, after its `</DropdownMenu>`).

- [ ] **Step 5: Verify type-check + lint**

Run: `npx tsc --noEmit && npm run fix-lint`
Expected: no errors. `UserRound`, `Button`, `Tooltip*` all already imported/used in this file.

- [ ] **Step 6: Commit**

```bash
git add src/components/layout/AppSidebar.tsx
git commit -m "feat(lead-gen): rename OBSERVE to MANAGE, add Credits & Billing link and Hire-an-Expert footer button"
```

---

## Task 10: Extract DograhCreditsCard + footer CTAs

**Files:**
- Create: `src/components/billing/DograhCreditsCard.tsx`

- [ ] **Step 1: Create `DograhCreditsCard.tsx`** (self-contained: owns its own fetch, identical to the current usage-page logic, plus the CTA footer)

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";

import { getMpsCreditsApiV1OrganizationsUsageMpsCreditsGet } from "@/client/sdk.gen";
import type { MpsCreditsResponse } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { useLeadForms } from "@/context/LeadFormsContext";
import { useAuth } from "@/lib/auth";
import { UserRound } from "lucide-react";

export function DograhCreditsCard() {
  const auth = useAuth();
  const { openHireExpert, openTopUp } = useLeadForms();
  const [mpsCredits, setMpsCredits] = useState<MpsCreditsResponse | null>(null);
  const [isLoadingCredits, setIsLoadingCredits] = useState(true);

  const fetchMpsCredits = useCallback(async () => {
    if (!auth.isAuthenticated) return;
    try {
      const response = await getMpsCreditsApiV1OrganizationsUsageMpsCreditsGet();
      if (response.data) {
        setMpsCredits(response.data);
      }
    } catch (error) {
      console.error("Failed to fetch MPS credits:", error);
    } finally {
      setIsLoadingCredits(false);
    }
  }, [auth.isAuthenticated]);

  useEffect(() => {
    if (auth.isAuthenticated) {
      fetchMpsCredits();
    }
  }, [auth.isAuthenticated, fetchMpsCredits]);

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>Dograh Model Credits</CardTitle>
        <CardDescription>
          These track usage of Dograh models using Dograh Service Keys.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoadingCredits ? (
          <div className="animate-pulse space-y-4">
            <div className="h-4 bg-muted rounded w-1/4"></div>
            <div className="h-8 bg-muted rounded"></div>
            <div className="h-4 bg-muted rounded w-1/3"></div>
          </div>
        ) : mpsCredits ? (
          <div className="space-y-4">
            <div className="flex justify-between items-baseline">
              <div>
                <p className="text-2xl font-bold">
                  {mpsCredits.total_credits_used.toFixed(2)}{" "}
                  <span className="text-lg font-normal text-muted-foreground">
                    / {mpsCredits.total_quota.toFixed(2)}
                  </span>
                </p>
                <p className="text-sm text-muted-foreground">Credits Used</p>
              </div>
              <div className="text-right">
                <p className="text-lg font-semibold">{mpsCredits.remaining_credits.toFixed(2)}</p>
                <p className="text-sm text-muted-foreground">Remaining</p>
              </div>
            </div>

            {mpsCredits.total_quota > 0 && (
              <Progress value={(mpsCredits.total_credits_used / mpsCredits.total_quota) * 100} className="h-3" />
            )}
          </div>
        ) : (
          <p className="text-muted-foreground">
            No Dograh service keys configured. Set up a service key in your model configuration to see usage.
          </p>
        )}

        {/* Footer CTAs — card ends with an action */}
        <div className="mt-6 flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
          <span className="text-sm text-muted-foreground">Running low?</span>
          <div className="flex flex-wrap gap-2 sm:justify-end">
            <Button variant="outline" className="gap-2" onClick={() => openHireExpert("billing_card")}>
              <UserRound className="h-4 w-4" />
              Hire an Expert
            </Button>
            <Button onClick={() => openTopUp("billing_card")}>Request top-up</Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors. (`getMpsCredits...`, `MpsCreditsResponse`, `useLeadForms` all resolve.)

- [ ] **Step 3: Commit**

```bash
git add src/components/billing/DograhCreditsCard.tsx
git commit -m "feat(lead-gen): extract DograhCreditsCard with top-up + hire CTAs"
```

---

## Task 11: New /billing page

**Files:**
- Create: `src/app/billing/page.tsx`

- [ ] **Step 1: Create `src/app/billing/page.tsx`**

```tsx
"use client";

import { DograhCreditsCard } from "@/components/billing/DograhCreditsCard";

export default function BillingPage() {
  return (
    <div className="container mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-3xl font-bold">Credits &amp; Billing</h1>
        <p className="text-muted-foreground">
          Track your Dograh model credits and request top-ups.
        </p>
      </div>
      <DograhCreditsCard />
    </div>
  );
}
```

- [ ] **Step 2: Verify type-check passes**

Run: `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Manual check** — start dev server if not running (`npm run dev`), open `http://localhost:3000/billing`. The credits card renders with the "Running low?" footer + both CTAs; clicking each opens the right modal.

- [ ] **Step 4: Commit**

```bash
git add src/app/billing/page.tsx
git commit -m "feat(lead-gen): add Credits & Billing page"
```

---

## Task 12: Remove the credits card from Agent Runs (usage page)

**Files:**
- Modify: `src/app/usage/page.tsx`

- [ ] **Step 1: Remove the MPS Credits Card JSX**

Delete the entire `{/* MPS Credits Card */}` block (lines 412–450 — the `<Card className="mb-6">…</Card>` that renders "Dograh Model Credits").

- [ ] **Step 2: Remove the now-unused credits state**

Delete these lines:
- Line 42–44: the `// MPS credits state` comment and the two `useState` declarations (`mpsCredits`, `isLoadingCredits`).
- Lines 79–92: the `// Fetch MPS credits` comment and the entire `fetchMpsCredits` `useCallback`.

- [ ] **Step 3: Remove the fetch call + dependency in the initial-load effect**

In the effect at lines 233–238, remove the `fetchMpsCredits();` call (line 235) and remove `fetchMpsCredits` from the dependency array (line 238). Result:

```tsx
    useEffect(() => {
        if (auth.isAuthenticated) {
            fetchUsageHistory(currentPage, appliedFilters);
        }
    }, [auth.isAuthenticated, currentPage, appliedFilters, fetchUsageHistory]);
```

- [ ] **Step 4: Remove now-unused imports**

- From the `@/client/sdk.gen` import (line 9), remove `getMpsCreditsApiV1OrganizationsUsageMpsCreditsGet`.
- From the `@/client/types.gen` import (line 10), remove `MpsCreditsResponse`.
- If `Progress` (line 18) is no longer referenced anywhere else in the file, remove its import. (Check: `grep -n "Progress" src/app/usage/page.tsx` — if the only hit was the deleted card, drop the import. If still used, keep it.)
- If `CardDescription` is no longer used elsewhere in the file, drop it from the `@/components/ui/card` import (line 17). (Same grep check.)

- [ ] **Step 5: Verify type-check + lint**

Run: `npx tsc --noEmit && npm run fix-lint`
Expected: no errors, no unused-variable warnings. The runs table, filters, daily-usage, and timezone selector are untouched.

- [ ] **Step 6: Manual check** — open `http://localhost:3000/usage` (Agent Runs). The Dograh Model Credits card is GONE; everything else (runs table, filters) still works.

- [ ] **Step 7: Commit**

```bash
git add src/app/usage/page.tsx
git commit -m "refactor(lead-gen): move Dograh Model Credits card out of Agent Runs to /billing"
```

---

## Task 13: HireExpertNudge on the workflow builder

**Files:**
- Create: `src/components/lead-forms/HireExpertNudge.tsx`
- Modify: `src/app/workflow/[workflowId]/RenderWorkflow.tsx`

- [ ] **Step 1: Create `HireExpertNudge.tsx`**

```tsx
"use client";

import { useEffect, useRef, useState } from "react";
import posthog from "posthog-js";
import { UserRound, X } from "lucide-react";

import { PostHogEvent } from "@/constants/posthog-events";
import { useLeadForms } from "@/context/LeadFormsContext";

interface HireExpertNudgeProps {
  workflowId: number;
}

// Timings. Override SHOW_DELAY_MS to a few seconds during manual testing.
const SHOW_DELAY_MS = 5 * 60 * 1000; // 5 minutes on the builder
const AUTO_FADE_MS = 30 * 1000; // visible for 30s

function nudgeDoneKey(workflowId: number) {
  return `dograh:hireNudge:${workflowId}`;
}

export function HireExpertNudge({ workflowId }: HireExpertNudgeProps) {
  const { openHireExpert, hasOpenedHireRef } = useLeadForms();
  const [visible, setVisible] = useState(false);
  const fadeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Arm the 5-minute show timer (once per mount / workflow).
  useEffect(() => {
    // Already shown+consumed for this workflow, or hire modal already opened → skip.
    if (typeof window === "undefined") return;
    if (localStorage.getItem(nudgeDoneKey(workflowId))) return;

    const showTimer = setTimeout(() => {
      if (hasOpenedHireRef.current) return; // they engaged elsewhere; don't nag
      if (localStorage.getItem(nudgeDoneKey(workflowId))) return;
      setVisible(true);
      posthog.capture(PostHogEvent.HIRE_NUDGE_SHOWN, { workflowId });
      // Auto-fade after 30s. Auto-expiry does NOT mark done (per spec).
      fadeTimer.current = setTimeout(() => {
        setVisible(false);
        posthog.capture(PostHogEvent.HIRE_NUDGE_EXPIRED, { workflowId });
      }, AUTO_FADE_MS);
    }, SHOW_DELAY_MS);

    return () => {
      clearTimeout(showTimer);
      if (fadeTimer.current) clearTimeout(fadeTimer.current);
    };
  }, [workflowId, hasOpenedHireRef]);

  if (!visible) return null;

  const markDone = () => {
    if (fadeTimer.current) clearTimeout(fadeTimer.current);
    localStorage.setItem(nudgeDoneKey(workflowId), "1");
    setVisible(false);
  };

  const handleClick = () => {
    posthog.capture(PostHogEvent.HIRE_NUDGE_CLICKED, { workflowId });
    markDone();
    openHireExpert("builder_nudge");
  };

  const handleDismiss = () => {
    posthog.capture(PostHogEvent.HIRE_NUDGE_DISMISSED, { workflowId });
    markDone();
  };

  return (
    <div className="fixed bottom-6 right-6 z-50 flex max-w-xs items-center gap-3 rounded-lg border border-primary bg-background p-3 shadow-lg animate-in fade-in slide-in-from-bottom-2">
      <button type="button" onClick={handleClick} className="flex flex-1 items-center gap-3 text-left">
        <UserRound className="h-5 w-5 shrink-0 text-primary" />
        <span>
          <span className="block text-sm font-semibold">Hire an Expert</span>
          <span className="block text-xs text-muted-foreground">We&apos;ll build your agent for you</span>
        </span>
      </button>
      <button
        type="button"
        onClick={handleDismiss}
        aria-label="Dismiss"
        className="shrink-0 text-muted-foreground hover:text-foreground"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  );
}
```

- [ ] **Step 2: Mount the nudge in `RenderWorkflow.tsx`**

Add the import near the other component imports at the top of `src/app/workflow/[workflowId]/RenderWorkflow.tsx`:

```tsx
import { HireExpertNudge } from "@/components/lead-forms/HireExpertNudge";
```

Then render it inside the top-level returned wrapper. The component returns (line 481–483):

```tsx
    return (
        <ReactFlowProvider>
            <div className="flex flex-col h-screen min-w-fit">
```

(If the outer wrapper is `<ReactFlowProvider>` or similar, place the nudge as a child of the outermost element.) Add the nudge as the first child inside the outermost `<div className="flex flex-col h-screen min-w-fit">`:

```tsx
            <div className="flex flex-col h-screen min-w-fit">
                <HireExpertNudge workflowId={workflowId} />
```

The nudge is `position: fixed`, so its DOM placement doesn't affect layout — putting it at the top of the wrapper is simplest. Note: `RenderWorkflow` only renders inside `AppLayout`'s `shouldShowSidebar` branch (the editor route `/workflow/<id>` matches `shouldShowSidebar`), so `LeadFormsProvider` is present in the tree.

- [ ] **Step 3: Verify type-check + lint**

Run: `npx tsc --noEmit && npm run fix-lint`
Expected: no errors. `useLeadForms` resolves because the builder lives under the provider.

- [ ] **Step 4: Manual check (with a shortened timer)**

Temporarily set `SHOW_DELAY_MS = 5 * 1000` in `HireExpertNudge.tsx`. Open a workflow editor (`/workflow/<id>`), wait ~5s → the banner slides in bottom-right. Click it → Hire modal opens, banner gone. Reload → it does NOT reappear (localStorage). Clear `localStorage` key, reload, let it auto-fade after 30s → reload → it CAN reappear (auto-expiry didn't consume). **Restore `SHOW_DELAY_MS = 5 * 60 * 1000` before committing.**

- [ ] **Step 5: Commit**

```bash
git add src/components/lead-forms/HireExpertNudge.tsx "src/app/workflow/[workflowId]/RenderWorkflow.tsx"
git commit -m "feat(lead-gen): delayed Hire-an-Expert nudge on the workflow builder"
```

---

## Task 14: Full verification + final dogfood

**Files:** none (verification only)

- [ ] **Step 1: Type-check + lint the whole UI**

Run: `npx tsc --noEmit && npm run fix-lint`
Expected: clean.

- [ ] **Step 2: Manual dogfood checklist** (dev server `npm run dev`, `http://localhost:3000`)

- [ ] Sidebar: section label reads **MANAGE** (not OBSERVE); contains Agent Runs, Reports, **Credits & Billing**.
- [ ] Sidebar footer: **Hire an Expert** button (person icon) right of the avatar; text visible when expanded; collapse the sidebar → button shrinks to icon-only with tooltip. Clicking opens the Hire modal.
- [ ] `/billing`: page titled **Credits & Billing**, renders the credits card with the **Running low?** footer; **Request top-up** and **Hire an Expert** both work.
- [ ] `/usage` (Agent Runs): credits card is **gone**; runs table + filters still work.
- [ ] Top-up modal: choosing **>20k** reveals Work email + Company + Company size + the dashed enterprise link; choosing a lower volume hides them. Personal email (e.g. `x@gmail.com`) is rejected with "Please use your work email". Wrong captcha blocks submit. Successful submit shows a toast and closes.
- [ ] Hire modal: all fields present; dashed enterprise link at the bottom opens the Enterprise modal.
- [ ] Enterprise modal: opens from both the top-up and hire dashed links; personal email rejected; submits.
- [ ] Builder nudge (use shortened timer to verify): appears, auto-fades, respects once-per-workflow, fires events.
- [ ] PostHog: confirm `hire_expert_opened`, `topup_request_opened`, `enterprise_lead_opened`, the `*_submitted`/`*_requested`, and `hire_nudge_*` events fire with `source`/`workflowId` props (PostHog debug / network tab, or the PostHog activity view).

- [ ] **Step 3: Final commit (if lint made any changes)**

```bash
git add -A
git commit -m "chore(lead-gen): lint + final verification pass"
```

---

## Self-review notes (addressed)

- **Spec coverage:** sidebar rename + link (T9), footer button (T9), credits move to /billing (T10–T12), 2 CTAs + helper text + PostHog (T10), top-up form + >20k gate + 3 qualifiers (T6), enterprise link from top-up & hire (T5/T6 → T4), hire form all fields (T5), enterprise form (T4), builder nudge 5min/30s/once-per-workflow/auto-expiry-not-consumed (T13), captcha (T3), personal-email blocklist (T2), submit→PostHog-now-with-submitLead-seam (T2), all PostHog events (T1 + per component). ✓
- **Min-diff/reuse:** credits card extracted not rewritten; sidebar = data + 2 button inserts; usage page = deletions; modals reuse existing shadcn primitives + established posthog pattern. ✓
- **Type consistency:** `LeadSource`/`LeadKind` defined in T2 used everywhere; `openHireExpert/openTopUp/openEnterprise` + `hasOpenedHireRef` defined in T7 used in T9/T10/T13; `validateWorkEmail` (T2) used in T4/T6; `submitLead({kind,source,payload})` signature consistent across T4/T5/T6. ✓
- **Known assumption to verify during T8/T13:** exact wrapper element of `RenderWorkflow`'s return and AppLayout indentation — the plan calls out the grep/visual check; the only functional requirement is the provider wrapping the trigger tree and the nudge being a child under it.
