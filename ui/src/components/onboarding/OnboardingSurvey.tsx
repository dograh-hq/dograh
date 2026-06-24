"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
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

// First-run business/qualification survey. Persisted server-side under the org's
// ONBOARDING_PROFILE config so the answers are usable for segmentation, not just
// localStorage. Shown once (until completed/skipped). Calls go through the shared
// hey-api client (the generated SDK isn't regenerated locally).
const URL = "/api/v1/organizations/onboarding-profile";

type Field = { key: string; label: string; options: string[] };

const SELECTS: Field[] = [
  {
    key: "source",
    label: "How did you hear about us?",
    options: ["Google search", "Referral / word of mouth", "Social media", "WhatsApp", "Ads", "Other"],
  },
  {
    key: "business_type",
    label: "What's your business?",
    options: ["Real estate", "Lending / finance", "Insurance", "EdTech", "Healthcare", "E-commerce / D2C", "BPO / call center", "Other"],
  },
  {
    key: "role",
    label: "Your role",
    options: ["Founder / owner", "Sales", "Marketing", "Operations", "Other"],
  },
  {
    key: "monthly_revenue",
    label: "Monthly revenue",
    options: ["Under ₹5 lakh", "₹5–25 lakh", "₹25 lakh–₹1 crore", "₹1 crore+", "Prefer not to say"],
  },
  {
    key: "use_case",
    label: "What will you mainly use calls for?",
    options: ["Lead qualification", "Follow-ups / reminders", "Collections", "Customer support", "Sales outreach"],
  },
  {
    key: "monthly_call_volume",
    label: "Expected monthly call volume",
    options: ["Under 1,000", "1,000–10,000", "10,000–50,000", "50,000+"],
  },
];

export function OnboardingSurvey() {
  const { user, loading: authLoading } = useAuth();
  const [open, setOpen] = useState(false);
  const [company, setCompany] = useState("");
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const hasFetched = useRef(false);

  useEffect(() => {
    if (authLoading || !user || hasFetched.current) return;
    hasFetched.current = true;
    (async () => {
      try {
        const res = await client.get({ url: URL });
        const data = res.data as { completed?: boolean } | undefined;
        if (!data?.completed) setOpen(true);
      } catch {
        // If we can't tell, don't nag.
      }
    })();
  }, [authLoading, user]);

  async function persist(payload: Record<string, unknown>) {
    setSaving(true);
    try {
      const res = await client.put({ url: URL, body: payload });
      if (res.error) throw new Error("save_failed");
      setOpen(false);
      return true;
    } catch {
      toast.error("Couldn't save — please try again");
      return false;
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (await persist({ company, ...answers })) {
      toast.success("Thanks — you're all set!");
    }
  }

  if (!open) return null;

  return (
    <Dialog open={open} onOpenChange={() => { /* require Submit or Skip */ }}>
      <DialogContent
        className="max-h-[90vh] overflow-y-auto sm:max-w-lg"
        onInteractOutside={(e) => e.preventDefault()}
        onEscapeKeyDown={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle>Welcome — tell us a bit about you</DialogTitle>
          <DialogDescription>
            A few quick questions so we can tailor your experience. Takes 20 seconds.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="ob-company">Company / business name</Label>
            <Input
              id="ob-company"
              placeholder="Acme Realty"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
            />
          </div>

          {SELECTS.map((f) => (
            <div key={f.key} className="space-y-2">
              <Label htmlFor={`ob-${f.key}`}>{f.label}</Label>
              <Select
                value={answers[f.key] ?? ""}
                onValueChange={(v) => setAnswers((a) => ({ ...a, [f.key]: v }))}
              >
                <SelectTrigger id={`ob-${f.key}`}>
                  <SelectValue placeholder="Select one" />
                </SelectTrigger>
                <SelectContent>
                  {f.options.map((o) => (
                    <SelectItem key={o} value={o}>
                      {o}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ))}

          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="ghost"
              disabled={saving}
              onClick={() => persist({ skipped: true })}
            >
              Skip for now
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? "Saving..." : "Get started"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
